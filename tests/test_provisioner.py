"""Coverage for the `provisioner` sidecar (container/provisioner/provision.py)
and its manifest wiring.

`tests/validate_manifest.py` is the shared cross-app manifest validator and
must not be app-specific — this file covers what only THIS app's manifest
and provisioner logic need to prove, same pattern as
`test_root_account_config.py` and `test_query_mcp_sidecar.py`.

The retry/idempotency logic in provision.py is exercised against a fake HTTP
transport (a plain callable matching `http_json`'s signature) rather than a
live SigNoz/workspace — this is stdlib-only code specifically so that's
possible with no vendored HTTP client or mocking framework.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(APP_ROOT, "aw-app.json")

sys.path.insert(0, os.path.join(APP_ROOT, "container", "provisioner"))
import provision  # noqa: E402


@pytest.fixture
def manifest():
    return json.loads(open(MANIFEST_PATH).read())


@pytest.fixture
def provisioner_sidecar(manifest):
    sidecars = manifest["runtime"]["sidecars"]
    (sidecar,) = [s for s in sidecars if s["name"] == "provisioner"]
    return sidecar


# ── manifest wiring ──────────────────────────────────────────────────────


def test_provisioner_sidecar_env_carries_no_secrets_baked_in(provisioner_sidecar):
    # Risk #1 from the design: signoz_api_key/provision_status must never be
    # in ANY container's env — this sidecar writing a value that fed its own
    # env would recreate (and thus restart) itself forever.
    env_blob = json.dumps(provisioner_sidecar["env"])
    assert "signoz_api_key" not in env_blob
    assert "provision_status" not in env_blob


def test_provisioner_sidecar_gets_root_credentials_and_workspace_key(provisioner_sidecar):
    env = provisioner_sidecar["env"]
    assert env["SIGNOZ_ROOT_MANAGED"] == "${config.root_account_managed}"
    assert env["SIGNOZ_ROOT_EMAIL"] == "${config.root_email}"
    assert env["SIGNOZ_ROOT_PASSWORD"] == "${config.root_password}"
    assert env["AW_WORKSPACE_API_KEY"] == "${env.AW_WORKSPACE_API_KEY}"
    assert env["AW_SIGNOZ_BACKEND_URL"] == "http://aw-app-signoz-backend:8080"


def test_provisioner_sidecar_publishes_no_port(provisioner_sidecar):
    assert "port" not in provisioner_sidecar


def test_provisioner_sidecar_has_a_writable_status_volume(provisioner_sidecar):
    (volume,) = provisioner_sidecar["volumes"]
    assert volume["source"] == "$AW_APP_DATA/provision"
    assert volume["mode"] == "rw"


def test_nginx_container_mounts_the_same_status_volume_readonly(manifest):
    volumes = manifest["runtime"]["volumes"]
    (status_volume,) = [v for v in volumes if v["source"] == "$AW_APP_DATA/provision"]
    assert status_volume["mode"] == "ro"


def test_contributes_doctor_points_at_the_status_route(manifest):
    (entry,) = manifest["contributes"]["doctor"]
    assert entry["route"] == "/aw-provision/status.json"


def test_config_schema_has_a_provision_status_field(manifest):
    spec = manifest["config_schema"]["properties"]["provision_status"]
    assert spec["type"] == "string"
    assert spec["default"] == ""


def test_nginx_conf_serves_status_json_as_application_json_above_catch_all():
    conf = open(os.path.join(APP_ROOT, "container", "nginx", "nginx.conf")).read()
    status_pos = conf.index("location = /aw-provision/status.json")
    catch_all_pos = conf.index("location / {")
    assert status_pos < catch_all_pos, "the catch-all location must not shadow the status route"
    # The block between the two location markers must set the content type.
    block = conf[status_pos:catch_all_pos]
    assert "default_type application/json" in block


# ── provision.py logic, against a fake HTTP transport ───────────────────


class FakeTransport:
    """Records every call and answers from a script keyed by (method, path).

    `path` is matched against the END of the requested URL so tests don't
    need to repeat the base URLs — e.g. `"/api/v1/roles"` matches
    `http://signoz:8080/api/v1/roles`.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self._script: dict[tuple[str, str], object] = {}

    def on(self, method: str, path_suffix: str, status, body: dict | None = None):
        """Register a response. Pass a list of ``(status, body)`` tuples as
        ``status`` (leaving ``body`` unset) to script a SEQUENCE of answers
        for repeated calls to the same endpoint — consumed in order, the
        last one repeating once exhausted."""
        if body is None and isinstance(status, list):
            self._script[(method, path_suffix)] = list(status)
        else:
            self._script[(method, path_suffix)] = (status, body)
        return self

    def __call__(self, method, url, headers=None, body=None, timeout=10.0):
        self.calls.append((method, url, headers, body))
        for (m, suffix), response in self._script.items():
            if m == method and url.endswith(suffix):
                if isinstance(response, list):
                    # Multiple calls to the same endpoint: pop in order,
                    # repeat the last one once exhausted.
                    return response.pop(0) if len(response) > 1 else response[0]
                return response
        raise AssertionError(f"unscripted call: {method} {url}")

    def post_calls(self, path_suffix: str):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(path_suffix)]


