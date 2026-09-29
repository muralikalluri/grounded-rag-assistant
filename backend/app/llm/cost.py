"""Price table for cost estimates. Unknown models return None, never 0."""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Anthropic first-party list prices, USD per 1M tokens (input, output), from the Claude API
# model overview cached 2026-09-25. OpenAI is intentionally absent: add rows you have verified.
PRICES_AS_OF = "2026-09-25"
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-fable-5-1": (Decimal("10.00"), Decimal("50.00")),
}


def estimate_cost(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        if price is None:
            logger.warning("no price for model", extra={"model": model})
        return None
    return (Decimal(input_tokens) * price[0] + Decimal(output_tokens) * price[1]) / Decimal(
        1_000_000
    )
