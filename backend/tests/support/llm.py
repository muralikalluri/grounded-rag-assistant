import asyncio
import json
from typing import Any

from app.llm.types import LLMAttempt, LLMError, LLMResult
from app.retrieval.types import RetrievalResult, RetrievedChunk


def answer_json(
    sentences: list[tuple[str, list[tuple[str, str]]]], status: str = "answered"
) -> str:
    return json.dumps(
        {
            "status": status,
            "answer_sentences": [
                {"text": t, "citations": [{"source": s, "quote": q} for s, q in cites]}
                for t, cites in sentences
            ],
        }
    )


class FakeProvider:
    """Scripted provider. Items: str (model text), LLMResult, LLMError, or a (text, finish) pair."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []
        self.cancelled = False

    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult:
        self.calls.append((system, user))
        if not self.script:
            raise AssertionError("FakeProvider script exhausted")
        item = self.script.pop(0)
        if isinstance(item, LLMError):
            raise item
        if isinstance(item, LLMResult):
            return item
        text, finish = item if isinstance(item, tuple) else (item, "stop")
        attempt = LLMAttempt("fake", "fake-model", 1, "ok", 1.0, 100, 50)
        return LLMResult(text, finish, "fake", "fake-model", [attempt])


class BlockingProvider:
    """Never returns; records cancellation (client-disconnect test)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult:
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class StubRetrieval:
    """Stands in for RetrievalService: returns fixed chunks; records calls."""

    def __init__(self, chunks: list[RetrievedChunk], reranked: bool = True) -> None:
        self.chunks = chunks
        self.reranked = reranked
        self.calls = 0

    async def retrieve(
        self, principal: Any, query: str, collection_ids: Any = None
    ) -> RetrievalResult:
        self.calls += 1
        return RetrievalResult(
            principal.user_id, [], list(self.chunks), reranked=self.reranked, counts={}
        )
