---
title: Memory Pressure / OOM
summary: Container or process memory climbing toward its limit, OOM kills, or restart loops. Covers memory leaks, unbounded caches and queues, and oversized request payloads.
applies_to: MemoryPressure, OOMKilled, ContainerRestarting
---

# Memory Pressure / OOM

## Diagnose

1. Check whether memory grows steadily (leak / unbounded structure) or spikes
   with specific requests (oversized payloads, pathological inputs).
2. Correlate the growth onset with deploys — a leak that starts at deploy
   time is the deploy.
3. Check restart counts: an OOM-kill loop can masquerade as an error-rate or
   availability alert.

## Mitigate

- Leak from a recent change: roll back; restarts only buy time.
- Unbounded cache or queue: apply a size bound or TTL; restart to reclaim.
- Legitimate footprint growth: raise the memory limit and file follow-up work
  to right-size.

## Escalate

Page the service owner if instances are OOM-killing more than once per hour.
