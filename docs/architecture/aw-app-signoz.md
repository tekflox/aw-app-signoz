# aw-app-signoz — architecture notes

Per-workspace SigNoz, ported from the monolith's single central instance
(`repos/agentic-workspace/docker-compose.signoz.yml`,
`docs/knowledge_base/notes/signoz-observability-setup.md`) into a normal
Tier-2 marketplace app. Every workspace that installs this gets its own
isolated stack — there is no "central SigNoz for all tenants" anymore.

## Multi-container packaging: sidecars, not a merged single-process image

The originating Kanban card (feature:aw-app-signoz-per-workspace) offered two
options for packaging SigNoz + ClickHouse + Zookeeper + otel-collector:
"docker-compose interno" or "multi-processo" inside one container image. A
third option — not named in the card, discovered while reading the
framework's own Tier-2 code (`src/apps/containers.py`,
`src/apps/runtime.py` `_register_sidecars`) — turned out to already exist
and fit far better: **`runtime.sidecars`**, the same mechanism
`aw-app-crispal` uses for its MySQL + WordPress companions. Each of the four
components ships as its own container. Two run the unmodified official
upstream image (`signoz/zookeeper`, `signoz/signoz`); the other two
(`clickhouse`, `otelcol`) are thin custom wrappers — see below for why each
one needed a wrapper at all.
This was chosen over hand-rolling a single merged rootfs (copying ClickHouse,
a JRE for Zookeeper, and two Go binaries into one base image) because that
path has real, hard-to-verify compatibility risk (glibc/musl mismatches
across images built on different base distros) with no way to iteratively
test-build it before committing to an approach, whereas sidecars let each
component keep running in the exact environment its own maintainers ship and
test.

The main container is a stock `nginx:alpine` acting purely as this app's
front door (see below) — not SigNoz itself. It is the cheapest way to get a
single public port routing to multiple internal sidecars, and needs no
custom image at all, just a mounted `nginx.conf`.

## Why one custom image (`otelcol`) instead of zero

