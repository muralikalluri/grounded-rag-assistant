import asyncio
import logging
from typing import Protocol

import torch
from sentence_transformers import CrossEncoder

from app.config import Settings
from app.inference import INFERENCE_LOCK

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        """Raw relevance logits aligned with ``passages`` and a per-passage truncation flag."""
        ...


class CrossEncoderReranker:
    """Local cross-encoder (bge-reranker-base). Returns raw logits, not probabilities."""

    def __init__(self, settings: Settings) -> None:
        self._max_len = settings.rerank_max_length
        self._max_query_tokens = settings.rerank_max_query_tokens
        self._batch = settings.rerank_batch_size
        # Identity activation: the default (sigmoid vs none) has changed across versions.
        self._model = CrossEncoder(
            settings.rerank_model,
            max_length=self._max_len,
            activation_fn=torch.nn.Identity(),
        )
        self._tok = self._model.tokenizer

    def _clip_query(self, query: str) -> str:
        ids = self._tok.encode(query, add_special_tokens=False)
        if len(ids) <= self._max_query_tokens:
            return query
        return str(self._tok.decode(ids[: self._max_query_tokens]))

    def score(self, query: str, passages: list[str]) -> tuple[list[float], list[bool]]:
        if not passages:
            return [], []
        # Everything that touches the shared tokenizer runs under the same lock as predict():
        # predict() changes the tokenizer's truncation state, so concurrent use races
        # ("Already borrowed").
        with INFERENCE_LOCK, torch.inference_mode():
            q = self._clip_query(query)
            q_len = len(self._tok.encode(q, add_special_tokens=False))
            # Only the passage should be truncated; the query is clipped first, then the
            # tokenizer trims the (longer) passage to fit max_length.
            truncated = [
                q_len + len(self._tok.encode(p, add_special_tokens=False)) + 4 > self._max_len
                for p in passages
            ]
            scores = self._model.predict(
                [(q, p) for p in passages],
                batch_size=self._batch,
                show_progress_bar=False,
            )
        return [float(s) for s in scores], truncated


async def score_async(
    reranker: Reranker, query: str, passages: list[str]
) -> tuple[list[float], list[bool]]:
    return await asyncio.to_thread(reranker.score, query, passages)
