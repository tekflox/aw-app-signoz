---
name: aw-signoz
description: Use this workspace's own SigNoz instance — the OTLP HTTP endpoint format, how to authenticate to it, and what each container is for. Use when instrumenting a workspace component to send traces/metrics/logs, or when debugging this app's containers.
---

# aw-signoz — this workspace's own observability stack

`aw-app-signoz` is a per-workspace install of [SigNoz](https://signoz.io/) —
traces, metrics and logs in one dashboard, backed by this app's own
ClickHouse. It is not a shared/central instance: every workspace that
installs this app gets its own isolated data.

## Sending telemetry to it (OTLP HTTP)

Point any OTel SDK's OTLP/HTTP exporter at:

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://signoz.app.<workspace>.workspace.<apex-domain>
OTEL_EXPORTER_OTLP_HEADERS=X-Api-Key=<this workspace's API key>
```

- The three standard OTLP/HTTP paths work as-is: `/v1/traces`, `/v1/metrics`,
  `/v1/logs`. Don't add `/api/apps/signoz` — the workspace's reverse proxy
  strips that prefix before the request reaches this app's container.
- **gRPC (4317) is not exposed externally** — only OTLP/HTTP. Internal to
  this app's own containers, the otel-collector sidecar still listens on
  4317 too, if a future need justifies routing it.
- Auth is the workspace's own shared API key (`X-Api-Key` header) — see
  `docs/app-workspace-api-auth.md` in this repo, or any app's copy of the
  same doc. This is not a SigNoz-specific credential; it's the same key any
  external caller uses to reach any `/api/apps/<slug>/...` route on this
  workspace. A component running *inside* this same workspace (e.g.
  aw-backend, mcp-gateway) reads it from `AW_WORKSPACE_API_KEY` — see that
  doc's "Two Ways an App Reads the Key" section — instead of hardcoding it.
- A missing or wrong key gets a `401` before the request ever reaches
  nginx/otel-collector, same as any other app route on this workspace.

## Opening the UI

The app's card in the Apps grid opens the SigNoz web UI directly (same
public hostname as above, any other path). **First visit, by default**:
SigNoz has no preset admin account — create one through its own signup
screen the first time anyone opens the UI; nobody ships a default password.

**Or set it from Settings instead.** Turning on "Manage the root account
from these settings" makes this app's own config (`root_account_managed`,
`root_email`, `root_password`, `root_org_id`) the source of the SigNoz root
account, via SigNoz's own upstream-supported root-account provisioner
(`pkg/modules/user`, `config.User.Root.*` / `SIGNOZ_USER_ROOT_*` env on the
`backend` sidecar) instead of its signup screen. This is a config block, not
an API — SigNoz deliberately closes every API password-reset path for the
root user (`user.ErrIfRoot()`), precisely so root stays config-managed. See
`docs/architecture/aw-app-signoz.md` "Managed root account" for the
mechanism, the `root_org_id` adoption trap on an already-bootstrapped
instance, and the crash-loop hazard from an invalid password.

## Containers (all sidecars of the `signoz` app id)

| Container | Image | Role |
|---|---|---|
| `aw-app-signoz` (main) | `nginx:alpine` | The only externally-reachable port. Path-routes OTLP paths to `otelcol`, everything else to `backend`. |
| `aw-app-signoz-backend` | `signoz/signoz:v0.128.0` | SigNoz's own web UI + query API. SQLite state (dashboards, alerts, the admin account) lives in `$AW_APP_DATA/signoz-sqlite`. |
| `aw-app-signoz-otelcol` | `ghcr.io/tekflox/aw-app-signoz-otelcol` (this repo's own thin wrapper, see `container/otelcol/`) | Accepts OTLP, writes to ClickHouse. The wrapper's `entrypoint.sh` runs ClickHouse schema migrations before serving — the Tier-2 sidecar manifest has no `command` override, so this had to move into the image. |
| `aw-app-signoz-clickhouse` | `ghcr.io/tekflox/aw-app-signoz-clickhouse` (this repo's own thin wrapper, see `container/clickhouse/`) | All ingested telemetry. `$AW_APP_DATA/clickhouse` is the durable volume — this is the data that matters most to not lose. The wrapper's `entrypoint.sh` applies the configurable retention TTL (below) on every start. |
| `aw-app-signoz-zookeeper` | `signoz/zookeeper:3.7.1` | ClickHouse's replicated-table-engine coordination store (used even for one node — SigNoz's schema always uses `ReplicatedMergeTree`). |

Sidecars resolve each other by container name on the shared podman network
(`aw-app-<app_id>-<sidecar-name>`) — see `aw-workspace` `src/apps/
containers.py` `register_sidecar` if you need the mechanism, not just the
names.

## Retention

`retention_days` in Settings (default **7**) caps how long traces/logs/
metrics stay in ClickHouse — a real `ALTER TABLE ... MODIFY TTL` applied to
already-stored data, not just newly-ingested data. Saving a new value
restarts the `clickhouse` sidecar (config-change auto-restart, same
mechanism any other `${config.x}`-fed sidecar env uses); its wrapper
re-applies the TTL to every table that holds ingested volume. ClickHouse's
own background merge does the actual deletion afterward — expect disk to
shrink over minutes-to-hours, not instantly. See
`docs/architecture/aw-app-signoz.md` "Retention" for the exact table list
and the two SigNoz-specific gotchas (a dynamic per-row TTL column on the
logs tables, and `MODIFY TTL` being a rule change, not an instant purge).

## Debugging

- UI unreachable / 502 from nginx: check `backend` first — it depends on
  `clickhouse` being reachable and will crash-loop until ClickHouse accepts
  connections (no cross-container health-check/depends_on exists in this
  framework yet, so this is `restart: unless-stopped` retrying, not a hang).
- No data showing up despite a 2xx from the OTLP endpoint: check `otelcol`'s
  logs for the migration retry loop in `entrypoint.sh` — it retries for
  2.5 minutes (30 attempts × 5s) before giving up, so a ClickHouse that's
  merely slow to start looks identical to one that's actually broken until
  that window closes.
- ClickHouse `ON CLUSTER` errors: check `container/clickhouse/cluster.xml`
  is actually mounted — SigNoz's own migrator always issues clustered DDL
  even for this one-node deployment.
