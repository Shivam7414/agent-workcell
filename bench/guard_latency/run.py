"""Guard latency benchmark: the same queries with and without the guard.

    python bench/guard_latency/run.py --database-url OWNER_URL --runs 30

Needs the reference ERP loaded (any --scale) and the guard installed (guard apply), in the database that OWNER_URL
points to. The agent and binder log in with WORKCELL_AGENT_PASSWORD and WORKCELL_BINDER_PASSWORD.

Modes:
- unguarded: the owner reads the real tables. No guard, no row rules.
- guarded admin: through the guard as an admin. The same rows as unguarded, so the difference is the guard's cost.
- guarded buyer, guarded supplier: through the guard as a person who sees fewer rows. This is what people get, but
  it is not a like-for-like comparison with unguarded.

Writes bench/results/guard-latency-<date>-<orders>.json. Calls no model and costs nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from agent_workcell.guard import GuardedConnection, Limits, Principal
from agent_workcell.policy import load_policy

ROOT = Path(__file__).resolve().parents[2]
LIMITS = Limits(timeout_ms=120_000, max_rows=1_000_000, max_chars=10**10)

QUERIES = {
    "orders by status": "SELECT status, count(*) FROM purchase_orders GROUP BY status",
    "order value by supplier, last 90 days": """
        SELECT o.supplier_id, sum(l.quantity * l.unit_price * (1 - COALESCE(l.discount_pct, 0) / 100))
        FROM purchase_orders o JOIN purchase_order_lines l ON l.purchase_order_id = o.id
        WHERE o.order_date > DATE '2026-06-30' - 90 AND o.deleted_at IS NULL
        GROUP BY o.supplier_id""",
    "overdue approved invoices": """
        SELECT count(*), sum(total_amount) FROM supplier_invoices
        WHERE status = 'approved' AND due_date < DATE '2026-06-30'""",
    "paid per invoice, failed payments excluded": """
        SELECT i.document_number, sum(a.amount)
        FROM supplier_invoices i
        JOIN payment_allocations a ON a.supplier_invoice_id = i.id
        JOIN payments p ON p.id = a.payment_id AND p.status <> 'failed'
        GROUP BY i.document_number""",
    "one order by number": "SELECT * FROM purchase_orders WHERE po_number = '4500000001'",
}


def timed(run, text: str, runs: int, warmup: int = 3) -> dict:
    for _ in range(warmup):
        run(text)
    timings = []
    rows = 0
    for _ in range(runs):
        started = time.perf_counter()
        rows = run(text)
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return {
        "median_ms": round(statistics.median(timings), 2),
        "p95_ms": round(timings[max(0, int(len(timings) * 0.95) - 1)], 2),
        "min_ms": round(timings[0], 2),
        "rows": rows,
    }


def commit() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        return head.stdout.strip() + ("-dirty" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "none (no commit yet; run from an uncommitted working tree)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("ERP_DATABASE_URL"))
    parser.add_argument("--policy", default=str(ROOT / "reference_erp" / "policy.toml"))
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42, help="the seed the data was loaded with (recorded only)")
    parser.add_argument("--scale", type=int, required=True, help="the scale the data was loaded with (recorded only)")
    parser.add_argument("--output-dir", default=str(ROOT / "bench" / "results"))
    args = parser.parse_args(argv)

    agent_password = os.environ.get("WORKCELL_AGENT_PASSWORD")
    binder_password = os.environ.get("WORKCELL_BINDER_PASSWORD")
    if not args.database_url or not agent_password or not binder_password:
        print("Set ERP_DATABASE_URL (or --database-url), WORKCELL_AGENT_PASSWORD and WORKCELL_BINDER_PASSWORD.",
              file=sys.stderr)
        return 2

    policy = load_policy(args.policy)
    agent_url = make_conninfo(args.database_url, user="workcell_agent", password=agent_password)
    binder_url = make_conninfo(args.database_url, user="workcell_binder", password=binder_password)

    owner = psycopg.connect(args.database_url, autocommit=True)
    owner.execute("ANALYZE")
    orders = owner.execute("SELECT count(*) FROM purchase_orders").fetchone()[0]
    server = owner.execute("SHOW server_version").fetchone()[0]
    admin = owner.execute("SELECT min(id) FROM users WHERE role = 'admin'").fetchone()[0]
    buyer = owner.execute("SELECT buyer_id FROM purchase_orders ORDER BY id LIMIT 1").fetchone()[0]
    supplier = owner.execute("SELECT supplier_id FROM purchase_orders GROUP BY supplier_id "
                             "ORDER BY count(*) DESC, supplier_id LIMIT 1").fetchone()[0]
    supplier_user = owner.execute("SELECT min(id) FROM users WHERE supplier_id = %s", [supplier]).fetchone()[0]
    owner.autocommit = False
    owner.read_only = True

    def unguarded(text: str) -> int:
        try:
            owner.execute("SET LOCAL search_path = public")
            with owner.cursor(name="bench") as cursor:
                cursor.execute(text)
                return len(cursor.fetchall())
        finally:
            owner.rollback()

    people = {
        "guarded admin": Principal("admin", admin),
        "guarded buyer": Principal("buyer", buyer),
        "guarded supplier": Principal("supplier_user", supplier_user, {"supplier_id": supplier}),
    }
    results: dict[str, dict] = {name: {} for name in QUERIES}
    for name, text in QUERIES.items():
        results[name]["unguarded"] = timed(unguarded, text, args.runs)
    for mode, principal in people.items():
        with GuardedConnection(agent_url, binder_url, policy, principal, LIMITS) as connection:
            for name, text in QUERIES.items():
                results[name][mode] = timed(lambda t: len(connection.query(t).rows), text, args.runs)
    owner.close()

    now = dt.datetime.now(dt.UTC)
    record = {
        "benchmark": "guard latency",
        "date": now.isoformat(timespec="seconds"),
        "commit": commit(),
        "model": "none (no model is called)",
        "seed": args.seed,
        "scale": args.scale,
        "cases": len(QUERIES),
        "purchase_orders": orders,
        "runs_per_query": args.runs,
        "warmup_runs": 3,
        "postgres": server,
        "python": platform.python_version(),
        "machine": f"{platform.system()} {platform.release()}, {platform.machine()}, {os.cpu_count()} logical CPUs",
        "policy_sha256": policy.digest,
        "note": "Database in Docker on the same machine. Times include the round trip from Python. "
                "Guarded buyer and supplier read fewer rows than unguarded, so compare them with care.",
        "queries": {name: " ".join(text.split()) for name, text in QUERIES.items()},
        "results": results,
    }
    out = Path(args.output_dir) / f"guard-latency-{now:%Y-%m-%d}-{orders}-orders.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(f"{orders:,} purchase orders, {args.runs} runs per query, median ms (p95)")
    modes = ["unguarded", *people]
    print(f"{'query':<44}" + "".join(f"{mode:>20}" for mode in modes))
    for name in QUERIES:
        cells = "".join(f"{results[name][m]['median_ms']:>11} ({results[name][m]['p95_ms']:>6})" for m in modes)
        print(f"{name:<44}{cells}")
    print(f"Written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
