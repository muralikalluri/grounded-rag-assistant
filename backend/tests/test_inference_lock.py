"""Local torch inference must never run concurrently (segfault / Metal assertion in testing)."""

import threading
import time
from typing import Any

from app.config import Settings
from app.inference import INFERENCE_LOCK
from app.ingestion.embedding import LocalEmbedder
from app.retrieval.rerank import CrossEncoderReranker


class Probe:
    """Records the peak number of threads inside encode()/predict() at once."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(0.03)
        with self._lock:
            self.running -= 1


class FakeSentenceModel:
    def __init__(self, probe: Probe) -> None:
        self.probe = probe

    def encode(self, texts: list[str], **kw: Any) -> list[list[float]]:
        self.probe.enter()
        return [[0.0] * 3 for _ in texts]


class FakeCrossEncoder:
    def __init__(self, probe: Probe) -> None:
        self.probe = probe

    def predict(self, pairs: list[tuple[str, str]], **kw: Any) -> list[float]:
        self.probe.enter()
        return [0.0] * len(pairs)


class FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, ids: list[int]) -> str:
        return " ".join("w" for _ in ids)


def run_in_threads(fn: Any, n: int = 8) -> None:
    threads = [threading.Thread(target=fn) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]


def test_embedder_and_reranker_share_one_lock_so_they_never_overlap() -> None:
    probe = Probe()
    emb = LocalEmbedder.__new__(LocalEmbedder)
    emb._model = FakeSentenceModel(probe)  # type: ignore[attr-defined]
    emb._batch = 8  # type: ignore[attr-defined]
    rr = CrossEncoderReranker.__new__(CrossEncoderReranker)
    rr._model = FakeCrossEncoder(probe)  # type: ignore[attr-defined]
    rr._tok = FakeTokenizer()  # type: ignore[attr-defined]
    rr._max_len, rr._max_query_tokens, rr._batch = 512, 64, 16  # type: ignore[attr-defined]

    def work() -> None:
        emb.embed(["a b c"])
        rr.score("query words", ["passage one", "passage two"])

    run_in_threads(work)
    assert probe.peak == 1  # encode() and predict() from many threads: never concurrent


def test_the_lock_is_process_wide_and_not_held_while_idle() -> None:
    assert INFERENCE_LOCK.acquire(blocking=False)  # free when nobody is inferring
    INFERENCE_LOCK.release()


def test_reranker_tokenizer_work_happens_under_the_lock() -> None:
    seen: list[bool] = []

    class Spy(FakeTokenizer):
        def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
            seen.append(INFERENCE_LOCK.locked())  # is the lock held while we tokenise?
            return super().encode(text, add_special_tokens)

    rr = CrossEncoderReranker.__new__(CrossEncoderReranker)
    rr._model = FakeCrossEncoder(Probe())  # type: ignore[attr-defined]
    rr._tok = Spy()  # type: ignore[attr-defined]
    rr._max_len, rr._max_query_tokens, rr._batch = 512, 64, 16  # type: ignore[attr-defined]
    rr.score("a query", ["p1", "p2"])
    assert seen and all(seen)


def test_count_tokens_uses_its_own_tokenizer_copy() -> None:
    import copy

    original = FakeTokenizer()
    emb = LocalEmbedder.__new__(LocalEmbedder)
    emb._tokenizer = copy.deepcopy(original)  # type: ignore[attr-defined]
    assert emb._tokenizer is not original  # type: ignore[attr-defined]
    assert emb.count_tokens("one two three") == 3
    assert Settings().chunk_max_tokens < 512
