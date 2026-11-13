"""The real embedding providers. By hand only: these call a paid API.

    OPENROUTER_API_KEY=... pytest tests/embedding_conformance
    OPENAI_API_KEY=...     pytest tests/embedding_conformance

Each provider gets one request with three short sentences (about 30 tokens). A provider whose key is not set is
skipped. Not part of the normal test run (see testpaths in pyproject.toml).
"""

import os

import pytest

from agent_workcell.documents.embed import HttpEmbedder, embedder_from_env

CASES = {
    "openrouter": ("OPENROUTER_API_KEY", "openai/text-embedding-3-small", 1536),
    "openai": ("OPENAI_API_KEY", "text-embedding-3-small", 1536),
}


@pytest.mark.parametrize("provider", sorted(CASES))
def test_the_provider_returns_vectors_of_the_stated_size_that_rank_related_text_higher(provider):
    key_name, model, dimensions = CASES[provider]
    if not os.environ.get(key_name):
        pytest.skip(f"{key_name} is not set")
    embedder = embedder_from_env({"WORKCELL_EMBEDDING_PROVIDER": provider, key_name: os.environ[key_name],
                                  "WORKCELL_EMBEDDING_MODEL": model, "WORKCELL_EMBEDDING_DIMENSIONS": str(dimensions)})
    assert isinstance(embedder, HttpEmbedder)
    question, related, unrelated = embedder.embed([
        "When do we have to pay the supplier's bill?",
        "Payment terms: invoices are due 30 days after the invoice date.",
        "Deliveries are accepted at receiving dock 3 on working days.",
    ])
    assert len(question) == len(related) == len(unrelated) == dimensions

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True)) / (
            sum(x * x for x in a) ** 0.5 * sum(y * y for y in b) ** 0.5)

    assert cosine(question, related) > cosine(question, unrelated)
