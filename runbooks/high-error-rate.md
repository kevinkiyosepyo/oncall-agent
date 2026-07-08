---
title: High Error Rate
summary: Elevated 5xx responses on one or more HTTP endpoints. Covers sudden error-rate spikes after deploys, dependency failures surfacing as 500s, and unhandled exceptions in request handlers.
applies_to: HighErrorRate, ErrorBudgetBurn
---

# High Error Rate

## Diagnose

1. Identify which endpoints are erroring and since when — compare against the
   deploy timeline. An error spike that starts within minutes of a deploy is a
   deploy until proven otherwise.
2. Pull a sample of error responses and read the actual exception. Do not
   guess from the status code alone.
3. Check whether errors are uniform across instances (code bug) or isolated to
   a subset (bad host, partial rollout).

## Mitigate

- If correlated with a deploy: roll back first, diagnose second.
- If a downstream dependency is failing: enable the endpoint's degraded mode
  or feature-flag the dependent path off.
- If load-related: scale out and enable rate limiting at the edge.

## Escalate

Page the service owner if error rate exceeds 25% for more than 10 minutes or
if checkout/payment paths are affected at any rate.
