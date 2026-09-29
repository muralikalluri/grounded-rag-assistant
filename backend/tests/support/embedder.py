import hashlib
import math


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder: fast, offline, similar text ~ similar vector."""

    def __init__(self, dim: int = 384, model_name: str = "fake-embedder") -> None:
        self.dim = dim
        self.model_name = model_name

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * self.dim
            for w in t.lower().split():
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)  # noqa: S324
                v[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out
