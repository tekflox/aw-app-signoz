# Changelog

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
