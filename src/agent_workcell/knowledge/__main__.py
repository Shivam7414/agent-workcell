"""Command line for the knowledge graph.

    python -m agent_workcell.knowledge check            # compare the knowledge with the live database
    python -m agent_workcell.knowledge guide            # write the generated guide for every role
    python -m agent_workcell.knowledge find --role buyer "invoices in review"

Settings come from flags or from WORKCELL_KNOWLEDGE, WORKCELL_POLICY and ERP_DATABASE_URL. The guides are written to
<knowledge>/guide/<role>.md.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent_workcell.knowledge.graph import KnowledgeError, load_graph
from agent_workcell.policy import PolicyError, load_policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_workcell.knowledge", description="The knowledge graph.")
    parser.add_argument("command", choices=["check", "guide", "find"])
    parser.add_argument("question", nargs="?", help="for find: the question to look up")
    parser.add_argument("--role", help="for find: the role whose view of the data is used")
    parser.add_argument("--knowledge", default=os.environ.get("WORKCELL_KNOWLEDGE"),
                        help="knowledge directory (default: $WORKCELL_KNOWLEDGE)")
    parser.add_argument("--policy", default=os.environ.get("WORKCELL_POLICY"),
                        help="access policy file (default: $WORKCELL_POLICY)")
    parser.add_argument("--database-url", default=os.environ.get("ERP_DATABASE_URL"),
                        help="database to read the catalog from (default: $ERP_DATABASE_URL)")
    parser.add_argument("--source-root", default=".", help="where rule source paths start (default: .)")
    args = parser.parse_intermixed_args(argv)

    if not args.knowledge or not args.policy:
        print("Set WORKCELL_KNOWLEDGE and WORKCELL_POLICY, or pass --knowledge and --policy.", file=sys.stderr)
        return 2
    try:
        graph = load_graph(args.knowledge)
        policy = load_policy(args.policy)
    except (OSError, KnowledgeError, PolicyError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.command == "find":
        from agent_workcell.knowledge.retrieve import Retriever, render_context

        if not args.question or args.role not in policy.roles:
            print(f"Give a question and --role (one of {', '.join(policy.roles)}).", file=sys.stderr)
            return 2
        print(render_context(graph, Retriever(graph, policy, args.role).find(args.question)), end="")
        return 0

    if not args.database_url:
        print("Set ERP_DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2

    import psycopg

    from agent_workcell.knowledge.catalog import read_catalog
    from agent_workcell.knowledge.check import check
    from agent_workcell.knowledge.guide import render_guide

    guide_dir = Path(args.knowledge) / "guide"
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if args.command == "guide":
            catalog = read_catalog(conn, policy.schema)
            guide_dir.mkdir(exist_ok=True)
            for role in policy.roles:
                path = guide_dir / f"{role}.md"
                path.write_text(render_guide(graph, catalog, policy, role), encoding="utf-8", newline="\n")
                print(f"Wrote {path}")
            return 0
        problems = check(conn, graph, policy, guide_dir, Path(args.source_root))

    for problem in problems:
        print(problem)
    if problems:
        print("The knowledge check failed.", file=sys.stderr)
        return 1
    print("Knowledge check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
