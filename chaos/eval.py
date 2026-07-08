#!/usr/bin/env python3
"""Accuracy eval: run every chaos scenario end to end and score the agent.

For each scenario, checks:
  - runbook:    the agent matched the expected runbook
  - culprit:    the agent identified the exact bad commit SHA it planted
  - postmortem: the resolve path produced a postmortem

Each run stages a genuinely fresh incident (new evidence repo, new commit
SHAs, fresh traffic), so this measures the agent, not a memorized answer.

Usage:
  python3 chaos/eval.py                # all scenarios, prints a table
  python3 chaos/eval.py high-latency   # a subset

Exit code 0 iff every check passes. Stdlib only.
"""

import argparse
import json
import sys
import time
import urllib.request

from inject import (
    LOG_FILE,
    SCENARIOS,
    baseline_traffic,
    build_payload,
    incident_traffic,
    post_alert,
    reset_faults,
    seed_repo,
    set_fault,
    _now_iso,
)

ANALYSIS_TIMEOUT_S = 150
POSTMORTEM_TIMEOUT_S = 150


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _wait_for_status(agent_api: str, incident_id: str, statuses: set[str], timeout_s: int) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        incident = _get(f"{agent_api}/incidents/{incident_id}")
        if incident["status"] in statuses:
            return incident
        time.sleep(3)
    return None


def run_one(name: str, args) -> dict:
    scenario = SCENARIOS[name]
    agent_api = args.url.rsplit("/webhook", 1)[0]

    # Clear any open incident for this fingerprint from earlier runs, then
    # stage the incident exactly the way inject.py does.
    post_alert(args.url, build_payload(name, "resolved", _now_iso(), _now_iso()))
    reset_faults(args.app_url)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")

    bad_sha = seed_repo(scenario)
    baseline_traffic(args.app_url)
    time.sleep(1.0)
    set_fault(args.app_url, scenario["fault"], True)
    starts_at = _now_iso()
    time.sleep(1)
    incident_traffic(name, args.app_url)

    fired = post_alert(args.url, build_payload(name, "firing", starts_at, None))
    incident_id = fired["results"][0]["incident_id"]
    assert fired["results"][0]["action"] == "created", "stale open incident survived pre-clean"

    result = {
        "scenario": name,
        "expected_runbook": scenario["expected_runbook"],
        "expected_culprit": bad_sha,
        "runbook_ok": False,
        "culprit_ok": False,
        "postmortem_ok": False,
        "matched_runbook": None,
        "suspect_sha": None,
    }

    incident = _wait_for_status(agent_api, incident_id, {"briefed"}, ANALYSIS_TIMEOUT_S)
    if incident is None:
        print(f"    TIMEOUT waiting for analysis of {name}", file=sys.stderr)
        return result
    result["matched_runbook"] = incident["matched_runbook"]
    result["suspect_sha"] = incident["suspect_commit_sha"]
    result["runbook_ok"] = incident["matched_runbook"] == scenario["expected_runbook"]
    result["culprit_ok"] = incident["suspect_commit_sha"] == bad_sha

    set_fault(args.app_url, scenario["fault"], False)
    post_alert(args.url, build_payload(name, "resolved", starts_at, _now_iso()))
    # "resolved" is set synchronously at ingest; only postmortem_complete
    # proves the postmortem pipeline finished.
    incident = _wait_for_status(
        agent_api, incident_id, {"postmortem_complete"}, POSTMORTEM_TIMEOUT_S
    )
    result["postmortem_ok"] = incident is not None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", choices=[[], *sorted(SCENARIOS)],
                        help="subset to run (default: all)")
    parser.add_argument("--url", default="http://localhost:8080/webhook/alert")
    parser.add_argument("--app-url", default="http://localhost:8081")
    args = parser.parse_args()
    names = args.scenarios or sorted(SCENARIOS)

    results = []
    for name in names:
        print(f"==> {name}")
        results.append(run_one(name, args))

    check = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    print()
    print(f"{'scenario':<26} {'runbook':<9} {'culprit':<9} {'postmortem':<10}")
    print("-" * 56)
    for r in results:
        print(
            f"{r['scenario']:<26} {check(r['runbook_ok']):<9} "
            f"{check(r['culprit_ok']):<9} {check(r['postmortem_ok']):<10}"
        )
        if not r["runbook_ok"]:
            print(f"    runbook: expected {r['expected_runbook']}, got {r['matched_runbook']}")
        if not r["culprit_ok"]:
            print(f"    culprit: expected {r['expected_culprit']}, got {r['suspect_sha']}")

    total = 3 * len(results)
    passed = sum(r["runbook_ok"] + r["culprit_ok"] + r["postmortem_ok"] for r in results)
    print("-" * 56)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
