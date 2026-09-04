"""Coverage for the signoz-mcp-server sidecar + its mcp.template.json.

`tests/validate_manifest.py` checks the manifest shape generically; this
locks the two decisions the design actually depends on:

1. The credential lives in the mcp.template.json HEADER, resolved from
   `${config.signoz_api_key}` — NOT in the sidecar's own env. A sidecar env
   is fixed at container creation and never sees a Settings save; the
   template is re-rendered (and the gateway reloaded) on every save. Putting
   the key in the sidecar env would mean pasting it in Settings never takes
   effect until the container is recreated.
2. `SIGNOZ_URL` points at the backend sidecar's INTERNAL hostname, not the
   public one — the public edge cuts a request at 30s and adds a proxy hop
   that has nothing to do on a machine-to-machine path.

See docs/architecture/aw-app-signoz.md and skills/aw-signoz/SKILL.md for the
full design and its known gaps (43 tools with no per-tool allowlist, the
docs indexer's egress to signoz.io, and the v0.135.0 floor on the 7
dashboard tools this app's pinned v0.128.0 backend doesn't meet).
"""
from __future__ import annotations

import json
import os

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(APP_ROOT, "aw-app.json")
MCP_TEMPLATE_PATH = os.path.join(APP_ROOT, "mcp.template.json")


@pytest.fixture
def manifest():
    return json.loads(open(MANIFEST_PATH).read())


@pytest.fixture
def mcp_sidecar(manifest):
    sidecars = manifest["runtime"]["sidecars"]
    (mcp,) = [s for s in sidecars if s["name"] == "mcp"]
    return mcp


def test_mcp_sidecar_talks_to_the_internal_backend_hostname(mcp_sidecar):
    assert mcp_sidecar["env"]["SIGNOZ_URL"] == "http://aw-app-signoz-backend:8080"
    assert mcp_sidecar["env"]["TRANSPORT_MODE"] == "http"


def test_mcp_sidecar_env_carries_no_api_key(mcp_sidecar):
    # The credential belongs in mcp.template.json's header, not here — see
    # module docstring point 1.
    env_blob = json.dumps(mcp_sidecar["env"])
    assert "signoz_api_key" not in env_blob
    assert "SIGNOZ_API_KEY" not in mcp_sidecar["env"]
    assert "SIGNOZ-API-KEY" not in mcp_sidecar["env"]


def test_mcp_sidecar_publishes_no_port(mcp_sidecar):
    # Only sibling containers on the app's own podman network may reach
    # :8000 — nothing here should expose it externally.
    assert "port" not in mcp_sidecar


def test_config_schema_declares_signoz_api_key_as_a_password(manifest):
    spec = manifest["config_schema"]["properties"]["signoz_api_key"]
    assert spec["type"] == "string"
    assert spec["format"] == "password"
    assert spec["default"] == ""


def test_mcp_template_points_at_the_mcp_sidecar_and_resolves_the_config_key():
    doc = json.loads(open(MCP_TEMPLATE_PATH).read())
    server = doc["mcpServers"]["signoz"]
    assert server["type"] == "http"
    assert server["url"] == "http://aw-app-signoz-mcp:8000/mcp"
    assert server["headers"]["SIGNOZ-API-KEY"] == "${config.signoz_api_key}"


def test_empty_key_disables_the_upstream_instead_of_401ing_every_call(tmp_path):
    pytest.importorskip(
        "src.apps.mcp_template",
        reason="requires an aw-workspace host checkout (src/apps) alongside this app repo",
    )
    import shutil

    from src.apps.mcp_template import output_path, render

    pkg = tmp_path / "signoz"
    pkg.mkdir()
    shutil.copy(MCP_TEMPLATE_PATH, pkg / "mcp.template.json")

    rendered = render(str(pkg), {}, "signoz")
    assert rendered["mcpServers"]["signoz"]["enabled"] is False

    rendered = render(str(pkg), {"signoz_api_key": "pat-abc123"}, "signoz")
    assert rendered["mcpServers"]["signoz"]["enabled"] is True
    assert rendered["mcpServers"]["signoz"]["headers"]["SIGNOZ-API-KEY"] == "pat-abc123"
    assert json.loads(open(output_path(str(pkg))).read()) == rendered
