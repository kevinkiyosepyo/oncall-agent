# Postmortem: Checkout 5xx Spike Caused by Unsafe Discount Code Lookup (KeyError: None)

- **Alert:** HighErrorRate (critical)
- **Service:** demo-shop
- **Fired:** 2026-07-02 23:15:00 UTC
- **Resolved:** 2026-07-02 23:15:57 UTC
- **Duration:** 0 minutes
- **Incident ID:** `e000bff0-de16-43f6-940f-5817f0993f02`

## Summary

POST /checkout on demo-shop returned 5xx for ~72.7% of requests, completely blocking customer purchases. The high-confidence root cause is commit 6c56805, which changed apply_discount to index DISCOUNTS[code] directly instead of using .get() with a default, so any request with a discount code missing from the table (including None) raised a KeyError and produced a 500. Impact was scoped to the /checkout endpoint only; GET /products remained healthy. The alert self-resolved in under a minute, and no rollback or manual mitigation is recorded in the timeline.

## Root cause

**Hypothesis (high confidence):** A code change (commit 6c56805) removed the safe fallback in apply_discount by replacing DISCOUNTS.get(code, NO_DISCOUNT) with direct dict indexing DISCOUNTS[code], causing a KeyError (and resulting 500 response) whenever a checkout request carried a discount_code not present in DISCOUNTS, including the default None value.

Suspect commit: `6c56805`

### Contributing factors

- Removal of a defensive .get() with default in favor of direct dict indexing, eliminating the safe path for missing/None discount codes
- No apparent validation or handling for discount codes not present in the DISCOUNTS table before this code path executes

## Impact

- **Severity:** sev1 · **Blast radius:** single_endpoint
- **Affected endpoints:** POST /checkout
- Checkout is completely broken—72.7% of checkout requests are failing due to a missing discount code in the database, blocking customer purchases.
- ~72.7% of checkout requests failing (baseline 0%)

## Timeline

- **23:15:01** — `alert_fired` — HighErrorRate alert fired after 5xx rate on /checkout exceeded 20% for 2 minutes.
- **23:15:02** — `analysis_started`
- **23:15:05** — `runbook_matched` — Automated triage matched the high-error-rate runbook with high confidence based on the alert pattern.
- **23:15:12** — `commit_identified` — Commit 6c56805 identified as the high-confidence culprit: it replaced a safe dict .get() lookup with direct indexing, causing KeyError on missing discount codes.
- **23:15:16** — `impact_estimated` — Impact assessed as sev1: ~72.7% of checkout requests failing, scoped to the single /checkout endpoint, but revenue-critical.
- **23:15:16** — `brief_posted`
- **23:15:57** — `alert_resolved` — Alert resolved roughly 55 seconds after firing; no rollback or mitigation action is recorded in the timeline explaining the resolution.

## Action items

- [ ] **p0** (prevent) Revert apply_discount to use DISCOUNTS.get(code, NO_DISCOUNT) instead of direct dict indexing, or add explicit handling for missing/None discount codes before lookup
- [ ] **p0** (prevent) Add a unit test asserting apply_discount returns NO_DISCOUNT (not an exception) when passed a discount_code absent from DISCOUNTS, including None
- [ ] **p1** (prevent) Add an integration test that a checkout request with no discount code succeeds with a 2xx response
- [ ] **p1** (detect) Investigate and document why the alert auto-resolved within ~55 seconds without a recorded rollback or mitigation step, to confirm whether the fix was deployed, traffic dropped, or the underlying data issue self-corrected
- [ ] **p2** (mitigate) Add validation at the checkout API boundary to reject or default unknown discount codes before they reach pricing logic, reducing blast radius of similar bugs

## Lessons learned

- Direct dictionary indexing on external/user-influenced lookup keys (like discount codes) is a recurring risk pattern; code review should flag removal of safe-default patterns (.get with default) on such lookups.
- The incident record has no evidence of an explicit mitigation or rollback action, yet the alert resolved quickly — timeline completeness for how/why an incident resolves should be captured for future postmortems.
- Fast, high-confidence automated triage (runbook match, commit identification, impact estimate all within ~15 seconds of firing) enabled rapid diagnosis; this workflow should be reinforced for similar endpoint-level error spikes.

_Runbook used: `runbooks/high-error-rate.md`_

---

<details><summary>LLM analysis runs (this incident)</summary>

| step | model | in | out | latency | ok |
|---|---|---|---|---|---|
| runbook_match | claude-haiku-4-5 | 1047 | 174 | 3784ms | ✅ |
| commit_analysis | claude-sonnet-5 | 5488 | 333 | 7079ms | ✅ |
| impact | claude-haiku-4-5 | 1420 | 247 | 3499ms | ✅ |
| postmortem | claude-sonnet-5 | 3766 | 1372 | 14860ms | ✅ |

</details>
