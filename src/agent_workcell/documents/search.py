"""Search passages by words and by meaning, as the person who asked.

The search is fixed SQL with bound values, and it runs on the person's guarded connection. It reads the guard views,
so it finds only passages of documents that person may read; it has no access rules of its own.

Two rankings are merged (hybrid search):
- words: PostgreSQL full-text search (websearch_to_tsquery on document_chunks.search_text);
- meaning: cosine distance between the question's embedding and the passages' embeddings of the same model.
Each passage scores 1 / (RRF_K + its rank) in each list it is in (reciprocal rank fusion). The value of RRF_K and
whether this merge beats either list alone are **to verify** by a benchmark.

The search compares every candidate passage exactly; no approximate vector index is used. How it performs on a large
set of documents is not measured.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_workcell.documents.embed import Embedder, vector_text
from agent_workcell.guard import GuardedConnection

RRF_K = 60
POOL = 50  # candidates taken from each ranking before they are merged
MAX_RESULTS = 20
MAX_QUERY_CHARS = 1_000

# Passages of the current version of each document that is not deleted: the highest version_no that is ready.
CANDIDATES = """
candidates AS (
    SELECT c.id, c.document_version_id, c.chunk_no, c.page_number, c.section, c.content, c.search_text,
           v.version_no, d.doc_number, d.title
    FROM document_chunks c
    JOIN document_versions v ON v.id = c.document_version_id
    JOIN documents d ON d.id = v.document_id
    WHERE d.deleted_at IS NULL AND v.status = 'ready'
      AND v.version_no = (SELECT max(v2.version_no) FROM document_versions v2
                          WHERE v2.document_id = v.document_id AND v2.status = 'ready')
)"""

WORDS = """
words AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.search_text, q.query) DESC, c.id) AS rank
    FROM candidates c, websearch_to_tsquery('english', %(text)s) AS q(query)
    WHERE c.search_text @@ q.query
    ORDER BY rank LIMIT %(pool)s
)"""

MEANING = """
meaning AS (
    SELECT c.id, row_number() OVER (ORDER BY e.embedding OPERATOR(public.<=>) %(vector)s::public.vector, c.id) AS rank
    FROM candidates c
    JOIN document_chunk_embeddings e ON e.document_version_id = c.document_version_id AND e.chunk_no = c.chunk_no
    WHERE e.model = %(model)s AND public.vector_dims(e.embedding) = %(dimensions)s
    ORDER BY rank LIMIT %(pool)s
)"""

MERGE = """
SELECT c.doc_number, c.title, c.version_no, c.page_number, c.section, c.chunk_no, c.content,
       round(f.score::numeric, 5) AS score
FROM (SELECT id, sum(1.0 / (%(k)s + rank)) AS score FROM ({ranked}) r GROUP BY id) f
JOIN candidates c ON c.id = f.id
ORDER BY f.score DESC, c.id
LIMIT %(limit)s"""


@dataclass(frozen=True)
class Hit:
    doc_number: str
    title: str
    version_no: int
    page_number: int | None
    section: str | None
    chunk_no: int
    content: str
    score: float

    def citation(self) -> str:
        """Where the passage is, built from the row, never typed by the model."""
        place = [f"version {self.version_no}"]
        if self.page_number is not None:
            place.append(f"page {self.page_number}")
        if self.section:
            place.append(f"section \"{self.section}\"")
        return f"{self.doc_number} \"{self.title}\", {', '.join(place)}, passage {self.chunk_no}"


def search_sql(with_meaning: bool) -> str:
    ranked = "SELECT * FROM words UNION ALL SELECT * FROM meaning" if with_meaning else "SELECT * FROM words"
    parts = [CANDIDATES, WORDS] + ([MEANING] if with_meaning else [])
    return "WITH " + ",".join(parts) + MERGE.format(ranked=ranked)


def search(connection: GuardedConnection, text: str, embedder: Embedder | None = None,
           limit: int = 8) -> list[Hit]:
    """The best passages for `text` that this connection's person may read. With an embedder, words and meaning;
    without one, words only."""
    text = text.strip()[:MAX_QUERY_CHARS]
    if not text:
        return []
    params: dict = {"text": text, "pool": POOL, "k": RRF_K, "limit": max(1, min(limit, MAX_RESULTS))}
    if embedder is not None:
        params.update(vector=vector_text(embedder.embed([text])[0]), model=embedder.model,
                      dimensions=embedder.dimensions)
    result = connection.query(search_sql(embedder is not None), params=params)
    return [Hit(*row[:7], float(row[7])) for row in result.rows]
