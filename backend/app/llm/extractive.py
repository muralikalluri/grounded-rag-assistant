"""Offline TEST MODE provider. It is NOT a language model.

It answers by quoting the source sentence that shares the most content words with the question,
and returns the same JSON shape a real model would, so the schema parser, grounding validator
and citation path all run for real. Used for e2e/CI and for demos without an API key.
Refused when app_env=prod; results must never be presented as model quality (M5 eval refuses).
"""

import html
import json
import re
from typing import Any

from app.generation.grounding import _content_words, normalise
from app.ingestion.chunker import split_sentences
from app.llm.types import LLMAttempt, LLMResult

_QUESTION = re.compile(r"<question_[0-9a-f]+>\n?(.*?)\n?</question_[0-9a-f]+>", re.S)
_SOURCE = re.compile(r'<source_[0-9a-f]+ id="(S\d+)"[^>]*>\n?(.*?)\n?</source_[0-9a-f]+>', re.S)
_MIN_SHARED_WORDS = 2
_MAX_SOURCES = 3
_MAX_SENTENCES = 2


class ExtractiveProvider:
    generator = "extractive"

    async def complete(self, system: str, user: str, json_schema: dict[str, Any]) -> LLMResult:
        q = _QUESTION.search(user)
        question_words = _content_words(normalise(html.unescape(q.group(1)))[0]) if q else set()
        scored: list[tuple[int, int, str, str]] = []
        for order, (alias, body) in enumerate(_SOURCE.findall(user)[:_MAX_SOURCES]):
            for sentence in split_sentences(html.unescape(body)):
                score = len(question_words & _content_words(normalise(sentence)[0]))
                if score >= _MIN_SHARED_WORDS:
                    scored.append((-score, order, alias, sentence))
        scored.sort()  # best score first; ties keep source order
        picked: list[tuple[str, str]] = []
        for _, _, alias, sentence in scored:
            if all(sentence != s for _, s in picked):
                picked.append((alias, sentence))
            if len(picked) == _MAX_SENTENCES:
                break
        payload: dict[str, Any] = {
            "status": "answered" if picked else "not_found",
            "answer_sentences": [
                {"text": s, "citations": [{"source": a, "quote": s}]} for a, s in picked
            ],
        }
        attempt = LLMAttempt("extractive", "extractive-v1", 1, "ok", 0.0, None, None, None)
        return LLMResult(json.dumps(payload), "stop", "extractive", "extractive-v1", [attempt])
