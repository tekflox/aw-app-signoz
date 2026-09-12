#!/usr/bin/env python3
"""container/clickhouse/system-logs.xml — bounds ClickHouse's own log tables.

THE MEASUREMENT (2026-09-12): this app's ClickHouse held 19.83 GiB of system.*
tables against 98 MiB of the telemetry it exists to store. system.trace_log
alone was 13.65 GiB over 781,359,938 rows, and had grown past the point where
`SELECT count()` on it could run — it died with MEMORY_LIMIT_EXCEEDED.

None of those tables ships a TTL. entrypoint.sh's AW_SIGNOZ_RETENTION_DAYS
reaches signoz_traces/signoz_logs/signoz_metrics — the data this app ingests —
and nothing reached the ones ClickHouse writes about itself.

These tests assert the SHAPE of the config, not a live server: the live check
belongs in a container and was run once by hand against clickhouse-server
25.5.6 (disabled tables absent, kept tables carrying
`TTL event_date + toIntervalDay(3)`). What rots without a test is the config
drifting from the manifest that mounts it, or a table quietly moving from the
disabled list to neither list.

Run: python3 tests/test_clickhouse_system_logs.py
"""
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
XML = ROOT / "container" / "clickhouse" / "system-logs.xml"
MANIFEST = ROOT / "aw-app.json"

#: Disabled outright. Each one is here because its value on a single-node,
#: one-workspace deployment does not justify its bytes — see the XML.
DISABLED = {"trace_log", "text_log", "asynchronous_metric_log"}
#: Kept, because they are what you want when ClickHouse misbehaves: what ran,
#: what the server was doing, what the merges did.
KEPT = {"query_log", "metric_log", "part_log"}

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}\n       {detail}")


def main():
    print("system-logs.xml")
    root = ET.parse(XML).getroot()
    check("the root element is <clickhouse>", root.tag == "clickhouse", root.tag)

    by_name = {child.tag: child for child in root}

    for name in DISABLED:
        el = by_name.get(name)
        check(f"{name} is disabled",
              el is not None and el.get("remove") == "1",
              f"expected <{name} remove=\"1\"/>, got {el is not None and el.attrib}")
        check(f"{name} carries no TTL to contradict it",
              el is not None and el.find("ttl") is None,
              "a disabled table with a TTL says two things at once")

    for name in KEPT:
        el = by_name.get(name)
        if el is None:
            check(f"{name} is configured", False, "absent — it would keep NO ttl")
            continue
        check(f"{name} is not disabled", el.get("remove") is None, el.attrib)
        ttl = el.find("ttl")
        check(f"{name} has a TTL", ttl is not None and ttl.text,
              "no <ttl> — this is exactly how 19.83 GiB accumulated")
        if ttl is not None and ttl.text:
            check(f"{name} TTL deletes rather than moves", "DELETE" in ttl.text, ttl.text)
        drop = el.find("ttl_only_drop_parts")
        check(f"{name} drops whole parts", drop is not None and drop.text == "1",
              "without this, expiry rewrites every part row by row")

    check("every element is accounted for",
          set(by_name) == DISABLED | KEPT,
          f"unexpected: {sorted(set(by_name) - (DISABLED | KEPT))}")

    # The config only does anything if the manifest mounts it. A file added
    # here and forgotten there is a change that passes review and ships nothing.
    manifest = json.loads(MANIFEST.read_text())
    mounts = [
        v for svc in _services(manifest)
        for v in (svc.get("volumes") or [])
        if "system-logs.xml" in str(v.get("source", ""))
    ]
    check("the manifest mounts it", len(mounts) == 1, f"found {len(mounts)}")
    if mounts:
        m = mounts[0]
        check("mounted into ClickHouse's config.d",
              m.get("target") == "/etc/clickhouse-server/config.d/system-logs.xml",
              m.get("target"))
        check("mounted read-only", m.get("mode") == "ro", m.get("mode"))

    print(f"\n{len(failures)} failed")
    return 1 if failures else 0


def _services(manifest):
    """Every service dict in the manifest, whatever nesting it uses."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if "volumes" in node:
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(manifest)
    return out


if __name__ == "__main__":
    sys.exit(main())
