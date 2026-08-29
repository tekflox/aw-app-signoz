#!/bin/sh
# Migrate-then-serve, folded into the image because this Tier-2 sidecar has
# no command/entrypoint override in the manifest (see Dockerfile). Mirrors
# the monolith's docker-compose.signoz.yml exactly: a one-shot
# telemetry-migrator (migrate bootstrap / sync up / async up) ran once before
# the long-lived collector started. Here it runs on every container start
# instead of in a separate one-shot container — safe because ClickHouse
# schema migrations are idempotent (a re-applied migration is a no-op), and
# `restart: unless-stopped` (src/apps/containers.py) means a start IS the
# only hook available.
#
# ClickHouse takes a few seconds to accept connections after its own
# container starts; migrate retries on a plain connection-refused instead of
# giving up, since sidecar start order isn't sequenced by the runtime either.
set -e

retry() {
    attempt=0
    max_attempts=30
    until "$@"; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "entrypoint: giving up after $attempt attempts: $*" >&2
            return 1
        fi
        echo "entrypoint: $* failed (attempt $attempt/$max_attempts), retrying in 5s..." >&2
        sleep 5
    done
}

retry /signoz-otel-collector migrate bootstrap
retry /signoz-otel-collector migrate sync up
retry /signoz-otel-collector migrate async up

exec /signoz-otel-collector --config=/etc/otel-collector-config.yaml
