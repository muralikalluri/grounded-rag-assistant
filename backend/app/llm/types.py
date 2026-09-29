from dataclasses import dataclass, field
from decimal import Decimal


class LLMError(Exception):
    """Provider failure. ``retryable`` was already acted on inside the provider."""

    def __init__(self, kind: str, status_code: int | None = None) -> None:
        # Deliberately no provider message: it can echo prompt text.
        super().__init__(f"{kind} (status={status_code})")
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class LLMAttempt:
    provider: str
    model: str
    attempt: int
    outcome: str  # "ok" | error kind
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None


@dataclass
class LLMResult:
    text: str
    finish_reason: str
    provider: str
    model: str
    attempts: list[LLMAttempt] = field(default_factory=list)

    @property
    def input_tokens(self) -> int | None:
        last = self.attempts[-1] if self.attempts else None
        return last.input_tokens if last else None

    @property
    def output_tokens(self) -> int | None:
        last = self.attempts[-1] if self.attempts else None
        return last.output_tokens if last else None

    @property
    def cost_usd(self) -> Decimal | None:
        costs = [a.cost_usd for a in self.attempts if a.cost_usd is not None]
        return sum(costs, Decimal(0)) if costs else None
