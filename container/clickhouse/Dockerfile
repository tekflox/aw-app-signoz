# Thin wrapper around the stock ClickHouse image: same reasoning as
# container/otelcol/Dockerfile — the Tier-2 sidecar manifest has no
# command/entrypoint override, so applying this app's configurable
# retention TTL on start had to move into the image instead of a
# docker-compose-style command override.
#
# Published by .github/workflows/build.yml as
# ghcr.io/tekflox/aw-app-signoz-clickhouse:latest.
FROM clickhouse/clickhouse-server:25.5.6

# Defensive, matching container/otelcol/Dockerfile's fix: don't assume the
# base image's default user can chmod a freshly COPY'd file.
USER root

COPY entrypoint.sh /aw-entrypoint.sh
RUN chmod +x /aw-entrypoint.sh

ENTRYPOINT ["/aw-entrypoint.sh"]
