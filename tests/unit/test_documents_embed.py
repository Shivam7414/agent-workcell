"""Embedders: the free fake one, and the OpenAI-compatible HTTP client against a mock transport. No network."""

import json
import math

import httpx2
import pytest

from agent_workcell.documents.embed import (
    BATCH_INPUTS,
    EmbeddingConfigError,
    EmbeddingError,
    FakeEmbedder,
    HttpEmbedder,
    batches,
    embedder_from_env,
    fake_vector,
    vector_text,
)

KEY = "sk-test-not-a-real-key"


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_fake_embedder_is_repeatable_has_length_one_and_ranks_shared_words_higher():
    embedder = FakeEmbedder()
    one, again = embedder.embed(["Invoices are due 30 days after the invoice date."] * 2)
    assert one == again
    assert len(one) == 64 and math.isclose(math.sqrt(sum(v * v for v in one)), 1.0, abs_tol=1e-4)
    near = fake_vector("When are invoices due after the invoice date?")
    far = fake_vector("Deliveries arrive at dock 3 before noon.")
    assert cosine(one, near) > cosine(one, far)


def provider(handler):
    seen = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    return seen, httpx2.MockTransport(respond)


def vectors_for(request: httpx2.Request, dimensions: int = 3, reverse: bool = True) -> httpx2.Response:
    inputs = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [float(i)] * dimensions} for i in range(len(inputs))]
    return httpx2.Response(200, json={"data": list(reversed(data)) if reverse else data})


def test_the_http_embedder_sends_the_model_and_key_and_keeps_the_input_order():
    seen, transport = provider(vectors_for)
    embedder = HttpEmbedder("https://provider.example/v1", KEY, "some/model", 3, transport)
    vectors = embedder.embed(["a", "b", "c"])
    assert vectors == [[0.0] * 3, [1.0] * 3, [2.0] * 3], "sorted by index, whatever order the provider used"
    request = seen[0]
    assert str(request.url) == "https://provider.example/v1/embeddings"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {"model": "some/model", "input": ["a", "b", "c"]}


def test_many_inputs_go_in_several_requests():
    seen, transport = provider(vectors_for)
    texts = [f"text {i}" for i in range(BATCH_INPUTS * 2 + 1)]
    assert len(HttpEmbedder("https://p.example/v1", KEY, "m", 3, transport).embed(texts)) == len(texts)
    assert [len(json.loads(r.content)["input"]) for r in seen] == [BATCH_INPUTS, BATCH_INPUTS, 1]
    assert batches(["x" * 40_000, "y" * 40_000]) == [["x" * 40_000], ["y" * 40_000]]


@pytest.mark.parametrize(("handler", "message"), [
    (lambda r: vectors_for(r, dimensions=5), "returned 5 dimensions"),
    (lambda r: httpx2.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]}), "1 vectors for 2"),
    (lambda r: httpx2.Response(200, content=b'{"data": [{"index": 0, "embedding": [NaN, 1, 2]}, '
                                            b'{"index": 1, "embedding": [1, 2, 3]}]}'), "not a number"),
    (lambda r: httpx2.Response(200, json={"unexpected": True}), "not in the expected form"),
    (lambda r: httpx2.Response(401, json={"error": "bad key"}), "HTTP 401"),
    (lambda r: httpx2.Response(429, json={"error": "slow down"}), "HTTP 429"),
])
def test_an_unusable_answer_is_an_error_that_never_shows_the_key(handler, message):
    _, transport = provider(handler)
    with pytest.raises(EmbeddingError) as failed:
        HttpEmbedder("https://p.example/v1", KEY, "m", 3, transport).embed(["a", "b"])
    assert message in str(failed.value)
    assert KEY not in str(failed.value)


def test_a_network_failure_is_an_embedding_error():
    def unreachable(request):
        raise httpx2.ConnectError("no route")

    with pytest.raises(EmbeddingError, match="could not be reached"):
        HttpEmbedder("https://p.example/v1", KEY, "m", 3, httpx2.MockTransport(unreachable)).embed(["a"])


def test_the_key_is_not_in_the_embedders_repr():
    assert KEY not in repr(HttpEmbedder("https://p.example/v1", KEY, "m", 3))


def test_settings_choose_the_provider_without_code_changes():
    assert embedder_from_env({}) is None
    assert isinstance(embedder_from_env({"WORKCELL_EMBEDDING_PROVIDER": "fake"}), FakeEmbedder)
    common = {"WORKCELL_EMBEDDING_MODEL": "m", "WORKCELL_EMBEDDING_DIMENSIONS": "1536"}
    openrouter = embedder_from_env({"WORKCELL_EMBEDDING_PROVIDER": "openrouter", "OPENROUTER_API_KEY": KEY, **common})
    openai = embedder_from_env({"WORKCELL_EMBEDDING_PROVIDER": "OpenAI", "OPENAI_API_KEY": KEY, **common})
    assert (openrouter.base_url, openrouter.dimensions) == ("https://openrouter.ai/api/v1", 1536)
    assert openai.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize(("env", "message"), [
    ({"WORKCELL_EMBEDDING_PROVIDER": "somewhere"}, "must be openai, openrouter or fake"),
    ({"WORKCELL_EMBEDDING_PROVIDER": "openrouter", "WORKCELL_EMBEDDING_MODEL": "m",
      "WORKCELL_EMBEDDING_DIMENSIONS": "8"}, "OPENROUTER_API_KEY"),
    # A key for the other provider must not be used instead.
    ({"WORKCELL_EMBEDDING_PROVIDER": "openai", "OPENROUTER_API_KEY": KEY, "WORKCELL_EMBEDDING_MODEL": "m",
      "WORKCELL_EMBEDDING_DIMENSIONS": "8"}, "OPENAI_API_KEY"),
    ({"WORKCELL_EMBEDDING_PROVIDER": "openai", "OPENAI_API_KEY": KEY, "WORKCELL_EMBEDDING_DIMENSIONS": "8"},
     "WORKCELL_EMBEDDING_MODEL"),
    ({"WORKCELL_EMBEDDING_PROVIDER": "openai", "OPENAI_API_KEY": KEY, "WORKCELL_EMBEDDING_MODEL": "m",
      "WORKCELL_EMBEDDING_DIMENSIONS": "0"}, "whole number"),
])
def test_a_provider_with_missing_or_wrong_settings_is_refused(env, message):
    with pytest.raises(EmbeddingConfigError, match=message):
        embedder_from_env(env)


def test_a_vector_is_written_in_the_form_postgresql_reads():
    assert vector_text([0.5, -1, 2.25]) == "[0.5,-1.0,2.25]"
