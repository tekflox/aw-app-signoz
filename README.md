# SigNoz

Self-hosted observability for your workspace — traces, metrics and logs in
one dashboard, backed by your own ClickHouse. Install it and this workspace
gets its own isolated instance; nothing is shared with other tenants.

## What It Does

- Opens a SigNoz web UI as a workspace window: search traces, browse logs,
  build dashboards, set up alerts.
- Exposes an OTLP/HTTP endpoint any already-instrumented service can send
  traces, metrics and logs to — this workspace's own components, or a
  service running anywhere else that can reach this workspace's public
  hostname.
- Keeps everything it ingests in a ClickHouse database that survives a
  container recreation (an app update, a workspace redeploy) — telemetry
  history isn't lost when the app restarts.
- Caps storage automatically: retention defaults to 7 days and is
  enforced by ClickHouse itself (a real TTL, not an external cleanup job),
  so disk usage doesn't grow unbounded. Raise it in Settings if you want a
  longer history.

## Why Use It

Use this when you want to see what your own workspace's apps and services
are actually doing — request traces, error logs, resource metrics — without
depending on a shared or external observability service. Each workspace's
data stays in that workspace.

## How To Use It

1. Install the app. Four containers come up alongside its own front door:
   ClickHouse (storage), Zookeeper (ClickHouse's coordination store), the
   SigNoz backend/UI, and an OTel Collector that accepts OTLP.
2. Open the SigNoz card from the Apps grid. The first person to open it
   creates the admin account through SigNoz's own signup screen — nothing is
   pre-provisioned, on purpose (no default password shipped).
3. Point anything you want to observe at this workspace's OTLP endpoint —
   see `skills/aw-signoz/SKILL.md` for the exact URL/auth format, or
   `docs/architecture/aw-app-signoz.md` for why it's shaped that way.

## What It Delivers

A working observability stack owned by this workspace: a UI to look at your
own telemetry, and an ingest endpoint any instrumented component — inside
this workspace or reachable from outside it — can send data to.

## Configuration

- **Session signing secret** (Settings) — rotates SigNoz's own login-session
  signing key. Ships with a default; change it before relying on this
  instance for anything sensitive.
- **Retention (days)** (Settings) — how long traces/logs/metrics are kept
  before ClickHouse drops them. Defaults to 7 days, intentionally short so
  storage doesn't grow unbounded; raise it if you need a longer history.
  Changing it actually shrinks storage for data already ingested, not just
  data ingested after the change.

Everything else (admin account, dashboards, alerts) is configured inside the
SigNoz UI itself, not through this app's Settings — SigNoz already has its
own settings surface for that.
