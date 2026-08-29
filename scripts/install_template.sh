#!/usr/bin/env bash
# Installs a trivial `template` command into the workspace's persistent bin dir
# (~/.aw-workspace/bin, on PATH, survives restarts). Idempotent — safe to
# re-run (on install, and on every reconcile pass after workspace
# recreation). The greeting word is configurable via AW_APP_TEMPLATE_GREETING
# (set by template_app/plugin.py from config_schema.greeting — config_visible:
# false in aw-app.json keeps it off the Settings UI, but it's still real,
# ctx.config-driven config).
#
# TEMPLATE: replace this with your app's real installer(s). Keep the shape —
# idempotent, no interactive prompts, writes only under AW_WORKSPACE_HOME —
# see aw-app-essentials/scripts/install_*.sh for real examples (apt package,
# single-binary download, corepack activation, git-clone install).
set -euo pipefail

GREETING="${AW_APP_TEMPLATE_GREETING:-Hello}"
TEMPLATE_VERSION="1.0.0"
AW_BIN_DIR="${AW_WORKSPACE_HOME:-$HOME/.aw-workspace}/bin"
mkdir -p "$AW_BIN_DIR"

# Migration: this CLI was called `hello` until 0.13.0. The uninstall journal
# only ever reverts the CURRENT name, so an install that just renames the
# command would strand the old shim on PATH forever. Drop it here, where the
# reconciler re-runs on every boot, rather than in uninstall.sh — a workspace
# that never uninstalls the app still needs the old name gone.
rm -f "$AW_BIN_DIR/hello"

# --version is what the framework's health check calls by default (the
# `verify` field in aw-app.json). A CLI that answers it lets `missing_system_clis`
# / `aw-workspace-cli doctor` tell "installed and working" from "the name
# happens to be on PATH" — which is the distinction a `command -v` check
# cannot make, and the reason a broken git once went unnoticed for months.
cat > "$AW_BIN_DIR/template" <<SCRIPT
#!/usr/bin/env bash
if [ "\${1:-}" = "--version" ]; then echo "template ${TEMPLATE_VERSION}"; exit 0; fi
echo "${GREETING}, \${1:-world}!"
SCRIPT
chmod +x "$AW_BIN_DIR/template"

"$AW_BIN_DIR/template" world