def make_provisioner(transport, tmp_path, *, root_managed=True, current_config=None):
    transport.on("GET", "/api/apps/signoz/config", 200,
                 {"config": current_config if current_config is not None else {}})
    transport.on("POST", "/api/apps/signoz/config", 200, {})
    return provision.Provisioner(
        signoz_url="http://aw-app-signoz-backend:8080",
        workspace_url="http://workspace:9030",
        workspace_api_key="wk-key",
        app_slug="signoz",
        root_managed=root_managed,
        root_email="fredericowu@gmail.com" if root_managed else "",
        root_password="Sup3r$ecurePassw0rd!" if root_managed else "",
        http=transport,
        status_path=str(tmp_path / "status.json"),
    )


def test_ready_state_syncs_the_status_message_once(tmp_path):
    # Fresh install with a key already pasted by hand: provision_status has
    # never been written, so the first pass syncs it — the ONLY way the
    # "SigNoz API key provisioning status" field in Settings gets populated
    # for a key that already worked from day one.
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path, current_config={"signoz_api_key": "already-good"})
    transport.on("GET", "/api/v1/service_accounts/me", 200, {"data": {}})

    result = prov.run_once()

    assert result == {"ok": True, "state": "ready", "detail": ""}
    posts = transport.post_calls("/api/apps/signoz/config")
    assert len(posts) == 1
    assert posts[0][3]["config"] == {"provision_status": provision.STATE_MESSAGES["ready"]}
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "ready"


def test_ready_state_posts_nothing_once_status_already_synced(tmp_path):
    # Risk from the design: every save of this app reloads the MCP gateway
    # — a status heartbeat that posts unconditionally would reload it every
    # 15 minutes forever. Steady state must be silent.
    transport = FakeTransport()
    prov = make_provisioner(
        transport, tmp_path,
        current_config={"signoz_api_key": "already-good",
                        "provision_status": provision.STATE_MESSAGES["ready"]},
    )
    transport.on("GET", "/api/v1/service_accounts/me", 200, {"data": {}})

    result = prov.run_once()

    assert result == {"ok": True, "state": "ready", "detail": ""}
    assert transport.post_calls("/api/apps/signoz/config") == []


def test_manual_key_required_when_root_not_managed(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path, root_managed=False)

    result = prov.run_once()

    assert result["ok"] is False
    assert result["state"] == "manual-key-required"
    posts = transport.post_calls("/api/apps/signoz/config")
    assert len(posts) == 1
    assert posts[0][3]["config"] == {"provision_status": provision.STATE_MESSAGES["manual-key-required"]}


