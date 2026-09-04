#!/usr/bin/env python3
"""Auto-provisions this app's own `signoz_api_key` from the managed root
account — no human step through SigNoz's Settings -> API Keys UI.

Long-lived poller (never exits — a one-shot container under this
framework's `restart: unless-stopped` policy restarts on ANY exit code,
turning a clean `exit 0` into a crash loop that leaks iptables rules; see
KB `crash-looping-container-leaks-iptables-rules`). Each pass:

  1. GET this app's own workspace config (X-Api-Key auth).
  2. If the stored `signoz_api_key` already validates, do nothing — this
     is the steady state, and it must never re-POST on a pass that changed
     nothing (every save of this app reloads the MCP gateway).
  3. Otherwise, if the managed root account isn't configured, publish an
     explicit "manual-key-required" status instead of silently doing
     nothing — a workspace with `root_account_managed` off keeps working
     exactly like before (paste a key by hand), it just isn't zero-touch.
  4. Otherwise, if `root_org_id` is blank, publish "org-id-required" —
     SigNoz's login API hard-requires an org id (see `_login`) and there is
     no unauthenticated way to discover one from scratch.
  5. Otherwise log in as root, ensure a `aw-workspace-mcp` service account
     with the `signoz-admin` role, mint a key (revoke-then-recreate on a
     name collision so no orphan keys accumulate), validate it, and POST
     it back — bundled with the human-readable status in one save.

SigNoz v0.128.0 has no `/api/v1/pats` (retired by sqlmigration 074) —
"Settings -> API Keys" is service accounts now. All responses are
enveloped `{"status": "...", "data": ...}`.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

LOG = logging.getLogger("provisioner")

SERVICE_ACCOUNT_NAME = "aw-workspace-mcp"
ROLE_NAME = "signoz-admin"

# "5s while not yet ready ... ~15min once ready" (Architect design) — a cold
# SigNoz backend takes ~30-60s to start accepting logins, so the short
# interval also covers that startup window without a separate readiness wait.
RETRY_POLL_S = 5
SETTLED_POLL_S = 15 * 60

DEFAULT_STATUS_PATH = "/var/lib/aw-provision/status.json"

# Fixed, canonical strings only — never interpolate a raw exception into a
# value that gets POSTed to the workspace config. That value is compared
# against what's already stored to decide whether to POST at all; a message
# that changes on every retry (e.g. a raw connection-error string) would
# make every single pass look "changed" and reload the MCP gateway forever.
STATE_MESSAGES = {
    "ready": "SigNoz API key is valid and active.",
    "provisioned": "SigNoz API key created automatically and is now active.",
    "manual-key-required": (
        "Automatic key creation needs the managed root account. Turn on "
        "'Manage the root account from these settings', or paste a SigNoz "
        "API key below."
    ),
    "org-id-required": (
        "Automatic key creation needs 'Root organization ID' filled in too "
        "— SigNoz's login API requires it. Look up the real org ID (see "
        "that field's own description) and paste it in, or paste a SigNoz "
        "API key below instead."
    ),
    "provision-failed": (
        "Could not create a SigNoz API key automatically — check this "
        "app's provisioner container logs."
    ),
}


def http_json(method: str, url: str, headers: dict | None = None,
              body: dict | None = None, timeout: float = 10.0):
    """Minimal stdlib HTTP-JSON client. Returns ``(status_code, parsed_body)``.

    Never raises for an HTTP error status — callers branch on the status
    code, same as they would with any other HTTP client. Only a transport
    failure (DNS, connection refused, timeout) raises ``ConnectionError``.
    """
    data = json.dumps(body).encode() if body is not None else None
    req_headers = dict(headers or {})
    if data is not None:
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", "replace")}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc)) from exc


class Provisioner:
    def __init__(self, *, signoz_url: str, workspace_url: str, workspace_api_key: str,
                 app_slug: str, root_managed: bool, root_email: str, root_password: str,
                 http=http_json, status_path: str = DEFAULT_STATUS_PATH):
        self.signoz_url = signoz_url.rstrip("/")
        self.workspace_url = workspace_url.rstrip("/")
        self.workspace_api_key = workspace_api_key
        self.app_slug = app_slug
        self.root_managed = root_managed
        self.root_email = root_email
        self.root_password = root_password
        self.http = http
        self.status_path = status_path

    # -- local status file (doctor + nginx-served /aw-provision/status.json) --

    def _write_local_status(self, ok: bool, state: str, detail: str = "") -> dict:
        payload = {"ok": ok, "state": state, "detail": detail}
        directory = os.path.dirname(self.status_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.status_path}.tmp"
        with open(tmp_path, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp_path, self.status_path)
        return payload

    # -- workspace config (this app's own /api/apps/<slug>/config) --

    def _get_workspace_config(self) -> dict:
        status, body = self.http(
            "GET", f"{self.workspace_url}/api/apps/{self.app_slug}/config",
            headers={"X-Api-Key": self.workspace_api_key},
        )
        if status != 200:
            raise ConnectionError(f"GET config failed: HTTP {status} {body}")
        return body.get("config") or {}

    def _post_workspace_config(self, partial: dict) -> None:
        status, body = self.http(
            "POST", f"{self.workspace_url}/api/apps/{self.app_slug}/config",
            headers={"X-Api-Key": self.workspace_api_key}, body={"config": partial},
        )
        if status != 200:
            raise ConnectionError(f"POST config failed: HTTP {status} {body}")

    def _sync_status_message(self, config: dict, state: str) -> None:
        message = STATE_MESSAGES[state]
        if config.get("provision_status") == message:
            return
        try:
            self._post_workspace_config({"provision_status": message})
        except Exception:                                      # noqa: BLE001
            LOG.warning("could not sync provision_status to workspace config", exc_info=True)

    # -- SigNoz's own API (v0.128.0 — no /api/v1/pats, service accounts instead) --

    def _validate_key(self, key: str) -> bool:
        try:
            status, _ = self.http(
                "GET", f"{self.signoz_url}/api/v1/service_accounts/me",
                headers={"SIGNOZ-API-KEY": key},
            )
        except Exception:                                      # noqa: BLE001
            return False
        return status == 200

    def _login(self, org_id: str) -> str:
        # v0.128.0's PostableEmailPasswordSession.UnmarshalJSON hard-rejects
        # a zero orgId ("orgID is required") — confirmed against a real
        # instance, not in the original design. There is no unauthenticated
        # "list orgs" endpoint to discover it from scratch, so this app's own
        # `root_org_id` config field (already documented for the managed-
        # root-account feature) doubles as the login credential's org scope.
        status, body = self.http(
            "POST", f"{self.signoz_url}/api/v2/sessions/email_password",
            body={"email": self.root_email, "password": self.root_password, "orgId": org_id},
        )
        if status != 200:
            raise RuntimeError(f"login failed: HTTP {status} {body}")
        token = (body.get("data") or {}).get("accessToken")
        if not token:
            raise RuntimeError(f"login response carried no accessToken: {body}")
        return token

    def _auth_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _find_role_id(self, token: str) -> str:
        status, body = self.http(
            "GET", f"{self.signoz_url}/api/v1/roles", headers=self._auth_headers(token),
        )
        if status != 200:
            raise RuntimeError(f"GET roles failed: HTTP {status} {body}")
        data = body.get("data")
        roles = data.get("roles") or data.get("items") or [] if isinstance(data, dict) else (data or [])
        for role in roles:
            if role.get("name") == ROLE_NAME or role.get("displayName") == ROLE_NAME:
                role_id = role.get("id")
                if role_id:
                    return role_id
        raise RuntimeError(f"role {ROLE_NAME!r} not found among {roles!r}")

    def _find_service_account_id(self, token: str) -> str | None:
        status, body = self.http(
            "GET", f"{self.signoz_url}/api/v1/service_accounts", headers=self._auth_headers(token),
        )
        if status != 200:
            raise RuntimeError(f"GET service_accounts failed: HTTP {status} {body}")
        for account in body.get("data") or []:
            if account.get("name") == SERVICE_ACCOUNT_NAME:
                return account.get("id")
        return None

    def _create_service_account(self, token: str) -> str:
        status, body = self.http(
            "POST", f"{self.signoz_url}/api/v1/service_accounts",
            headers=self._auth_headers(token), body={"name": SERVICE_ACCOUNT_NAME},
        )
        if status != 201:
            raise RuntimeError(f"POST service_accounts failed: HTTP {status} {body}")
        account_id = (body.get("data") or {}).get("id")
        if not account_id:
            raise RuntimeError(f"service_account creation carried no id: {body}")
        return account_id

    def _grant_role(self, token: str, account_id: str, role_id: str) -> None:
        status, body = self.http(
            "POST", f"{self.signoz_url}/api/v1/service_accounts/{account_id}/roles",
            headers=self._auth_headers(token), body={"id": role_id},
        )
        if status not in (200, 201, 204):
            raise RuntimeError(f"role grant failed: HTTP {status} {body}")

    def _list_keys(self, token: str, account_id: str) -> list[dict]:
        status, body = self.http(
            "GET", f"{self.signoz_url}/api/v1/service_accounts/{account_id}/keys",
            headers=self._auth_headers(token),
        )
        if status != 200:
            raise RuntimeError(f"GET keys failed: HTTP {status} {body}")
        return body.get("data") or []

    def _delete_key(self, token: str, account_id: str, key_id: str) -> None:
        status, body = self.http(
            "DELETE", f"{self.signoz_url}/api/v1/service_accounts/{account_id}/keys/{key_id}",
            headers=self._auth_headers(token),
        )
        if status not in (200, 204):
            raise RuntimeError(f"key delete failed: HTTP {status} {body}")

    def _create_key(self, token: str, account_id: str) -> str:
        payload = {"name": SERVICE_ACCOUNT_NAME, "expiresAt": 0}
        status, body = self.http(
            "POST", f"{self.signoz_url}/api/v1/service_accounts/{account_id}/keys",
            headers=self._auth_headers(token), body=payload,
        )
        if status == 409:
            # We don't hold the previous secret (it's returned only once at
            # creation) — revoke the stale entry by name, not by guessing a
            # unique suffix, so no orphan keys accumulate across restarts.
            for existing in self._list_keys(token, account_id):
                if existing.get("name") == SERVICE_ACCOUNT_NAME:
                    self._delete_key(token, account_id, existing["id"])
            status, body = self.http(
                "POST", f"{self.signoz_url}/api/v1/service_accounts/{account_id}/keys",
                headers=self._auth_headers(token), body=payload,
            )
        if status != 201:
            raise RuntimeError(f"key create failed: HTTP {status} {body}")
        key = (body.get("data") or {}).get("key")
        if not key:
            raise RuntimeError(f"key creation carried no key: {body}")
        return key

    def _ensure_key(self, org_id: str) -> str:
        token = self._login(org_id)
        role_id = self._find_role_id(token)
        account_id = self._find_service_account_id(token)
        if account_id is None:
            account_id = self._create_service_account(token)
        self._grant_role(token, account_id, role_id)
        return self._create_key(token, account_id)

    # -- one provisioning pass --

    def run_once(self) -> dict:
        try:
            config = self._get_workspace_config()
        except Exception as exc:                                # noqa: BLE001
            return self._write_local_status(False, "workspace-unreachable", str(exc))

        current_key = config.get("signoz_api_key") or ""
        if current_key and self._validate_key(current_key):
            self._sync_status_message(config, "ready")
            return self._write_local_status(True, "ready")

        if not (self.root_managed and self.root_email and self.root_password):
            self._sync_status_message(config, "manual-key-required")
            return self._write_local_status(
                False, "manual-key-required", STATE_MESSAGES["manual-key-required"])

        org_id = config.get("root_org_id") or ""
        if not org_id:
            self._sync_status_message(config, "org-id-required")
            return self._write_local_status(
                False, "org-id-required", STATE_MESSAGES["org-id-required"])

        try:
            new_key = self._ensure_key(org_id)
            if not self._validate_key(new_key):
                raise RuntimeError("newly created key failed validation against /service_accounts/me")
        except Exception as exc:                                # noqa: BLE001
            self._sync_status_message(config, "provision-failed")
            return self._write_local_status(False, "provision-failed", str(exc))

        partial = {"signoz_api_key": new_key, "provision_status": STATE_MESSAGES["provisioned"]}
        try:
            self._post_workspace_config(partial)
        except Exception as exc:                                # noqa: BLE001
            return self._write_local_status(False, "workspace-unreachable", str(exc))
        return self._write_local_status(True, "provisioned")


def _env_bool(name: str) -> bool:
    # `expand_env` drops an unresolved `${config.x}` placeholder entirely
    # rather than passing an empty string — an absent var here means "off",
    # same as a present-but-empty one.
    return os.environ.get(name, "") == "true"


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s provisioner: %(message)s",
    )

    provisioner = Provisioner(
        signoz_url=os.environ.get("AW_SIGNOZ_BACKEND_URL", "http://aw-app-signoz-backend:8080"),
        workspace_url=f"http://{os.environ.get('AW_WORKSPACE_HOST', '')}:"
                      f"{os.environ.get('AW_WORKSPACE_PORT', '9030')}",
        workspace_api_key=os.environ.get("AW_WORKSPACE_API_KEY", ""),
        app_slug="signoz",
        root_managed=_env_bool("SIGNOZ_ROOT_MANAGED"),
        root_email=os.environ.get("SIGNOZ_ROOT_EMAIL", ""),
        root_password=os.environ.get("SIGNOZ_ROOT_PASSWORD", ""),
    )
    provisioner._write_local_status(False, "starting")

    while True:
        result = provisioner.run_once()
        LOG.info("pass complete: state=%s ok=%s", result["state"], result["ok"])
        time.sleep(SETTLED_POLL_S if result["ok"] else RETRY_POLL_S)


if __name__ == "__main__":
    main()
