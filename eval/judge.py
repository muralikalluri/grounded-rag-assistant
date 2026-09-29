"""LLM judge used only when a real provider is configured (never in test mode).

Verdicts are accepted only if ``evidence`` is an exact substring of the text being judged
(same trick as the grounding validator), and answers that look like prompt injection are failed
outright. The judge never sees expected sources, only the single fact / quote it is grading.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.ingestion.flags import compile_patterns, injection_matches
from app.llm.provider import LLMProvider
from eval.metrics import norm

PROMPT_DIR = Path(__file__).parent / "prompts"
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "evidence": {"type": "string"}},
    "required": ["supported", "evidence"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    supported: bool
    reason: str  # ok | no_evidence | bad_json | injection_like | not_supported
    cost_usd: float | None = None
    calls: int = 1


def prompt_hash(name: str) -> str:
    return hashlib.sha256((PROMPT_DIR / f"{name}.md").read_bytes()).hexdigest()


def _escape(text: str) -> str:
    # Keep the delimiting tags unforgeable.
    return text.replace("<", "&lt;").replace(">", "&gt;")


class Judge:
    def __init__(self, provider: LLMProvider, settings: Settings) -> None:
        self._p = provider
        self._injection = compile_patterns(settings.injection_patterns)

    async def _ask(self, template: str, fill: dict[str, str], evidence_in: str) -> Verdict:
        prompt = (PROMPT_DIR / f"{template}.md").read_text(encoding="utf-8")
        for k, v in fill.items():
            prompt = prompt.replace("{" + k + "}", _escape(v))
        system = (PROMPT_DIR / "judge_system.md").read_text(encoding="utf-8").strip()
        result = await self._p.complete(system, prompt, _SCHEMA)
        cost = float(result.cost_usd) if result.cost_usd is not None else None
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.text.strip())
        try:
            data = json.loads(body[body.find("{") : body.rfind("}") + 1])
            supported, evidence = bool(data["supported"]), str(data.get("evidence", ""))
        except (ValueError, KeyError, TypeError):
            return Verdict(False, "bad_json", cost)
        if not supported:
            return Verdict(False, "not_supported", cost)
        if not evidence or norm(evidence) not in norm(evidence_in):
            return Verdict(False, "no_evidence", cost)  # a verdict without a real quote is void
        return Verdict(True, "ok", cost)

    async def fact_supported(self, answer: str, fact: str) -> Verdict:
        if injection_matches(answer, self._injection):
            return Verdict(False, "injection_like", None, 0)
        return await self._ask("judge_fact", {"fact": fact, "answer": answer}, answer)

    async def citation_supported(self, sentence: str, quote: str) -> Verdict:
        if injection_matches(sentence, self._injection):
            return Verdict(False, "injection_like", None, 0)
        return await self._ask("judge_citation", {"sentence": sentence, "quote": quote}, quote)