The monolith's compose overrides the otel-collector's `command` to chain
`migrate bootstrap && migrate sync up && migrate async up` before serving —
the ClickHouse schema migration has to run before the collector can write
anything. This app's Tier-2 sidecar manifest has **no `command`/`entrypoint`
override field** (`register_sidecar()` in `src/apps/containers.py` takes
only `image`/`port`/`env`/`volumes`/`resources`) — a real, deliberate gap in
the current framework (every other component here needs no override, so this
wasn't a design compromise made for this app specifically, just the one spot
where the framework's Tier-2 primitives ran out). Rather than wait on a
framework change, `container/otelcol/` wraps SigNoz's own otel-collector
image with a 4-line `Dockerfile` + an `entrypoint.sh` that runs the same
migrate-then-serve sequence, published as
`ghcr.io/tekflox/aw-app-signoz-otelcol`. Migrations are idempotent, so
running them on every container start (this framework's sidecars always
`restart: unless-stopped`, so "on every start" is the only hook available) is
safe rather than merely convenient.

## Retention: configurable, enforced with a real `ALTER TABLE ... MODIFY TTL`

Frederico's explicit ask (added after the initial build): a configurable
retention cap, defaulting to 7 days, that actually shrinks storage for data
already sitting in ClickHouse — not just a knob that only affects data
ingested after the change. `config_schema.retention_days` (default `7`)
feeds `AW_SIGNOZ_RETENTION_DAYS` into the `clickhouse` sidecar's env, which
is why that sidecar is now ALSO a custom wrapper
(`container/clickhouse/Dockerfile` + `entrypoint.sh`) instead of the stock
image: on every start, it waits for ClickHouse to accept connections, then
runs `ALTER TABLE <db>.<table> ON CLUSTER cluster MODIFY TTL <expr>` against
the tables that actually hold ingested volume (traces: `signoz_index_v3`,
`signoz_spans`, `signoz_error_index_v2`; logs: `logs_v2`,
`logs_v2_resource`; metrics: `samples_v2`, `samples_v4` and its `_agg_5m`/
`_agg_30m` rollups, `time_series_v4` and its `_6hrs`/`_1day`/`_1week`
rollups, `exp_hist`) — verified live against a real running instance (see
`inspect_ttl_precise.out` in the delivery notes) rather than guessed from
SigNoz's source.

Two things worth knowing:

- **`logs_v2`/`logs_v2_resource` ship with a *dynamic*, per-row
  `_retention_days` column** — SigNoz's own built-in retention-settings
  feature (its UI has a "Retention Period" control that presumably drives
  this column, not a static per-table TTL). This app overrides that with a
  static TTL keyed to `retention_days` instead, per Frederico's explicit
  ask for `ALTER TABLE ... MODIFY TTL`. The `_retention_days` column itself
  is harmless left unused — nothing else reads it once the TTL expression
  no longer references it.
- **`MODIFY TTL` changes the *rule*, not an instant deletion.** ClickHouse's
  own background merge process drops newly-expired parts on its own
  schedule (every table here already has `ttl_only_drop_parts = 1`, so it
  drops whole parts cheaply rather than rewriting them) — expect actual
  disk shrinkage over minutes-to-hours after a retention decrease, not
  immediately on save.

Config-save wiring reuses the SAME mechanism the OTLP/DSN fixes already
depend on: `src/apps/routes.py` `_apply_runtime_config` restarts a sidecar
whenever a `${config.x}` value baked into its `env` changes — so saving a
new `retention_days` in Settings recreates the `clickhouse` sidecar
automatically, and the wrapper's entrypoint re-applies TTL with the new
value. First-install ordering is handled the same way `otelcol`'s migration
retry works: `entrypoint.sh` retries each `ALTER TABLE` (5s backoff, 60
attempts per table) since these tables don't exist yet until the `otelcol`
sidecar's own migration creates them, and starting them in the "wrong"
order isn't sequenced by the framework either.

## Managed root account — config block, not an API

Frederico wanted a way to set/reset SigNoz's admin (root) account from this
app's own Settings, instead of relying on SigNoz's one-shot signup screen or
asking an agent to hand-provision the account again after every incident.

**Why a config block and not a direct DB write or an HTTP reset.** SigNoz
v0.128.0 ships a first-class, upstream-supported root-account provisioner:
`pkg/modules/user/config.go`'s `RootConfig{Enabled, Email, Password,
Org{ID, Name}}`, validated in `Config.Validate()`, reconciled by
`impluser/service.go`'s `service.Start()` loop (10s retry) at backend
startup — bootstrap (`CreateFirstUser`, org + root user + roles + all 254
authz tuples) when no matching org exists, promote-or-reset
(`createOrPromoteRootUser` / `updateExistingRootUser` → `PromoteToRoot`,
`UpdateEmail`, `setPassword`) otherwise. `setPassword` is a no-op when the
stored bcrypt hash already matches, and a real re-hash
(`bcrypt.GenerateFromPassword`, cost 10) when it doesn't — so saving the
same password twice is safe, and saving a new one actually rotates it.
A raw SQLite write was rejected outright: it would have to reimplement that
hashing scheme, org wiring and the 254 authz tuples by hand, all of which
the upstream reconciler already does correctly and re-validates on every
restart. An HTTP-based reset was rejected because SigNoz does not offer
one for the root user on purpose — both `GetOrCreateResetPasswordToken` and
`UpdatePasswordByResetPasswordToken` call `user.ErrIfRoot()`, closing every
API password path for root specifically because root is meant to be
config-managed. Verified live against this app's own instance: a bogus
reset token answers `reset_password_token_not_found`, and `/register`
(signup) answers `self-registration is disabled` once the config's `Root.
Enabled` is on (`pkg/query-service/app/http_handler.go` sets
`SetupCompleted = true` in that case, which is what disables the signup
screen).

**Wiring.** Four new `config_schema` fields feed four new env vars on the
`backend` sidecar via the koanf env mapping `pkg/config/envprovider/
provider.go` uses (prefix `SIGNOZ_`, `_` nests as `::`, `__` escapes a
literal underscore): `root_account_managed` → `SIGNOZ_USER_ROOT_ENABLED`,
`root_email` → `SIGNOZ_USER_ROOT_EMAIL`, `root_password` (`x-secret`) →
`SIGNOZ_USER_ROOT_PASSWORD`, `root_org_id` → `SIGNOZ_USER_ROOT_ORG_ID`.
`SIGNOZ_USER_ROOT_ORG_NAME` is deliberately not exposed — SigNoz defaults it
to `"default"`, and a knob nobody needs is worse than the default. Saving
these in Settings goes through the same `_apply_runtime_config` config-save
path that `jwt_secret`/`retention_days` already use — it restarts the
`backend` sidecar, whose reconcile loop then applies the change.

**Why `root_account_managed` is a string enum (`["", "true"]`), not a
boolean.** `src/apps/containers.py` `expand_value` drops an env var
entirely when the resolved config value is the empty *string* — `value is
not None and value != ""` — and only on that exact case. A boolean `false`
would resolve to the Python string `"False"` (truthy to koanf) and get
passed through as `SIGNOZ_USER_ROOT_ENABLED=False`, silently turning root
management on with an all-empty config the first time someone saved any
other setting on this form. The empty-string enum makes "off" and "field
never configured" the same absent-env-var state, so a fresh install and an
unmodified existing install both keep behaving exactly like before this
feature shipped.

**Nothing here validates the password before saving.** `root_password`
carries a `pattern` mirroring Go's `IsPasswordValid` (≥12 chars, at least
one lowercase/uppercase/digit/symbol from `` ~!@#$%^&*()_+`-={}|[]\:"<>?,./
``, ASCII-only where Go's check is Unicode-aware — strictly stricter, so it
can never accept something SigNoz would reject) — but it exists purely as
machine-readable spec and documentation. `_coerce_config` in `src/apps/
routes.py` only coerces booleans; `AppConfigBody.jsx` renders a bare
`<input>` with no `pattern`/`minLength` attribute wired up. Saving a
password that fails SigNoz's own `Config.Validate()` makes SigNoz's backend
refuse to start at all — a real crash loop under this framework's
`restart: unless-stopped`, and a crash-looping container here has
previously leaked hundreds of duplicate iptables rules (see the
`aw-autoskill-iptables-dedupe-crash-loop` skill). Recovery is to blank
`root_account_managed` back to `""` in Settings and save — that alone
un-sets the env var and lets the backend start again on its next restart.

