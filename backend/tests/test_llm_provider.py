import asyncio
import logging
from decimal import Decimal
from typing import Any

import litellm
import pytest
from litellm import exceptions as lx

from app.config import Settings
from app.generation.schema import ANSWER_JSON_SCHEMA
from app.llm.cost import estimate_cost
from app.llm.provider import LiteLLMProvider, classify
from app.llm.types import LLMError

OK = '{"status":"not_found","answer_sentences":[]}'
SECRET_PROMPT = "TOP-SECRET-QUESTION-TEXT-12345"  # noqa: S105 (test marker, not a credential)
_REAL_ACOMPLETION = litellm.acompletion  # captured before any monkeypatching


class Script:
    """Replaces litellm.acompletion: scripted exceptions / texts; records call kwargs."""

    def __init__(self, items: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
        self.items = list(items)
        self.calls: list[dict[str, Any]] = []
        real = _REAL_ACOMPLETION

        async def fake(**kw: Any) -> Any:
            self.calls.append(kw)
            item = self.items.pop(0)
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return await item()
            return await real(model=kw["model"], messages=kw["messages"], mock_response=item)

        monkeypatch.setattr("app.llm.provider.litellm.acompletion", fake)


def rl() -> Exception:
    return lx.RateLimitError("rate limited " + SECRET_PROMPT, "anthropic", "claude-opus-5-5")


def auth() -> Exception:
    return lx.AuthenticationError("bad key " + SECRET_PROMPT, "anthropic", "claude-opus-5-5")


def srv() -> Exception:
    return lx.InternalServerError("boom", "anthropic", "claude-opus-5-5")


def make(
    monkeypatch: pytest.MonkeyPatch, items: list[Any], **cfg: Any
) -> tuple[LiteLLMProvider, Script, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    settings = Settings(llm_backoff_base_seconds=1.0, llm_max_retries=2, **cfg)
    return LiteLLMProvider(settings, sleep=fake_sleep), Script(items, monkeypatch), sleeps


async def call(p: LiteLLMProvider) -> Any:
    return await p.complete("sys", SECRET_PROMPT, ANSWER_JSON_SCHEMA)


async def test_success_records_provider_model_tokens_latency_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, script, _ = make(monkeypatch, [OK])
    r = await call(p)
    assert r.text == OK and r.finish_reason == "stop"
    assert (r.provider, r.model) == ("anthropic", "claude-opus-5-5")
    a = r.attempts[0]
    assert a.outcome == "ok" and a.input_tokens is not None and a.latency_ms >= 0
    assert a.cost_usd is not None and a.cost_usd > 0  # priced model
    assert script.calls[0]["num_retries"] == 0  # our retry loop, not LiteLLM's


async def test_retries_429_then_succeeds_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, sleeps = make(monkeypatch, [rl(), srv(), OK])
    r = await call(p)
    assert len(script.calls) == 3 and [a.outcome for a in r.attempts] == [
        "http_429",
        "http_500",
        "ok",
    ]
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 0.9  # exponential


async def test_does_not_retry_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, sleeps = make(monkeypatch, [auth()])
    with pytest.raises(LLMError) as exc:
        await call(p)
    assert len(script.calls) == 1 and sleeps == [] and exc.value.status_code == 401
    assert SECRET_PROMPT not in str(exc.value)


async def test_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, _ = make(monkeypatch, [lx.BadRequestError("bad", "anthropic", "m")])
    with pytest.raises(LLMError):
        await call(p)
    assert len(script.calls) == 1


async def test_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, _ = make(monkeypatch, [rl(), rl(), rl()])
    with pytest.raises(LLMError) as exc:
        await call(p)
    assert len(script.calls) == 3 and exc.value.kind == "http_429"


async def test_fallback_used_only_after_retryable_errors_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, script, _ = make(
        monkeypatch, [srv(), srv(), srv(), OK],
        llm_fallback_provider="openai", llm_fallback_model="gpt-fallback",
    )  # fmt: skip
    r = await call(p)
    assert r.provider == "openai" and r.model == "gpt-fallback"
    assert script.calls[-1]["model"] == "openai/gpt-fallback"
    assert len(r.attempts) == 4


async def test_fallback_not_used_for_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, _ = make(
        monkeypatch, [auth()], llm_fallback_provider="openai", llm_fallback_model="gpt-fallback"
    )
    with pytest.raises(LLMError):
        await call(p)
    assert len(script.calls) == 1  # a config mistake must not be hidden by the fallback


async def test_overall_deadline_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang() -> Any:
        await asyncio.sleep(3600)

    p, _, _ = make(monkeypatch, [hang], llm_deadline_seconds=0.2)
    with pytest.raises(LLMError) as exc:
        await call(p)
    assert exc.value.kind == "deadline_exceeded"


async def test_provider_specific_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    p, script, _ = make(monkeypatch, [OK], llm_provider="anthropic")
    await call(p)
    kw = script.calls[0]
    # Current Claude models reject non-default sampling and forced tool use.
    assert "temperature" not in kw and "response_format" not in kw and "tool_choice" not in kw
    assert kw["model"] == "anthropic/claude-opus-5-5"

    p2, script2, _ = make(monkeypatch, [OK], llm_provider="openai")
    await call(p2)
    kw2 = script2.calls[0]
    assert kw2["temperature"] == 0.0 and kw2["response_format"]["json_schema"]["strict"] is True


async def test_no_prompt_completion_or_key_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from pydantic import SecretStr

    key = "test-placeholder-not-a-real-KEYMARKER"
    caplog.set_level(logging.DEBUG)
    p, _, _ = make(monkeypatch, [rl(), OK], anthropic_api_key=SecretStr(key))
    await call(p)
    with pytest.raises(LLMError):
        (p2, _, _) = make(monkeypatch, [auth()], anthropic_api_key=SecretStr(key))
        await call(p2)
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert SECRET_PROMPT not in logged and "KEYMARKER" not in logged and OK not in logged


def test_classification_table() -> None:
    assert classify(rl()) == ("http_429", 429, True)
    assert classify(srv())[2] is True
    assert classify(auth()) == ("http_401", 401, False)
    assert classify(lx.Timeout("t", "anthropic", "m"))[2] is True
    assert classify(lx.APIConnectionError("c", "anthropic", "m"))[2] is True
    assert classify(ValueError("x"))[2] is False


def test_unknown_model_cost_is_none_not_zero() -> None:
    assert estimate_cost("some-unpriced-model", 1000, 1000) is None
    assert estimate_cost("claude-opus-5-5", None, 10) is None
    assert estimate_cost("claude-opus-5-5", 1_000_000, 1_000_000) == Decimal("24.00")
