#!/usr/bin/env python3
"""Chaos scenario runner: stage a whole incident end to end.

For each scenario this script:
  1. re-seeds the evidence git repo (demo-app/repo) with a plausible history,
     including the scenario's bad commit — deliberately NOT the HEAD commit
  2. generates baseline traffic against the demo app
  3. toggles the matching runtime fault on and generates incident traffic
  4. fires an Alertmanager-format webhook at the on-call agent
  5. with --resolve-after N: waits, turns the fault off, sends the resolved
     payload (which triggers postmortem generation)

The running app's behavior (fault flag), the committed diff, and the error
strings in the logs all describe the same failure mechanism, so the agent
analyzes genuine, mutually consistent evidence.

Stdlib only — no dependencies to install on the host.

Usage:
  python3 chaos/inject.py high-error-rate --resolve-after 30
  python3 chaos/inject.py --list
  python3 chaos/inject.py reset            # turn all faults off
  python3 chaos/inject.py high-latency --alert-only   # fire alert, no demo app
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "demo-app" / "src"
REPO_DIR = ROOT / "demo-app" / "repo"
LOG_FILE = ROOT / "demo-app" / "logs" / "access.log"

# Business modules only — app.py stays out of the evidence repo because its
# docstring describes the fault-injection scaffolding, which would hint the
# negative-control answer to the agent.
SOURCE_FILES = ["shop.py", "pricing.py", "db.py", "payments.py", "README.md"]

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

SCENARIOS = {
    "high-error-rate": {
        "fault": "checkout_error",
        "expected_runbook": "high-error-rate.md",
        "labels": {
            "alertname": "HighErrorRate",
            "service": "demo-shop",
            "severity": "critical",
            "endpoint": "/checkout",
        },
        "annotations": {
            "summary": "Error rate on demo-shop /checkout above 20% for 2m",
            "description": "5xx responses on /checkout exceeded the 20% threshold",
        },
        "patch": {
            "file": "pricing.py",
            "replacements": [
                (
                    "    discount = DISCOUNTS.get(code, NO_DISCOUNT) if code else NO_DISCOUNT",
                    "    discount = DISCOUNTS[code]",
                )
            ],
        },
        "commit": {
            "message": "pricing: enforce strict discount code validation in apply_discount",
            "author": ("Priya Shah", "priya@demo.shop"),
        },
    },
    "high-latency": {
        "fault": "latency",
        "expected_runbook": "high-latency.md",
        "labels": {
            "alertname": "HighLatencyP99",
            "service": "demo-shop",
            "severity": "warning",
            "endpoint": "/products",
        },
        "annotations": {
            "summary": "p99 latency on demo-shop /products above 3s for 5m",
            "description": "p99 latency exceeded 3s; error rate is normal",
        },
        "patch": {
            "file": "shop.py",
            "replacements": [
                (
                    "from pricing import apply_discount",
                    "from pricing import apply_discount, fetch_live_price",
                ),
                (
                    "def list_products():\n    get_connection()\n    return PRODUCTS",
                    "def list_products():\n    get_connection()\n"
                    "    products = []\n"
                    "    for product in PRODUCTS:\n"
                    "        live = fetch_live_price(product[\"id\"])\n"
                    "        products.append({**product, \"price\": live or product[\"price\"]})\n"
                    "    return products",
                ),
            ],
        },
        "commit": {
            "message": "products: show live prices on the catalog page",
            "author": ("Marcus Webb", "marcus@demo.shop"),
        },
    },
    "db-pool-exhausted": {
        "fault": "db_pool",
        "expected_runbook": "db-connection-pool-exhausted.md",
        "labels": {
            "alertname": "DBConnectionPoolExhausted",
            "service": "demo-shop",
            "severity": "critical",
        },
        "annotations": {
            "summary": "demo-shop cannot obtain database connections",
            "description": "connection pool timeouts: QueuePool limit reached, requests failing fast",
        },
        "patch": {
            "file": "db.py",
            "replacements": [
                (
                    "# Connection pool sizing. Checkout bursts hold a dozen connections during\n"
                    "# flash sales, so keep this generous.\n"
                    "POOL_SIZE = 20\n"
                    "POOL_OVERFLOW = 10",
                    "# Connection pool sizing. Trimmed to cut idle connections on the shared\n"
                    "# Postgres instance (cost review, June 2026).\n"
                    "POOL_SIZE = 2\n"
                    "POOL_OVERFLOW = 0",
                )
            ],
        },
        "commit": {
            "message": "db: trim connection pool for shared postgres cost review",
            "author": ("Elena Rodriguez", "elena@demo.shop"),
        },
    },
    # Negative control: the incident has NO code-change cause. The evidence
    # repo gets only the innocuous baseline history — payments.py exists but
    # nothing in the commit window touched it. The correct analysis is
    # no_culprit_found=true; blaming any commit fails the eval.
    "payment-provider-outage": {
        "fault": "provider_timeout",
        "expected_runbook": "payment-provider-outage.md",
        "labels": {
            "alertname": "PaymentProviderErrors",
            "service": "demo-shop",
            "severity": "critical",
            "endpoint": "/checkout",
        },
        "annotations": {
            "summary": "Checkout failing: payment provider requests timing out",
            "description": "Outbound calls to the payment provider are timing out; /checkout returning 502",
        },
        "patch": None,
        "commit": None,
    },
}

# ---------------------------------------------------------------------------
# Evidence repo seeding
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, author: tuple[str, str] | None = None, when: datetime | None = None):
    cmd = ["git", "-C", str(repo)]
    if author:
        cmd += ["-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}"]
    cmd += list(args)
    env = dict(os.environ)
    if when:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)


def _commit_all(repo: Path, message: str, author: tuple[str, str], when: datetime):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, author=author, when=when)


def _head_sha(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def seed_repo(scenario: dict) -> str | None:
    """(Re)build the evidence repo; returns the bad commit's short sha, or
    None for negative-control scenarios that plant no bad commit."""
    canonical = {name: (SRC_DIR / name).read_text() for name in SOURCE_FILES}

    REPO_DIR.mkdir(parents=True, exist_ok=True)
    for child in REPO_DIR.iterdir():  # keep the dir itself: it's a bind mount
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    _git(REPO_DIR, "init", "-q", "-b", "main")
    now = datetime.now(timezone.utc).astimezone()

    # -- baseline history ---------------------------------------------------
    stage1 = dict(canonical)
    stage1["shop.py"] = canonical["shop.py"].replace(
        '    {"id": 5, "name": "brass reading lamp", "price": 89.0},\n'
        '    {"id": 6, "name": "felt laptop sleeve", "price": 27.0},\n',
        "",
    )
    stage1["README.md"] = "# demo-shop\n\nToy storefront API used as the incident-response demo target.\n"
    for name, content in stage1.items():
        (REPO_DIR / name).write_text(content)
    _commit_all(
        REPO_DIR, "initial import of demo-shop storefront service",
        ("Marcus Webb", "marcus@demo.shop"), now - timedelta(days=6),
    )

    (REPO_DIR / "shop.py").write_text(canonical["shop.py"])
    _commit_all(
        REPO_DIR, "catalog: add brass reading lamp and felt laptop sleeve",
        ("Elena Rodriguez", "elena@demo.shop"), now - timedelta(days=3),
    )

    (REPO_DIR / "README.md").write_text(canonical["README.md"])
    _commit_all(
        REPO_DIR, "docs: document storefront endpoints",
        ("Marcus Webb", "marcus@demo.shop"), now - timedelta(days=2),
    )

    # -- the bad commit (45 minutes ago); skipped for negative controls -----
    bad_sha = None
    patch = scenario["patch"]
    if patch is not None:
        content = canonical[patch["file"]]
        for old, new in patch["replacements"]:
            if old not in content:
                raise RuntimeError(f"patch anchor not found in {patch['file']} — src drifted")
            content = content.replace(old, new)
        (REPO_DIR / patch["file"]).write_text(content)
        _commit_all(
            REPO_DIR, scenario["commit"]["message"],
            scenario["commit"]["author"], now - timedelta(minutes=45),
        )
        bad_sha = _head_sha(REPO_DIR)

    # -- an innocuous commit after it, so the culprit is not HEAD -----------
    readme = (REPO_DIR / "README.md").read_text()
    (REPO_DIR / "README.md").write_text(
        readme + "\n## Ops\n\nSee the runbooks repo for incident procedures.\n"
    )
    _commit_all(
        REPO_DIR, "docs: link ops runbooks",
        ("Elena Rodriguez", "elena@demo.shop"), now - timedelta(minutes=18),
    )
    return bad_sha


# ---------------------------------------------------------------------------
# HTTP helpers, faults, traffic
# ---------------------------------------------------------------------------


def _req(url: str, method: str = "GET", body: dict | None = None, timeout: int = 25) -> int:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def set_fault(app_url: str, name: str, enabled: bool):
    status = _req(f"{app_url}/admin/fault", "POST", {"name": name, "enabled": enabled})
    if status != 200:
        raise RuntimeError(f"fault toggle {name}={enabled} returned {status}")


def reset_faults(app_url: str):
    for name in ("checkout_error", "latency", "db_pool", "provider_timeout"):
        set_fault(app_url, name, False)


def _run_traffic(requests: list[tuple[str, str, dict | None]], app_url: str, workers: int = 6):
    def one(spec):
        method, path, body = spec
        return _req(f"{app_url}{path}", method, body)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(one, requests))
    ok = sum(1 for s in statuses if s < 500)
    print(f"    {len(statuses)} requests, {ok} ok, {len(statuses) - ok} server errors")


def baseline_traffic(app_url: str):
    reqs: list[tuple[str, str, dict | None]] = []
    reqs += [("GET", "/products", None)] * 12
    reqs += [("GET", f"/products/{random.randint(1, 6)}", None) for _ in range(10)]
    reqs += [
        ("POST", "/checkout",
         {"product_id": random.randint(1, 6), "quantity": random.randint(1, 3),
          "discount_code": random.choice(["WELCOME10", "STAFF20"])})
        for _ in range(8)
    ]
    random.shuffle(reqs)
    _run_traffic(reqs, app_url)


def incident_traffic(scenario_name: str, app_url: str):
    reqs: list[tuple[str, str, dict | None]] = []
    if scenario_name == "high-error-rate":
        reqs += [
            ("POST", "/checkout",
             {"product_id": random.randint(1, 6), "quantity": 1, "discount_code": None})
            for _ in range(16)
        ]
        reqs += [
            ("POST", "/checkout",
             {"product_id": random.randint(1, 6), "quantity": 1, "discount_code": "WELCOME10"})
            for _ in range(6)
        ]
        reqs += [("GET", "/products", None)] * 8
    elif scenario_name == "high-latency":
        reqs += [("GET", "/products", None)] * 10  # ~3.3s each under the fault
        reqs += [("GET", f"/products/{random.randint(1, 6)}", None) for _ in range(6)]
    elif scenario_name == "db-pool-exhausted":
        reqs += [("GET", "/products", None)] * 10
        reqs += [
            ("POST", "/checkout",
             {"product_id": random.randint(1, 6), "quantity": 1, "discount_code": "WELCOME10"})
            for _ in range(8)
        ]
        reqs += [("GET", f"/products/{random.randint(1, 6)}", None) for _ in range(6)]
    elif scenario_name == "payment-provider-outage":
        reqs += [
            ("POST", "/checkout",
             {"product_id": random.randint(1, 6), "quantity": 1,
              "discount_code": random.choice([None, "WELCOME10"])})
            for _ in range(18)
        ]
        reqs += [("GET", "/products", None)] * 8
        reqs += [("GET", f"/products/{random.randint(1, 6)}", None) for _ in range(4)]
    random.shuffle(reqs)
    _run_traffic(reqs, app_url)


# ---------------------------------------------------------------------------
# Alert payloads
# ---------------------------------------------------------------------------


def _fingerprint(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _now_iso() -> str:
    # Microsecond precision matters: the agent splits its log windows at
    # startsAt, and a second-truncated timestamp can misfile baseline
    # records written in the same second as the fault toggle.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_payload(name: str, status: str, starts_at: str, ends_at: str | None) -> dict:
    scenario = SCENARIOS[name]
    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{scenario["labels"]["alertname"]}"}}',
        "status": status,
        "receiver": "oncall-agent",
        "alerts": [
            {
                "status": status,
                "labels": scenario["labels"],
                "annotations": scenario["annotations"],
                "startsAt": starts_at,
                "endsAt": ends_at or "0001-01-01T00:00:00Z",
                "fingerprint": _fingerprint(name),
            }
        ],
    }


def post_alert(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_scenario(name: str, args) -> int:
    scenario = SCENARIOS[name]

    if args.alert_only:
        starts_at = _now_iso()
        result = post_alert(args.url, build_payload(name, "firing", starts_at, None))
        print(f"fired {name} (alert-only): {json.dumps(result)}")
        if args.resolve_after is not None:
            time.sleep(args.resolve_after)
            result = post_alert(args.url, build_payload(name, "resolved", starts_at, _now_iso()))
            print(f"resolved {name}: {json.dumps(result)}")
        return 0

    # 0. preconditions
    try:
        _req(f"{args.app_url}/healthz", timeout=5)
    except urllib.error.URLError as e:
        print(f"demo app not reachable at {args.app_url}: {e}", file=sys.stderr)
        return 1
    reset_faults(args.app_url)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")  # clean windows for this run

    # 1. evidence repo
    print("==> seeding evidence repo")
    bad_sha = seed_repo(scenario)
    if bad_sha:
        print(f"    bad commit (buried in plausible history): {bad_sha} — "
              f"{scenario['commit']['message']!r}")
    else:
        print("    no bad commit planted (negative control — innocuous history only)")

    # 2. baseline traffic
    print("==> baseline traffic (healthy)")
    baseline_traffic(args.app_url)

    # 3. fault on + incident traffic (with buffers so the baseline/incident
    # log windows are cleanly separated at startsAt)
    time.sleep(1.0)
    print(f"==> enabling fault {scenario['fault']!r}")
    set_fault(args.app_url, scenario["fault"], True)
    starts_at = _now_iso()
    time.sleep(1)
    print("==> incident traffic (degraded)")
    incident_traffic(name, args.app_url)

    # 4. fire the alert
    result = post_alert(args.url, build_payload(name, "firing", starts_at, None))
    print(f"==> alert fired: {json.dumps(result)}")
    if result["results"] and result["results"][0]["action"] == "refired":
        print("    NOTE: an incident for this alert was already open, so this fire "
              "deduped onto it\n    and no new analysis runs. Resolve it "
              f"(--resolve-after/--resolve-only) and re-run {name}.")
    if bad_sha:
        print(f"    expected: runbook={scenario['expected_runbook']} culprit={bad_sha} "
              f"({scenario['patch']['file']})")
    else:
        print(f"    expected: runbook={scenario['expected_runbook']} culprit=NONE "
              f"(agent should report no_culprit_found)")

    # 5. resolve
    if args.resolve_after is not None:
        print(f"==> resolving in {args.resolve_after}s (analysis runs meanwhile)")
        time.sleep(args.resolve_after)
        set_fault(args.app_url, scenario["fault"], False)
        result = post_alert(args.url, build_payload(name, "resolved", starts_at, _now_iso()))
        print(f"==> resolved: {json.dumps(result)} — postmortem generating")
    else:
        print(f"    fault is still ON; resolve with: "
              f"python3 chaos/inject.py {name} --resolve-only")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS) + ["reset"])
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--resolve-after", type=int, metavar="SECONDS",
                        help="turn the fault off and send the resolved payload after N seconds")
    parser.add_argument("--resolve-only", action="store_true",
                        help="just turn the fault off and send the resolved payload")
    parser.add_argument("--alert-only", action="store_true",
                        help="fire the alert payload without touching the demo app or repo")
    parser.add_argument("--url", default="http://localhost:8080/webhook/alert",
                        help="agent webhook URL")
    parser.add_argument("--app-url", default="http://localhost:8081",
                        help="demo app base URL")
    args = parser.parse_args()

    if args.list:
        for name, s in SCENARIOS.items():
            print(f"{name}: {s['annotations']['summary']}")
        return 0
    if not args.scenario:
        parser.error("scenario is required (or use --list)")
    if args.scenario == "reset":
        reset_faults(args.app_url)
        print("all faults off")
        return 0
    if args.resolve_only:
        scenario = SCENARIOS[args.scenario]
        set_fault(args.app_url, scenario["fault"], False)
        # startsAt is unknown here; the agent matches on fingerprint, so a
        # nominal value is fine for the resolved payload.
        result = post_alert(
            args.url, build_payload(args.scenario, "resolved", _now_iso(), _now_iso())
        )
        print(f"resolved {args.scenario}: {json.dumps(result)}")
        return 0

    try:
        return run_scenario(args.scenario, args)
    except urllib.error.URLError as e:
        print(f"could not reach a service: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"git failed: {e.stderr}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