def test_manual_key_required_is_not_reposted_once_status_already_matches(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(
        transport, tmp_path, root_managed=False,
        current_config={"provision_status": provision.STATE_MESSAGES["manual-key-required"]},
    )

    prov.run_once()

    assert transport.post_calls("/api/apps/signoz/config") == []


def test_full_provision_flow_creates_service_account_and_key(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path)
    transport.on("POST", "/api/v2/sessions/email_password", 200,
                 {"data": {"accessToken": "jwt-token"}})
    transport.on("GET", "/api/v1/roles", 200,
                 {"data": [{"id": "role-1", "name": "signoz-admin"}]})
    transport.on("GET", "/api/v1/service_accounts", 200, {"data": []})
    transport.on("POST", "/api/v1/service_accounts", 201, {"data": {"id": "acct-1"}})
    transport.on("POST", "/api/v1/service_accounts/acct-1/roles", 200, {})
    transport.on("POST", "/api/v1/service_accounts/acct-1/keys", 201,
                 {"data": {"id": "key-1", "key": "new-pat-abc"}})
    transport.on("GET", "/api/v1/service_accounts/me", 200, {"data": {}})

    result = prov.run_once()

    assert result == {"ok": True, "state": "provisioned", "detail": ""}
    posts = transport.post_calls("/api/apps/signoz/config")
    # Bundled in ONE save, not two — each save reloads the MCP gateway.
    assert len(posts) == 1
    assert posts[0][3]["config"] == {
        "signoz_api_key": "new-pat-abc",
        "provision_status": provision.STATE_MESSAGES["provisioned"],
    }


def test_key_name_collision_is_revoked_then_recreated_not_orphaned(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path)
    transport.on("POST", "/api/v2/sessions/email_password", 200,
                 {"data": {"accessToken": "jwt-token"}})
    transport.on("GET", "/api/v1/roles", 200,
                 {"data": [{"id": "role-1", "name": "signoz-admin"}]})
    transport.on("GET", "/api/v1/service_accounts", 200, {"data": [{"id": "acct-1", "name": "aw-workspace-mcp"}]})
    transport.on("POST", "/api/v1/service_accounts/acct-1/roles", 200, {})
    transport.on("POST", "/api/v1/service_accounts/acct-1/keys", [
        (409, {"error": "api_key_already_exists"}),
        (201, {"data": {"id": "key-2", "key": "fresh-pat"}}),
    ])
    transport.on("GET", "/api/v1/service_accounts/acct-1/keys", 200,
                 {"data": [{"id": "key-1", "name": "aw-workspace-mcp"}]})
    transport.on("DELETE", "/api/v1/service_accounts/acct-1/keys/key-1", 200, {})
    transport.on("GET", "/api/v1/service_accounts/me", 200, {"data": {}})

    result = prov.run_once()

    assert result["ok"] is True
    assert any(c[0] == "DELETE" and c[1].endswith("/keys/key-1") for c in transport.calls)
    assert transport.post_calls("/api/apps/signoz/config")[0][3]["config"]["signoz_api_key"] == "fresh-pat"


def test_role_not_found_fails_legibly_not_with_an_indexerror(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path)
    transport.on("POST", "/api/v2/sessions/email_password", 200,
                 {"data": {"accessToken": "jwt-token"}})
    transport.on("GET", "/api/v1/roles", 200,
                 {"data": [{"id": "role-1", "name": "signoz-viewer"}]})

    result = prov.run_once()

    assert result["ok"] is False
    assert result["state"] == "provision-failed"
    assert "signoz-admin" in result["detail"]


def test_new_key_failing_validation_marks_provision_failed(tmp_path):
    transport = FakeTransport()
    prov = make_provisioner(transport, tmp_path)
    transport.on("POST", "/api/v2/sessions/email_password", 200,
                 {"data": {"accessToken": "jwt-token"}})
    transport.on("GET", "/api/v1/roles", 200,
                 {"data": [{"id": "role-1", "name": "signoz-admin"}]})
    transport.on("GET", "/api/v1/service_accounts", 200, {"data": []})
    transport.on("POST", "/api/v1/service_accounts", 201, {"data": {"id": "acct-1"}})
    transport.on("POST", "/api/v1/service_accounts/acct-1/roles", 200, {})
    transport.on("POST", "/api/v1/service_accounts/acct-1/keys", 201,
                 {"data": {"id": "key-1", "key": "bad-pat"}})
    transport.on("GET", "/api/v1/service_accounts/me", 401, {"error": "unauthorized"})

    result = prov.run_once()

    assert result["ok"] is False
    assert result["state"] == "provision-failed"
    posts = transport.post_calls("/api/apps/signoz/config")
    assert len(posts) == 1
    assert posts[0][3]["config"] == {"provision_status": provision.STATE_MESSAGES["provision-failed"]}


def test_workspace_unreachable_does_not_crash(tmp_path):
    def flaky_transport(method, url, headers=None, body=None, timeout=10.0):
        raise ConnectionError("connection refused")

    prov = provision.Provisioner(
        signoz_url="http://aw-app-signoz-backend:8080",
        workspace_url="http://workspace:9030",
        workspace_api_key="wk-key",
        app_slug="signoz",
        root_managed=True,
        root_email="fredericowu@gmail.com",
        root_password="Sup3r$ecurePassw0rd!",
        http=flaky_transport,
        status_path=str(tmp_path / "status.json"),
    )

    result = prov.run_once()

    assert result["ok"] is False
    assert result["state"] == "workspace-unreachable"


def test_env_bool_treats_absent_var_as_off(monkeypatch):
    monkeypatch.delenv("SIGNOZ_ROOT_MANAGED", raising=False)
    assert provision._env_bool("SIGNOZ_ROOT_MANAGED") is False
    monkeypatch.setenv("SIGNOZ_ROOT_MANAGED", "true")
    assert provision._env_bool("SIGNOZ_ROOT_MANAGED") is True
