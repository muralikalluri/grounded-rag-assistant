import hashlib
import hmac
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from app.auth.principal import Principal
from app.config import Settings
from app.generation.abstention import GateDecision, retrieval_gate
from app.generation.grounding import ValidationResult, validate_answer
from app.generation.prompt_builder import BuiltPrompt, build_prompt
from app.generation.response import (
    ChatResponse,
    CitationOut,
    GroundingOut,
    RelatedOut,
    SentenceOut,
)
from app.generation.schema import ANSWER_JSON_SCHEMA, InvalidOutput, RawAnswer, parse_model_output
from app.llm.provider import LLMProvider
from app.llm.types import LLMError, LLMResult
from app.retrieval.service import RetrievalService
from app.retrieval.types import RetrievedChunk

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class ChatError(Exception):
    """A failure the client should see as an error event (not as a "not found" answer)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _source_url(chunk: RetrievedChunk) -> str | None:
    return chunk.source_uri if chunk.source_uri.startswith(("http://", "https://")) else None


class ChatService:
    def __init__(
        self, retrieval: RetrievalService, provider: LLMProvider, settings: Settings
    ) -> None:
        self._retrieval = retrieval
        self._provider = provider
        self._s = settings
        self._generator = getattr(provider, "generator", "llm")

    async def run(
        self,
        principal: Principal,
        question: str,
        collection_ids: list[uuid.UUID] | None,
        emit: Emit,
        request_id: str | None = None,
    ) -> ChatResponse:
        rid = request_id or uuid.uuid4().hex
        t_start = time.perf_counter()
        await emit("status", {"stage": "retrieving", "request_id": rid})
        try:
            retrieved = await self._retrieval.retrieve(principal, question, collection_ids)
        except Exception as exc:
            # Type only: DB errors carry bound parameters, which include the question text.
            logger.error("retrieval failed", extra={"request_id": rid, "error": type(exc).__name__})
            raise ChatError("retrieval_failed", "Search is temporarily unavailable.") from None

        chunks = retrieved.chunks
        if self._s.flagged_chunk_policy == "drop":
            chunks = [c for c in chunks if not c.flags.get("injection")]
        gate = retrieval_gate(replace(retrieved, chunks=chunks), self._s)
        if not gate.proceed:
            related = chunks if gate.reason == "low_relevance" else []
            resp = self._not_found(rid, gate.reason or "abstained", gate, related)
            self._log(rid, principal, question, chunks, resp, None, None, t_start)
            return resp

        await emit("status", {"stage": "generating", "request_id": rid})
        prompt = build_prompt(question, chunks)
        llm_results: list[LLMResult] = []
        answer, fail_reason = await self._generate(prompt, llm_results, rid)
        if answer is None:
            resp = self._not_found(rid, fail_reason or "generation_invalid", gate, chunks)
            self._log(rid, principal, question, chunks, resp, None, llm_results, t_start)
            return resp

        await emit("status", {"stage": "validating", "request_id": rid})
        validation = validate_answer(answer, prompt.sources, self._s)
        resp = self._build(rid, validation, gate, chunks)
        self._log(rid, principal, question, chunks, resp, validation, llm_results, t_start)
        return resp

    # ------------------------------------------------------------------ generation
    async def _generate(
        self, prompt: BuiltPrompt, sink: list[LLMResult], rid: str
    ) -> tuple[RawAnswer | None, str | None]:
        """One call, plus one retry if the output is malformed. Outages raise ChatError."""
        for attempt in range(1, self._s.generation_invalid_retries + 2):
            try:
                result = await self._provider.complete(
                    prompt.system, prompt.user, ANSWER_JSON_SCHEMA
                )
            except LLMError as exc:
                logger.warning("llm unavailable", extra={"kind": exc.kind, "request_id": rid})
                raise ChatError("llm_unavailable", "The answer service is unavailable.") from None
            sink.append(result)
            if result.finish_reason == "length":
                return None, "generation_truncated"  # never parse a cut-off answer
            if result.finish_reason in ("content_filter", "refusal"):
                return None, "model_refused"
            try:
                return parse_model_output(result.text), None
            except InvalidOutput as exc:
                logger.info(
                    "invalid model output",
                    extra={"attempt": attempt, "detail": str(exc), "request_id": rid},
                )
        return None, "generation_invalid"

    # -------------------------------------------------------------------- responses
    def _related(self, chunks: list[RetrievedChunk]) -> list[RelatedOut]:
        seen: set[tuple[str, int | None]] = set()
        out: list[RelatedOut] = []
        for c in chunks:
            key = (c.doc_title, c.page_start)
            if key in seen:
                continue
            seen.add(key)
            out.append(RelatedOut(doc_title=c.doc_title, page_start=c.page_start,
                                  section_path=c.section_path))  # fmt: skip
            if len(out) >= self._s.possibly_related_max:
                break
        return out

    def _grounding(
        self, gate: GateDecision, total: int, dropped: int, reason: str | None,
        drop_reasons: dict[str, int],
    ) -> GroundingOut:  # fmt: skip
        stage1: dict[str, Any] = {"gate": gate.gate}
        if self._s.expose_stage1_scores:
            stage1.update(
                top_prob=gate.top_prob,
                second_prob=gate.second_prob,
                n_chunks=gate.n_chunks,
                threshold=gate.threshold,
            )
        return GroundingOut(
            sentences_total=total,
            sentences_dropped=dropped,
            abstained_reason=reason,
            drop_reasons=drop_reasons,
            stage1=stage1,
        )

    def _not_found(
        self, rid: str, reason: str, gate: GateDecision, related: list[RetrievedChunk]
    ) -> ChatResponse:
        return ChatResponse(
            request_id=rid,
            generator=self._generator,
            status="not_found",
            message=self._s.not_found_message,
            possibly_related=self._related(related),
            grounding=self._grounding(gate, 0, 0, reason, {}),
        )

    def _build(
        self, rid: str, v: ValidationResult, gate: GateDecision, chunks: list[RetrievedChunk]
    ) -> ChatResponse:
        if v.status == "not_found":
            resp = self._not_found(rid, v.abstain_reason or "ungrounded", gate, chunks)
            resp.grounding.sentences_total = v.total
            resp.grounding.sentences_dropped = v.dropped
            resp.grounding.drop_reasons = v.drop_reasons
            return resp
        numbers: dict[tuple[uuid.UUID, int, int], int] = {}
        citations: list[CitationOut] = []
        sentences: list[SentenceOut] = []
        for s in v.sentences:
            ns: list[int] = []
            for c in s.citations:
                key = (c.chunk.chunk_id, c.char_start, c.char_end)
                if key not in numbers:
                    numbers[key] = len(numbers) + 1
                    ch = c.chunk
                    citations.append(
                        CitationOut(
                            n=numbers[key],
                            chunk_id=str(ch.chunk_id),
                            document_id=str(ch.document_id),
                            doc_title=ch.doc_title,
                            page_start=ch.page_start,
                            page_end=ch.page_end,
                            section_path=ch.section_path,
                            quote=c.quote,
                            char_start=c.char_start,
                            char_end=c.char_end,
                            source_date=ch.source_modified_at.date().isoformat()
                            if ch.source_modified_at
                            else None,
                            source_url=_source_url(ch),
                            flags=["injection_flagged"] if ch.flags.get("injection") else [],
                        )
                    )
                ns.append(numbers[key])
            sentences.append(SentenceOut(text=s.text, citations=ns, flags=s.flags))
        return ChatResponse(
            request_id=rid,
            generator=self._generator,
            status="answered",
            sentences=sentences,
            citations=citations,
            grounding=self._grounding(gate, v.total, v.dropped, None, v.drop_reasons),
        )

    # ---------------------------------------------------------------------- logging
    def _log(
        self,
        rid: str,
        principal: Principal,
        question: str,
        provided: list[RetrievedChunk],
        resp: ChatResponse,
        validation: ValidationResult | None,
        llm: list[LLMResult] | None,
        t_start: float,
    ) -> None:
        """Audit-style log: ids, counts and an HMAC of the question. Never text."""
        key = self._s.audit_hmac_key.get_secret_value().encode()
        attempts = [a for r in (llm or []) for a in r.attempts]
        costs = [a.cost_usd for a in attempts if a.cost_usd is not None]
        logger.info(
            "chat",
            extra={
                "request_id": rid,
                "user": principal.user_id,
                "question_hmac": hmac.new(key, question.encode(), hashlib.sha256).hexdigest()[:16],
                "provided_chunk_ids": [str(c.chunk_id) for c in provided],
                "cited_chunk_ids": sorted({c.chunk_id for c in resp.citations}),
                "status": resp.status,
                "abstained_reason": resp.grounding.abstained_reason,
                "sentences_total": resp.grounding.sentences_total,
                "sentences_dropped": resp.grounding.sentences_dropped,
                "drop_reasons": resp.grounding.drop_reasons,
                "gate": resp.grounding.stage1["gate"],
                "llm_calls": len(attempts),
                "llm_model": llm[-1].model if llm else None,
                "input_tokens": sum(a.input_tokens or 0 for a in attempts) if attempts else None,
                "output_tokens": sum(a.output_tokens or 0 for a in attempts) if attempts else None,
                "cost_usd": str(sum(costs)) if costs else None,
                "total_ms": round((time.perf_counter() - t_start) * 1000, 1),
                "validated": validation is not None,
            },
        )
