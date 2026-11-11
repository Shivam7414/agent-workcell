"""Command line for the data guard.

    python -m agent_workcell.guard plan   --policy reference_erp/policy.toml > guard.sql
    python -m agent_workcell.guard doctor --policy reference_erp/policy.toml --database-url URL
    python -m agent_workcell.guard apply  --policy reference_erp/policy.toml --database-url ADMIN_URL

`apply` is for local development: it runs the plan and sets the passwords from WORKCELL_AGENT_PASSWORD and
WORKCELL_BINDER_PASSWORD. In production a database administrator reviews the output of `plan` and runs it.
"""

from __future__ import annotations

import argparse
import os
import sys

from agent_workcell.guard.doctor import check, errors
from agent_workcell.guard.plan import install, plan_sql
from agent_workcell.policy import PolicyError, load_policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_workcell.guard", description="The data guard.")
    parser.add_argument("command", choices=["plan", "doctor", "apply"])
    parser.add_argument("--policy", default=os.environ.get("WORKCELL_POLICY"),
                        help="policy file (default: $WORKCELL_POLICY)")
    parser.add_argument("--database-url", default=os.environ.get("ERP_DATABASE_URL"),
                        help="database to check or install into (default: $ERP_DATABASE_URL)")
    args = parser.parse_args(argv)

    if not args.policy:
        print("Set WORKCELL_POLICY or pass --policy.", file=sys.stderr)
        return 2
    try:
        policy = load_policy(args.policy)
    except (OSError, PolicyError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.command == "plan":
        print(plan_sql(policy), end="")
        return 0

    if not args.database_url:
        print("Set ERP_DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if args.command == "apply":
            agent, binder = os.environ.get("WORKCELL_AGENT_PASSWORD"), os.environ.get("WORKCELL_BINDER_PASSWORD")
            if not agent or not binder:
                print("Set WORKCELL_AGENT_PASSWORD and WORKCELL_BINDER_PASSWORD.", file=sys.stderr)
                return 2
            install(conn, policy, agent, binder)
            print("Guard installed.")
        problems = check(conn, policy)

    for problem in problems:
        print(problem)
    if errors(problems):
        print("The guard is not safe to use.", file=sys.stderr)
        return 1
    print("Guard check passed." if not problems else "Guard check passed with warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
