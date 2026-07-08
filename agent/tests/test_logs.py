"""Log aggregation: window splitting at startsAt, percentiles, error samples."""

import json
from datetime import datetime, timedelta, timezone

from app.context.logs import aggregate_logs

T0 = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


def _record(ts: datetime, path="/checkout", method="POST", status=200, latency=5.0, error=None):
    return {
        "ts": ts.isoformat(),
        "method": method,
        "path": path,
        "status": status,
        "latency_ms": latency,
        "error": error,
    }


def _write(tmp_path, records):
    f = tmp_path / "access.log"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return str(f)


def test_window_split_is_exact_at_starts_at(tmp_path):
    records = [
        _record(T0 - timedelta(microseconds=1)),          # last baseline record
        _record(T0, status=500, error="KeyError: None"),  # first incident record
    ]
    result = aggregate_logs(_write(tmp_path, records), T0)

    assert result["baseline"]["total_requests"] == 1
    assert result["incident"]["total_requests"] == 1
    assert result["incident"]["total_errors"] == 1


def test_per_endpoint_rates_and_percentiles(tmp_path):
    records = [_record(T0 - timedelta(seconds=1), latency=2.0) for _ in range(10)]
    # incident: 8 errors + 2 ok on /checkout; latencies 1..10ms
    records += [
        _record(T0 + timedelta(seconds=i), status=500 if i < 8 else 200,
                latency=float(i + 1), error="boom" if i < 8 else None)
        for i in range(10)
    ]
    result = aggregate_logs(_write(tmp_path, records), T0)

    endpoint = result["incident"]["endpoints"]["POST /checkout"]
    assert endpoint["requests"] == 10
    assert endpoint["errors"] == 8
    assert endpoint["error_rate_pct"] == 80.0
    assert endpoint["p50_latency_ms"] == 5.0   # round(0.5*9)=4 (banker's) -> values[4]
    assert endpoint["p95_latency_ms"] == 10.0
    assert result["baseline"]["endpoints"]["POST /checkout"]["error_rate_pct"] == 0.0


def test_error_samples_dedupe_and_top_errors_count(tmp_path):
    records = [
        _record(T0 + timedelta(seconds=i), status=500, error=f"error-{i % 2}")
        for i in range(6)
    ]
    result = aggregate_logs(_write(tmp_path, records), T0)

    assert sorted(result["error_samples"]) == ["error-0", "error-1"]
    assert {e["error"]: e["count"] for e in result["top_errors"]} == {
        "error-0": 3,
        "error-1": 3,
    }


def test_malformed_lines_are_skipped(tmp_path):
    f = tmp_path / "access.log"
    f.write_text("not json\n" + json.dumps(_record(T0)) + "\n{\"ts\": \"nope\"}\n")
    result = aggregate_logs(str(f), T0)

    assert result["incident"]["total_requests"] == 1


def test_missing_file_returns_none(tmp_path):
    assert aggregate_logs(str(tmp_path / "absent.log"), T0) is None


def test_empty_incident_window_returns_none(tmp_path):
    records = [_record(T0 - timedelta(seconds=5))]  # baseline only
    assert aggregate_logs(_write(tmp_path, records), T0) is None
