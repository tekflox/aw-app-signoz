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
- Lets agents query this instance directly through MCP tools — logs,
  traces, metrics, alerts, dashboards and saved views — instead of opening
  the UI or querying ClickHouse by hand. See `skills/aw-signoz/SKILL.md`
  "Querying via MCP tools" for the one-time setup and the known gaps.

## Why Use It

Use this when you want to see what your own workspace's apps and services
are actually doing — request traces, error logs, resource metrics — without
depending on a shared or external observability service. Each workspace's
data stays in that workspace.

## How To Use It

1. Install the app. Four containers come up alongside its own front door:
   ClickHouse (storage), Zookeeper (ClickHouse's coordination store), the
   SigNoz backend/UI, and an OTel Collector that accepts OTLP.
2. Open the SigNoz card from the Apps grid. By default, nothing is
   pre-provisioned — the first person to open it creates the admin account
   through SigNoz's own signup screen. Turn on "Manage the root account from
   these settings" in Settings if you'd rather set/reset the admin email and
   password yourself instead — see Configuration below.
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
- **Manage the root account from these settings** (Settings) — off by
  default, so a fresh install behaves exactly like before: SigNoz's own
  signup screen creates the first account. Turn it on to set the root
  account's email and password from this form instead of SigNoz's signup
  flow. While it's on:
  - This becomes the source of truth for that password — changing the
    password inside SigNoz's own UI is reverted back to this setting's
    value at the next backend restart.
  - SigNoz's own signup screen is disabled the moment it's on.
  - **Root organization ID (advanced)** — leave it blank on a fresh
    install. Only fill it in if this instance already had an organization
    created through SigNoz's own signup screen before you turned this on;
    set it to that organization's ID. Getting this wrong on an
    already-bootstrapped instance creates a second, empty organization
    instead of taking over the existing one — you'd log in successfully but
    see no dashboards, no saved views, none of your existing telemetry.
  - **Nothing in this form validates the password** before saving — the
    length/character-class rule is documented on the field, not enforced.
    A password that fails SigNoz's own policy makes its backend refuse to
    start (a crash loop) until you blank this switch and save again — that
    is the recovery step if this happens.
  - The password is stored the same way this app already stores its
    session signing secret: in this workspace's app-config store and in
    the container's own environment, not specially encrypted beyond that.
- **SigNoz API key** (Settings) — turns the MCP query tools on. This is a
  manual, one-time step: open this app's own SigNoz UI, go to
  **Settings → API Keys**, create a Personal Access Token there, then paste
  it into this field. It is a SigNoz-native credential, not this
  workspace's own API key. Leave it blank to keep the query tools off — see
  `skills/aw-signoz/SKILL.md` "Querying via MCP tools" for the full setup
  and the tools' known gaps (all-or-nothing allowlisting, dashboard tools
  needing a newer SigNoz than this app ships).

Everything else (dashboards, alerts) is configured inside the SigNoz UI
itself, not through this app's Settings — SigNoz already has its own
settings surface for that.
