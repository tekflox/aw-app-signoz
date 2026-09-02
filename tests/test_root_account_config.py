"""Coverage for the managed root-account feature (SIGNOZ_USER_ROOT_*).

`tests/validate_manifest.py` is the shared cross-app manifest validator and
must not be app-specific — this file covers what only THIS app's manifest
needs to prove: the four env vars actually exist on the `backend` sidecar,
`root_account_managed`'s default/enum are the exact empty-string-enum shape
the safety property below depends on, and — the one that actually matters —
that `src.apps.containers.expand_env` drops all four vars when the config is
empty/default and emits all four when it is filled. `root_account_managed`
being a string enum (not a boolean) is load-bearing: `expand_value` only
drops a placeholder on an empty *string*, so a boolean `False` would resolve
to the literal `"False"` and get passed through, silently turning root
management on. This asserts that behavior directly rather than assuming it.

Needs an aw-workspace host checkout (`src/apps/containers.py`) alongside
this app repo — skipped cleanly in this app's own standalone CI, which
checks out only this repo. Run from within an aw-workspace checkout, e.g.
`cd /opt/aw-workspace && .venv/aw/bin/python -m pytest repos/aw-app-signoz/tests/`.
"""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip(
    "src.apps.containers",
    reason="requires an aw-workspace host checkout (src/apps) alongside this app repo",
)

from src.apps.containers import expand_env

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(APP_ROOT, "aw-app.json")

ROOT_ENV_VARS = {
    "SIGNOZ_USER_ROOT_ENABLED": "root_account_managed",
    "SIGNOZ_USER_ROOT_EMAIL": "root_email",
    "SIGNOZ_USER_ROOT_PASSWORD": "root_password",
    "SIGNOZ_USER_ROOT_ORG_ID": "root_org_id",
}


@pytest.fixture
def manifest():
    return json.loads(open(MANIFEST_PATH).read())


@pytest.fixture
def backend_sidecar(manifest):
    sidecars = manifest["runtime"]["sidecars"]
    (backend,) = [s for s in sidecars if s["name"] == "backend"]
    return backend


def test_backend_sidecar_declares_all_four_root_env_vars(backend_sidecar):
    env = backend_sidecar["env"]
    for env_var, config_key in ROOT_ENV_VARS.items():
        assert env.get(env_var) == f"${{config.{config_key}}}", (
            f"{env_var} missing or not wired to config.{config_key}"
        )
    # SIGNOZ_USER_ROOT_ORG_NAME is deliberately NOT exposed — SigNoz
    # defaults it to "default", and this app doesn't add a knob for it.
    assert "SIGNOZ_USER_ROOT_ORG_NAME" not in env


def test_root_account_managed_is_an_empty_string_default_enum(manifest):
    spec = manifest["config_schema"]["properties"]["root_account_managed"]
    assert spec["type"] == "string"
    assert spec["enum"] == ["", "true"]
    assert spec["default"] == ""


def test_expand_env_drops_all_four_vars_when_config_is_empty(backend_sidecar):
    env = backend_sidecar["env"]

    # Default/fresh-install config: root_account_managed at its schema
    # default ("") and no root_* fields ever saved.
    resolved = expand_env(env, {}, "signoz")
    for env_var in ROOT_ENV_VARS:
        assert env_var not in resolved, (
            f"{env_var} must be ABSENT when config is empty, not set to "
            f"an empty string — an absent var lets SigNoz's own signup "
            f"flow keep working; a present-but-empty one does not."
        )

    # Explicitly saved-but-blank config (the literal default value) must
    # behave identically to a config that was never touched.
    resolved = expand_env(env, {"root_account_managed": ""}, "signoz")
    for env_var in ROOT_ENV_VARS:
        assert env_var not in resolved


def test_expand_env_emits_all_four_vars_when_configured(backend_sidecar):
    env = backend_sidecar["env"]
    config = {
        "root_account_managed": "true",
        "root_email": "fredericowu@gmail.com",
        "root_password": "Sup3r$ecurePassw0rd!",
        "root_org_id": "01a053ad-d16f-79bd-9b2f-1f9bde1edda7",
    }
    resolved = expand_env(env, config, "signoz")
    assert resolved["SIGNOZ_USER_ROOT_ENABLED"] == "true"
    assert resolved["SIGNOZ_USER_ROOT_EMAIL"] == "fredericowu@gmail.com"
    assert resolved["SIGNOZ_USER_ROOT_PASSWORD"] == "Sup3r$ecurePassw0rd!"
    assert resolved["SIGNOZ_USER_ROOT_ORG_ID"] == "01a053ad-d16f-79bd-9b2f-1f9bde1edda7"


def test_root_password_pattern_matches_signoz_policy(manifest):
    import re

    spec = manifest["config_schema"]["properties"]["root_password"]
    pattern = re.compile(spec["pattern"])

    # Valid per SigNoz's IsPasswordValid: >=12 chars, lower+upper+digit+symbol.
    assert pattern.match("Sup3r$ecurePassw0rd!")
    # Too short.
    assert not pattern.match("Sh0rt!Aa")
    # Missing a symbol.
    assert not pattern.match("NoSymbolHere123")
    # Missing an uppercase letter.
    assert not pattern.match("no_upper_pass123!")
    assert spec["x-secret"] is True
    assert spec["minLength"] == 12
