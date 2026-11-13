"""Embeddings: vectors of a text's meaning, so a search can find passages that use other words.

OpenAI and OpenRouter both offer the same embeddings endpoint (POST <base>/embeddings with a model and a list of
inputs), so one small client serves both. The provider is chosen in settings; the code does not change. Every
response is checked: one finite vector per input, each with the dimensions the settings say. Tests and the seed use
FakeEmbedder, which costs nothing and needs no network.

    WORKCELL_EMBEDDING_PROVIDER     openai | openrouter | fake (leave it out to turn embeddings off)
    WORKCELL_EMBEDDING_MODEL        for example text-embedding-3-small (OpenAI) or openai/text-embedding-3-small
    WORKCELL_EMBEDDING_DIMENSIONS   the model's number of dimensions, for example 1536
    OPENAI_API_KEY / OPENROUTER_API_KEY
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx2

PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}
BATCH_INPUTS = 64  # passages per request
BATCH_CHARS = 60_000  # characters per request, well under the providers' token limits
TIMEOUT_S = 60.0
FAKE_MODEL = "fake-64"
WORD = re.compile(r"[a-z0-9]+")


class EmbeddingError(RuntimeError):
    """The provider failed or answered with something unusable. The message names no key and no input text."""


class EmbeddingConfigError(ValueError):
    pass


class FakeEmbedder:
    """A bag of words hashed into a fixed number of dimensions, then scaled to length 1. Texts that share words get
    similar vectors, so tests can check ranking. It does not understand meaning."""

    def __init__(self, dimensions: int = 64, model: str = FAKE_MODEL):
        self.dimensions = dimensions
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [fake_vector(text, self.dimensions) for text in texts]


def fake_vector(text: str, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    for word in WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % dimensions] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


@dataclass
class HttpEmbedder:
    """The OpenAI-compatible embeddings endpoint of OpenAI or OpenRouter."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    dimensions: int
    transport: httpx2.BaseTransport | None = None  # tests pass a mock transport; never a real network in tests

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in batches(texts):
            vectors.extend(self._request(batch))
        return vectors

    def _request(self, batch: list[str]) -> list[list[float]]:
        try:
            with httpx2.Client(timeout=TIMEOUT_S, transport=self.transport) as client:
                response = client.post(f"{self.base_url}/embeddings",
                                       headers={"Authorization": f"Bearer {self.api_key}"},
                                       json={"model": self.model, "input": batch})
        except httpx2.HTTPError as error:
            raise EmbeddingError(f"The embedding provider could not be reached: {type(error).__name__}") from None
        if response.status_code != 200:
            raise EmbeddingError(f"The embedding provider answered HTTP {response.status_code}.")
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [[float(value) for value in item["embedding"]] for item in data]
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("The embedding provider's answer is not in the expected form.") from None
        check_vectors(vectors, len(batch), self.dimensions)
        return vectors


def check_vectors(vectors: list[list[float]], expected: int, dimensions: int) -> None:
    if len(vectors) != expected:
        raise EmbeddingError(f"The embedding provider returned {len(vectors)} vectors for {expected} inputs.")
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError(f"The embedding model returned {len(vector)} dimensions; the settings say "
                                 f"{dimensions}. Set WORKCELL_EMBEDDING_DIMENSIONS to the model's size.")
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("The embedding provider returned a value that is not a number.")


def batches(texts: Sequence[str]) -> list[list[str]]:
    result: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and (len(current) >= BATCH_INPUTS or size + len(text) > BATCH_CHARS):
            result.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        result.append(current)
    return result


def embedding_input(section: str | None, content: str) -> str:
    """What is embedded for a passage: its section heading too, as for full-text search, because a passage such as
    "Invoices are due in 30 days." means "payment terms" only under its heading."""
    return f"{section}\n{content}" if section else content


def vector_text(vector: Sequence[float]) -> str:
    """The form PostgreSQL's vector type reads, such as [0.1,-0.2]."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


Embedder = FakeEmbedder | HttpEmbedder


def embedder_from_env(env: Mapping[str, str]) -> Embedder | None:
    """The embedder the settings ask for, or None when embeddings are off. A provider with a missing model, size or
    key is a configuration error, never a silent fallback to another provider."""
    provider = env.get("WORKCELL_EMBEDDING_PROVIDER", "").strip().lower()
    if not provider:
        return None
    if provider == "fake":
        return FakeEmbedder()
    if provider not in PROVIDERS:
        raise EmbeddingConfigError(f"WORKCELL_EMBEDDING_PROVIDER must be openai, openrouter or fake, not "
                                   f"{provider!r}.")
    base_url, key_name = PROVIDERS[provider]
    model = env.get("WORKCELL_EMBEDDING_MODEL", "").strip()
    dimensions = env.get("WORKCELL_EMBEDDING_DIMENSIONS", "").strip()
    key = env.get(key_name, "").strip()
    missing = [name for name, value in (("WORKCELL_EMBEDDING_MODEL", model),
                                        ("WORKCELL_EMBEDDING_DIMENSIONS", dimensions), (key_name, key)) if not value]
    if missing:
        raise EmbeddingConfigError(f"Embeddings from {provider} need these settings: {', '.join(missing)}.")
    if not dimensions.isdigit() or not 0 < int(dimensions) <= 16_000:
        raise EmbeddingConfigError("WORKCELL_EMBEDDING_DIMENSIONS must be a whole number from 1 to 16000.")
    return HttpEmbedder(base_url, key, model, int(dimensions))
