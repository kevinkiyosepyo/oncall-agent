"""Aggregate the demo service's JSON access log for the impact step.

All counting happens here, in Python. The LLM receives aggregates plus a few
sample error lines and interprets them — it never counts raw log lines, so
its input stays small and its arithmetic stays trustworthy.

Windows are split at the alert's startsAt: baseline before, incident after.
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger("oncall-agent.context.logs")

MAX_ERROR_SAMPLES = 10


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return ordered[idx]


def _window_stats(records: list[dict]) -> dict:
    by_endpoint: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_endpoint[f"{r['method']} {r['path']}"].append(r)

    endpoints = {}
    for endpoint, rows in sorted(by_endpoint.items()):
        errors = [r for r in rows if r["status"] >= 500]
        latencies = [r["latency_ms"] for r in rows]
        endpoints[endpoint] = {
            "requests": len(rows),
            "errors": len(errors),
            "error_rate_pct": round(100 * len(errors) / len(rows), 1),
            "p50_latency_ms": round(_percentile(latencies, 0.50), 1),
            "p95_latency_ms": round(_percentile(latencies, 0.95), 1),
        }
    return {
        "total_requests": len(records),
        "total_errors": sum(1 for r in records if r["status"] >= 500),
        "endpoints": endpoints,
    }


def aggregate_logs(log_file: str, incident_start: datetime) -> dict | None:
    """Baseline-vs-incident aggregates, or None when no log data exists."""
    path = Path(log_file)
    if not path.exists():
        log.warning("no access log at %s", log_file)
        return None

    baseline: list[dict] = []
    incident: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            ts = datetime.fromisoformat(record["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        (incident if ts >= incident_start else baseline).append(record)

    if not incident:
        log.warning("no log records in the incident window (>= %s)", incident_start)
        return None

    error_counter: Counter[str] = Counter()
    samples: list[str] = []
    for r in incident:
        err = r.get("error")
        if r["status"] >= 500 and err:
            error_counter[err] += 1
            if err not in samples and len(samples) < MAX_ERROR_SAMPLES:
                samples.append(err)

    return {
        "incident_window_start": incident_start.isoformat(),
        "baseline": _window_stats(baseline) if baseline else None,
        "incident": _window_stats(incident),
        "top_errors": [
            {"error": e, "count": c} for e, c in error_counter.most_common(5)
        ],
        "error_samples": samples,
    }
