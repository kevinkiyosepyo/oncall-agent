---
title: Payment Provider Outage
summary: Checkout failures caused by the third-party payment provider — timeouts or 5xx from the provider API, webhook delivery stalls, or elevated card-decline anomalies not explained by our own deploys.
applies_to: PaymentProviderErrors, CheckoutFailures
---

# Payment Provider Outage

## Diagnose

1. Check the provider's status page and our outbound call metrics to the
   provider (timeout rate, 5xx rate).
2. Confirm our own service is healthy: if only the payment call in the
   checkout path fails, the problem is downstream.
3. Verify recent config changes to API keys, endpoints, or webhook secrets —
   an "outage" that starts at config-change time is self-inflicted.

## Mitigate

- Enable the checkout degraded mode: queue orders for deferred capture
  instead of failing them.
- If a secondary provider is configured, fail over.
- Self-inflicted config change: revert it.

## Escalate

Open a support case with the provider and post in the incident channel; page
the payments owner if degraded mode is unavailable.
