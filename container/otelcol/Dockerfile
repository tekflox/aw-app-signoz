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

COPY entrypoint.sh /entrypoint.sh
COPY otel-collector-config.yaml /etc/otel-collector-config.yaml
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
