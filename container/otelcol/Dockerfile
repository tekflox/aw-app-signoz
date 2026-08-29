# Thin wrapper around SigNoz's own otel-collector image: the aw-workspace
# Tier-2 runtime has no `command`/`entrypoint` override for a sidecar
# container (src/apps/containers.py `register_sidecar` — image/port/env/
# volumes/resources only), so the migrate-then-serve startup sequence the
# monolith's docker-compose.signoz.yml expressed as a compose `command:`
# override has to be baked into the image instead.
#
# Published by .github/workflows/build.yml as
# ghcr.io/tekflox/aw-app-signoz-otelcol:latest.
FROM signoz/signoz-otel-collector:v0.144.5

# The upstream image's default USER can't chmod a COPY'd file it doesn't own
# (confirmed live: "Operation not permitted" building without this). Staying
# root for the rest of the build — and at runtime — is fine here: this is an
# internal sidecar container, and /signoz-otel-collector itself doesn't
# require a non-root uid to run.
USER root

COPY entrypoint.sh /entrypoint.sh
COPY otel-collector-config.yaml /etc/otel-collector-config.yaml
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
