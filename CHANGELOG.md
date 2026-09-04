# Changelog

## 0.8.0

Query MCP tools — a `mcp` sidecar running the official SigNoz MCP server
(`signoz/signoz-mcp-server:v0.14.0`, `github.com/SigNoz/signoz-mcp-server`),
talking Streamable HTTP natively (`TRANSPORT_MODE=http`) straight to
`aw-mcp-gateway`'s `HttpUpstream` — no bridge process. Ported from the
monolith's vendored stdio binary of the same upstream project. New
`mcp.template.json` (precedent: `apps/home-assistant/mcp.template.json`)
puts the credential in the `SIGNOZ-API-KEY` header, resolved from a new
`signoz_api_key` config field — a SigNoz-native Personal Access Token,
created manually via SigNoz's own Settings → API Keys, NOT this workspace's
own API key. `SIGNOZ_URL` points at the `backend` sidecar's internal
hostname to skip the public edge's 30s cut. See `docs/architecture/
aw-app-signoz.md` and `skills/aw-signoz/SKILL.md` "Querying via MCP tools"
for the full design and three known, deliberately-not-fixed gaps: the
gateway allowlist is per-upstream (all ~43 tools, including 12 mutants, on
or off together), the binary's docs indexer egresses to signoz.io with no
env to disable it (use `/livez` as the healthcheck, not `/readyz`), and the
7 dashboard tools need SigNoz ≥v0.135.0 while this app pins v0.128.0.

## 0.6.0

Settings UI to set/reset the SigNoz root account — `root_account_managed`,
`root_email`, `root_password`, `root_org_id` on the `backend` sidecar,
wired to SigNoz v0.128.0's own upstream root-account provisioner
(`SIGNOZ_USER_ROOT_*` env). Off by default, so an existing install's
behavior is unchanged until it's turned on. See
`docs/architecture/aw-app-signoz.md` "Managed root account" for the
mechanism, the `root_org_id` adoption trap, and the invalid-password
crash-loop hazard.

## 0.1.0

Initial release — SigNoz as a per-workspace Tier-2 app, ported from the
monolith's single central instance. See
`docs/architecture/aw-app-signoz.md` for the packaging and OTLP-exposure
decisions.
