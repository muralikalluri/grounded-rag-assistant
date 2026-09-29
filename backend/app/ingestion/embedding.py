import asyncio
import copy
from typing import Protocol

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import Settings
from app.inference import INFERENCE_LOCK


class Embedder(Protocol):
    model_name: str
    dim: int

    def count_tokens(self, text: str) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """L2-normalised vectors (the index uses cosine distance)."""
        ...


class LocalEmbedder:
    """sentence-transformers model running locally; no API key needed."""

    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.embedding_model
        self._batch = settings.embedding_batch_size
        self._model = SentenceTransformer(self.model_name)
        dim = self._model.get_embedding_dimension()
        if dim is None:
            raise RuntimeError("embedding model reports no dimension")
        self.dim = int(dim)
        # count_tokens (used by the chunker on the event-loop thread) gets its OWN tokenizer copy,
        # so it never shares truncation state with encode() running in a worker thread.
        self._tokenizer = copy.deepcopy(self._model.tokenizer)

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def embed(self, texts: list[str]) -> list[list[float]]:
        with INFERENCE_LOCK:
            vecs = self._model.encode(
                texts, batch_size=self._batch, normalize_embeddings=True, show_progress_bar=False
            )
        return [np.asarray(v, dtype=float).tolist() for v in vecs]


async def embed_async(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """Run the CPU-bound embedder off the event loop."""
    return await asyncio.to_thread(embedder.embed, texts)
