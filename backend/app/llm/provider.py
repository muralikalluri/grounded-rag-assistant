"""LLM provider layer on LiteLLM.

- LiteLLM's own retries are disabled; ours are testable and bounded by one overall deadline.
- Retries only on 408/409/429/5xx, timeouts and connection errors. Never on auth/400/context/
  content-policy errors. The fallback model is tried only after retryable failures run out.
  A 401 or a 404 "model not found" therefore does NOT fall back: a wrong key or model id is a
  configuration mistake and must be visible, not hidden by a second provider.
- Prompts, completions and keys are never logged; exceptions are reduced to kind + status.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import litellm
from litellm import exceptions as lx

from app.config import Settings
from app.llm.cost import estimate_cost
from app.llm.types import LLMAttempt, LLMError, LLMResult

logger = logging.getLogger(__name__)

# LiteLLM's own loggers print full request bodies (prompts) at DEBUG/INFO: keep them quiet.
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(_name).setLevel(logging.WARNING)
litellm.turn_off_message_logging = True
litellm.suppress_debug_info = True
litellm.drop_params = False

_RETRYABLE_STATUS = {408, 409, 429}
_JSON_OBJECT_PROVIDERS = {"gemini", "groq"}


class LLMProvider(Protocol):
    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult: ...


@dataclass(frozen=True)
class Target:
    provider: str  # "openai" | "anthropic" | "gemini" | "groq"
    model: str


def classify(exc: BaseException) -> tuple[str, int | None, bool]:
    """(kind, status_code, retryable) without ever touching the exception message."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, lx.Timeout | TimeoutError | asyncio.TimeoutError):
        return "timeout", status, True
    if isinstance(exc, lx.APIConnectionError) and not isinstance(exc, lx.Timeout):
        return "connection_error", status, True
    if isinstance(status, int):
        if status in _RETRYABLE_STATUS or status >= 500:
            return f"http_{status}", status, True
        return f"http_{status}", status, False
    if isinstance(exc, lx.AuthenticationError | lx.PermissionDeniedError | lx.BadRequestError):
        return type(exc).__name__, status, False
    return type(exc).__name__, status, False


def _supports_temperature(provider: str) -> bool:
    # Current Claude models reject non-default sampling parameters with a 400.
    return provider != "anthropic"


class LiteLLMProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        extra_completion_kwargs: dict[str, Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._s = settings
        self._extra = extra_completion_kwargs or {}
        self._sleep = sleep
        if settings.llm_provider == "extractive":
            raise ValueError("use ExtractiveProvider (app.llm.factory) for llm_provider=extractive")
        primary = Target(settings.llm_provider, settings.llm_model_for(settings.llm_provider) or "")
        self._targets = [primary]
        if settings.llm_fallback_provider and settings.llm_fallback_model:
            self._targets.append(
                Target(settings.llm_fallback_provider, settings.llm_fallback_model)
            )

    def _kwargs(
        self, target: Target, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        s = self._s
        kw: dict[str, Any] = {
            "model": f"{target.provider}/{target.model}",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": s.llm_max_tokens,
            "timeout": s.llm_timeout_seconds,
            "num_retries": 0,
        }
        if _supports_temperature(target.provider):
            kw["temperature"] = s.llm_temperature
        if target.provider == "openai":
            kw["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "grounded_answer", "schema": schema, "strict": True},
            }
            if s.openai_api_key:
                kw["api_key"] = s.openai_api_key.get_secret_value()
        elif target.provider in _JSON_OBJECT_PROVIDERS:
            # Dev-only free tiers: strict json_schema support varies by model, so ask for a JSON
            # object (the prompt carries the schema) and validate with Pydantic like Anthropic.
            kw["response_format"] = {"type": "json_object"}
            key = s.gemini_api_key if target.provider == "gemini" else s.groq_api_key
            if key:
                kw["api_key"] = key.get_secret_value()
        elif s.anthropic_api_key:
            # Anthropic: JSON is requested in the prompt and validated by Pydantic. LiteLLM's
            # json_schema mode uses forced tool_choice, which current Claude models reject.
            kw["api_key"] = s.anthropic_api_key.get_secret_value()
        kw.update(self._extra)
        return kw

    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult:
        attempts: list[LLMAttempt] = []
        deadline = time.monotonic() + self._s.llm_deadline_seconds
        last: LLMError | None = None
        for target in self._targets:
            for attempt in range(1, self._s.llm_max_retries + 2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMError("deadline_exceeded") from None
                t0 = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(
                        litellm.acompletion(**self._kwargs(target, system, user, json_schema)),
                        timeout=remaining,
                    )
                except Exception as exc:
                    kind, status, retryable = classify(exc)
                    latency = (time.perf_counter() - t0) * 1000
                    attempts.append(
                        LLMAttempt(target.provider, target.model, attempt, kind, latency)
                    )
                    logger.warning(
                        "llm call failed",
                        extra={"provider": target.provider, "model": target.model,
                               "attempt": attempt, "kind": kind, "status": status},
                    )  # fmt: skip
                    last = LLMError(kind, status)
                    if not retryable:
                        raise last from None
                    if attempt <= self._s.llm_max_retries:
                        delay = min(
                            self._s.llm_backoff_base_seconds * 2 ** (attempt - 1),
                            self._s.llm_backoff_max_seconds,
                        ) * (0.5 + random.random() / 2)  # noqa: S311
                        if time.monotonic() + delay >= deadline:
                            raise LLMError("deadline_exceeded") from None
                        await self._sleep(delay)
                    continue
                latency = (time.perf_counter() - t0) * 1000
                usage = getattr(resp, "usage", None)
                tin = getattr(usage, "prompt_tokens", None)
                tout = getattr(usage, "completion_tokens", None)
                attempts.append(
                    LLMAttempt(
                        target.provider,
                        target.model,
                        attempt,
                        "ok",
                        latency,
                        tin,
                        tout,
                        estimate_cost(target.model, tin, tout),
                    )  # fmt: skip
                )
                choice = resp.choices[0]
                return LLMResult(
                    text=choice.message.content or "",
                    finish_reason=str(choice.finish_reason or ""),
                    provider=target.provider,
                    model=target.model,
                    attempts=attempts,
                )
            # retryable failures exhausted for this target -> fall through to the fallback
        raise last or LLMError("no_target")
