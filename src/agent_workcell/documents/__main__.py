"""Command line for documents.

    python -m agent_workcell.documents plan  --policy reference_erp/policy.toml > documents.sql
    python -m agent_workcell.documents apply --policy reference_erp/policy.toml --database-url OWNER_URL
    python -m agent_workcell.documents check   # the documents role can do its job and nothing more
    python -m agent_workcell.documents work    # process uploaded files; --once stops when no job is due

`apply` is for local development: it runs the plan and sets the password from WORKCELL_DOCUMENTS_PASSWORD. In
production a database administrator reviews the output of `plan` and runs it. `check` and `work` log in with
WORKCELL_DOCUMENTS_DATABASE_URL. `work` reads the files from WORKCELL_DATA_DIR/documents, where the answer server
stores them.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from agent_workcell.policy import PolicyError, load_policy

IDLE_S = 2.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_workcell.documents", description="Documents.")
    parser.add_argument("command", choices=["plan", "apply", "check", "work"])
    parser.add_argument("--policy", default=os.environ.get("WORKCELL_POLICY"),
                        help="policy file; its schema holds the document tables (default: $WORKCELL_POLICY)")
    parser.add_argument("--database-url", default=os.environ.get("ERP_DATABASE_URL"),
                        help="for apply: the owner of the document tables (default: $ERP_DATABASE_URL)")
    parser.add_argument("--once", action="store_true", help="for work: stop when no job is due")
    args = parser.parse_args(argv)

    if not args.policy:
        print("Set WORKCELL_POLICY or pass --policy.", file=sys.stderr)
        return 2
    try:
        schema = load_policy(args.policy).schema
    except (OSError, PolicyError) as error:
        print(error, file=sys.stderr)
        return 2

    from agent_workcell.documents.store import check_role, install, plan_sql, process_next

    if args.command == "plan":
        print(plan_sql(schema), end="")
        return 0

    import psycopg

    if args.command == "apply":
        password = os.environ.get("WORKCELL_DOCUMENTS_PASSWORD")
        if not args.database_url or not password:
            print("Set ERP_DATABASE_URL (or --database-url) and WORKCELL_DOCUMENTS_PASSWORD.", file=sys.stderr)
            return 2
        with psycopg.connect(args.database_url) as conn:
            install(conn, schema, password)
        print("Documents role and job table installed.")
        return 0

    url = os.environ.get("WORKCELL_DOCUMENTS_DATABASE_URL")
    if not url:
        print("Set WORKCELL_DOCUMENTS_DATABASE_URL: the database URL that logs in as the documents role.",
              file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        problems = check_role(conn, schema)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        print("The documents role does not check out." + (" The worker will not start." if args.command == "work"
                                                           else ""), file=sys.stderr)
        return 1
    if args.command == "check":
        print("The documents role checks out.")
        return 0

    data_dir = os.environ.get("WORKCELL_DATA_DIR")
    if not data_dir:
        print("Set WORKCELL_DATA_DIR: the answer server's data folder, which holds the uploaded files.",
              file=sys.stderr)
        return 2
    storage_root = Path(data_dir).expanduser().resolve() / "documents"
    from agent_workcell.documents.embed import EmbeddingConfigError, embedder_from_env

    try:
        embedder = embedder_from_env(os.environ)
    except EmbeddingConfigError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Worker started. Embeddings: {embedder.model if embedder else 'off'}.", file=sys.stderr)
    while True:
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                while True:
                    outcome = process_next(conn, schema, storage_root, embedder=embedder)
                    if outcome is None:
                        if args.once:
                            return 0
                        time.sleep(IDLE_S)
                    else:
                        print(f"document job: {outcome}", file=sys.stderr)
        except psycopg.OperationalError as error:
            if args.once:
                print(error, file=sys.stderr)
                return 1
            print(f"database connection lost, trying again: {error}", file=sys.stderr)
            time.sleep(IDLE_S * 5)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