**The `root_org_id` adoption trap.** `reconcile()` finds the org to attach
the root user to via `orgGetter.GetByIDOrName(cfg.Org.ID, cfg.Org.Name)`;
with `Org.ID` empty it falls back to `GetByName("default")`
(`implorganization/getter.go`). An org that was bootstrapped through
SigNoz's own signup screen before this feature existed does not necessarily
have that name — this app's own live instance has one org with `name =
NULL`. `GetByName("default")` will not match a `NULL` name, so leaving
`root_org_id` blank on an already-bootstrapped instance does not fail
loudly — it succeeds, but at `CreateFirstUser`, quietly creating a *second*,
brand-new, empty organization (dashboards, saved views and all ingested
telemetry are scoped per-org). The person configuring this would log in
successfully and see nothing they had before. There is no way for this app
to detect "this instance already has data under a differently-named org"
automatically, so the field's own description tells the operator to look up
the real org ID first rather than leave it blank by habit on an existing
instance.

**Exposure.** The password lands in `.aw-workspace/app-config/signoz.json`
(0600, under a 0700 directory — the same place every other app's config
secrets, like this app's own `jwt_secret`, already live) and in the
`backend` sidecar's container environment, visible to anything holding the
container socket. Same exposure class as `jwt_secret` above; nothing new is
introduced here, and the field's description says so rather than implying a
stronger guarantee.

## OTLP ingest exposure — the decision the next card depends on

The card asked this to reuse the existing per-app public-hostname mechanism
rather than invent new tunneling, and flagged "own port vs. path-routing" as
an expected ambiguous call to document rather than block on.

**What the platform actually offers today** (confirmed by reading
`src/apps/proxy.py`, `src/apps/containers.py`, and
`docs/knowledge_base/architecture/per-app-subdomain-routing-generic-model.md`
before deciding):

- Every app gets exactly **one** authenticated, publicly-hostnamed HTTP port
  — `runtime.port`, reverse-proxied at `/api/apps/<id>/*` behind
  `IdentityGuard` and externally at `https://<id>.app.<ws>.workspace.<apex>`.
  This is the *only* port with a hostname, TLS, and auth already wired up.
- `runtime.publish` (the mechanism `aw-app-call-agent` uses for its SIP port)
  gives a **second** port, but only as a raw `host:port` — no hostname, no
  TLS, no auth, and no Caddy/edge involvement at all. Using it for OTLP would
  mean inventing DNS + TLS + firewall exposure by hand, which is exactly the
  "don't reinvent tunneling" the card ruled out.

**Decision: path-route OTLP/HTTP on the single existing `runtime.port`,
using the SAME auth every other external caller of this workspace already
uses.** The app's own `nginx.conf` (`container/nginx/nginx.conf`) sends
`/v1/traces`, `/v1/metrics`, `/v1/logs` to the `otelcol` sidecar and
everything else to the `backend` (SigNoz UI) sidecar, all behind the one
port the framework already exposes with a hostname, TLS and `IdentityGuard`.
An external caller (this workspace's own aw-backend/mcp-gateway today; a
future cross-workspace caller like the AP-MT tenant-routing card tomorrow)
authenticates with `X-Api-Key: <this workspace's API key>` — the exact,
already-documented mechanism in `docs/app-workspace-api-auth.md` for any
external caller of any `/api/apps/<slug>/...` route. Nothing new was built
for auth; this app just uses what every other app-to-workspace call already
uses.

Trade-off, stated plainly: this makes the OTLP endpoint require a valid
workspace API key, unlike a "fully public, no-auth" OTLP collector some
setups expect. That's the intended shape here — an unauthenticated public
ingest port would let anyone who finds the hostname write arbitrary
telemetry into this workspace's ClickHouse. The contract for the next card:

```
POST https://signoz.app.<workspace>.workspace.<apex>/v1/traces   (or /v1/metrics, /v1/logs)
X-Api-Key: <that workspace's API key>
```

gRPC (4317) is not exposed through this path — only OTLP/HTTP. Nothing in
this card's scope needed gRPC, and multiplexing gRPC's HTTP/2 framing
through the same nginx path-routing as plain HTTP/1.1 REST wasn't worth the
added complexity for a v1.

### 2026-08-30 update: auth moved from `IdentityGuard` to nginx itself

The paragraph above assumed the app stays behind the workspace's
`IdentityGuard` (`auth_required: true`) for every path. That assumption
broke in practice: SigNoz has its own real login/session system, and the
workspace's identity gate — both `IdentityGuard` and aw-backend's own edge
membership check — intercepted SigNoz's own `Authorization: Bearer
<signoz-token>` calls and tried (and failed) to verify them as the
workspace's own identity JWT, which is what caused the SPA's post-login
`authz/check` to 401 and bounce back to `/login` (bug:
`signoz-login-authz-check-401-bounce`).

The fix for that bug is to run this app with `auth_required: false` /
`public: true`, so the workspace stops intercepting any request to this
app's hostname and SigNoz's own auth is the only gate on the UI/API surface
(`location /`). That, by itself, also turned off the *only* auth the OTLP
paths had — they have none of their own — which is what let an
unauthenticated `POST /v1/traces` from the open internet succeed (confirmed
live, same day). The two paths need genuinely different treatment and the
framework has no per-route `auth_required` (confirmed by reading
`src/apps/manifest.py` and `src/apps/runtime.py`'s `IdentityGuard` — the
flag is a single whole-app boolean, and the `Host(f"{app_id}.app.{{_:str}}")`
mount shares the exact same guard instance as the `/api/apps/<id>` mount, so
there is no path-level knob to flip there either).

So the `X-Api-Key` check described above moved from `IdentityGuard` into
`nginx.conf` itself, scoped to exactly the three OTLP `location` blocks —
`location /` is untouched and reaches the `backend` sidecar with no gate of
its own, same as any other request SigNoz would normally receive from the
open internet on a self-hosted install. The workspace API key's live value
is baked into the container's own generated nginx config at start time via
the stock `nginx:alpine` image's `envsubst`-on-templates mechanism (the
volume mount target is `/etc/nginx/templates/default.conf.template`, not
`conf.d/default.conf` directly) — see the comment block at the top of
`container/nginx/nginx.conf` for exactly how that works and its one caveat
(a regenerated workspace API key needs this app's container restarted to
take effect, same as `jwt_secret`/`retention_days` above).

## What's intentionally NOT ported from the monolith's config

The monolith's `otel-collector-config.yaml` also scraped Caddy's admin
metrics, host Postgres/Redis, `docker_stats` (via a `docker.sock` mount) and
every container's log files (via a `/hostfs` bind mount) — all monolith-
specific, assuming a shared network namespace with a particular sibling
container and host-level device access this Tier-2 app does not request.
This app's collector config (`container/otelcol/otel-collector-config.yaml`)
keeps only the OTLP receiver → ClickHouse exporters pipeline: this app's job
is to accept telemetry from already-instrumented components, not to
re-scrape the host it happens to run on.

Also not ported: the monolith's `histogramQuantile` ClickHouse UDF (used by
some percentile dashboard panels), downloaded at startup by a one-shot
`init-clickhouse` container. Skipped for v1 as a non-critical nice-to-have;
port it into `container/clickhouse/` as a mounted `user_scripts/` binary
later if a dashboard actually needs it.

## Known gaps

- No cross-container health-check/`depends_on` exists in this framework's
  Tier-2 primitives yet, so `backend` and `otelcol` both just retry against
  ClickHouse until it's ready (via `restart: unless-stopped` and, for
  `otelcol`, an explicit retry loop in `entrypoint.sh`) rather than waiting
  for a real readiness signal.
- No per-sidecar `ulimits`/`user` override exists either — the monolith's
  ClickHouse compose service set `ulimits.nofile: 262144` and its Zookeeper
  service ran as `user: root` to work around a bind-mount permission issue.
  Neither is possible to replicate here today; if either sidecar turns out
  to need it in practice, that's a framework gap to raise, not something
  this app's manifest can currently express.
