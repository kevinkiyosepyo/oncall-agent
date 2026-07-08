---
title: High Latency
summary: p95/p99 request latency above SLO without a corresponding error spike. Covers slow database queries, missing indexes, N+1 query regressions, cold caches, and CPU saturation.
applies_to: HighLatencyP99, SlowRequests
---

# High Latency

## Diagnose

1. Determine whether latency is elevated across all endpoints (resource
   saturation) or one endpoint (code path regression).
2. Check database query timings — the most common cause is a new slow query
   or an N+1 pattern introduced by a recent change.
3. Inspect CPU and memory on the service instances; sustained CPU above 85%
   turns queuing delay into user-visible latency.

## Mitigate

- Recent deploy touching the slow endpoint: roll back.
- Slow query: add the missing index or revert the query change; as a stopgap,
  raise statement timeout so requests fail fast instead of piling up.
- Saturation: scale out; shed non-critical traffic.

## Escalate

Page the service owner if p99 exceeds 5s for 15 minutes or if latency is
causing upstream timeouts.
