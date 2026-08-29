#!/bin/bash
# Wraps ClickHouse's own stock entrypoint to apply this app's configurable
# retention TTL (aw-app.json config_schema.retention_days) to the tables
# that actually hold ingested telemetry, via a real ALTER TABLE ... MODIFY
# TTL — not a static value baked in at table-creation time, and not an
# external cron dropping partitions. ClickHouse's own TTL-driven background
# merge does the actual deletion later (ttl_only_drop_parts=1 on every
# table below, so it drops whole expired parts rather than rewriting them).
#
# Runs on every container start, same reasoning as container/otelcol/
# entrypoint.sh's migration step: the Tier-2 sidecar manifest has no
# command/entrypoint override, so "when the app's config is saved and the
# framework restarts this sidecar because its env changed" (see aw-workspace
# src/apps/routes.py _apply_runtime_config) is the only hook available, and
# re-applying the same TTL on an unrelated restart is a harmless no-op.
set -e

# ClickHouse's own entrypoint, backgrounded so this script gets a turn
# before the container's main process takes over the foreground.
/entrypoint.sh "$@" &
CH_PID=$!
trap 'kill -TERM "$CH_PID" 2>/dev/null' TERM INT

until clickhouse-client -q "SELECT 1" >/dev/null 2>&1; do
    sleep 2
done

RETENTION_DAYS="${AW_SIGNOZ_RETENTION_DAYS:-7}"
case "$RETENTION_DAYS" in
    ''|*[!0-9]*) RETENTION_DAYS=7 ;;  # config always sends a string; guard against garbage
esac

# Retries per table: on a FIRST install, this container can be ready before
# the otelcol sidecar's migration (container/otelcol/entrypoint.sh) has
# actually created these tables — "table doesn't exist" is retried exactly
# like otelcol retries "ClickHouse not up yet", so retention lands on first
# boot instead of needing a second, unrelated restart to take effect.
apply_ttl() {
    table="$1"
    expr="$2"
    attempt=0
    max_attempts=60
    until clickhouse-client -q "ALTER TABLE $table ON CLUSTER cluster MODIFY TTL $expr" 2>/tmp/aw_ttl_err; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge "$max_attempts" ]; then
            echo "aw-entrypoint: giving up setting TTL on $table after $attempt attempts: $(cat /tmp/aw_ttl_err)" >&2
            return 0  # non-fatal — a table we couldn't reach keeps its previous TTL, not zero TTL
        fi
        sleep 5
    done
    echo "aw-entrypoint: TTL set on $table -> $expr"
}

# Every table below already ships a SigNoz-default TTL (verified live:
# 15-30 days depending on table); this only replaces the interval, keeping
# each table's own time-source expression exactly as SigNoz defined it.
apply_ttl signoz_traces.signoz_index_v3        "toDateTime(timestamp) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_traces.signoz_spans           "toDateTime(timestamp) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_traces.signoz_error_index_v2  "toDateTime(timestamp) + toIntervalDay($RETENTION_DAYS)"
# logs_v2/logs_v2_resource ship with a dynamic _retention_days-per-row TTL
# (SigNoz's own retention-settings feature) — overridden here with a static
# one keyed to this app's config, per this app's explicit requirement to use
# ALTER TABLE MODIFY TTL. The 1800s grace window on logs_v2_resource is
# SigNoz's own buffer against clock skew between a log line's timestamp and
# when its resource row is considered "seen" — preserved untouched.
apply_ttl signoz_logs.logs_v2                  "toDateTime(timestamp / 1000000000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_logs.logs_v2_resource         "(toDateTime(seen_at_ts_bucket_start) + toIntervalDay($RETENTION_DAYS)) + toIntervalSecond(1800)"
apply_ttl signoz_metrics.samples_v2            "toDateTime(timestamp_ms / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.samples_v4            "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.samples_v4_agg_5m     "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.samples_v4_agg_30m    "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.time_series_v4        "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.time_series_v4_6hrs   "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.time_series_v4_1day   "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.time_series_v4_1week  "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"
apply_ttl signoz_metrics.exp_hist              "toDateTime(unix_milli / 1000) + toIntervalDay($RETENTION_DAYS)"

wait "$CH_PID"
