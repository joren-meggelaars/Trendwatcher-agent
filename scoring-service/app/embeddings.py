import hashlib
import math
import re
from abc import ABC, abstractmethod

import voyageai

from app.config import settings

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class VoyageEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed(self, text: str) -> list[float]:
        result = self._client.embed([text], model=self._model, input_type="document")
        return result.embeddings[0]


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic, dependency-free stand-in for VoyageEmbeddingProvider.

    Selected via EMBEDDING_PROVIDER=fake. Hashes words into a fixed-size
    vector (feature hashing) so texts sharing vocabulary land closer together
    than unrelated texts — enough to exercise /score and /feedback end-to-end
    without a Voyage API key, e.g. before one has been issued.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            idx = int(hashlib.sha256(token.encode()).hexdigest(), 16) % self._dim
            vector[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]


def get_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding_dim)
    return VoyageEmbeddingProvider(api_key=settings.voyage_api_key, model=settings.voyage_model)
