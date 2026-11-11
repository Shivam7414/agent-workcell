"""The knowledge graph: what the data means, checked against the real database.

- graph.py loads the hand-written TOML files.
- catalog.py reads table and column names and types from the live database.
- check.py compares the two and fails on anything missing, unknown, unexplained or stale.
- guide.py generates the data guide for one role.
- retrieve.py finds the knowledge one question needs.
"""

from agent_workcell.knowledge.catalog import Catalog, read_catalog
from agent_workcell.knowledge.graph import Graph, KnowledgeError, load_graph
from agent_workcell.knowledge.guide import render_guide
from agent_workcell.knowledge.retrieve import Context, Retriever, render_context

__all__ = [
    "Catalog",
    "Context",
    "Graph",
    "KnowledgeError",
    "Retriever",
    "load_graph",
    "read_catalog",
    "render_context",
    "render_guide",
]
