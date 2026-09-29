import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_chat_service, get_principal
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.generation.answer import ChatError, ChatService

_LIMITS = get_settings()  # request-size limits are fixed when the model class is defined
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_LIMITS.query_max_chars)
    # Accepted for forward compatibility; query rewriting from history is not implemented yet.
    conversation_id: str | None = Field(default=None, max_length=_LIMITS.chat_conversation_id_max)
    collection_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=_LIMITS.chat_max_collection_ids
    )


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ChatService, Depends(get_chat_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Server-sent events: ``status`` progress events, then exactly one ``answer`` or ``error``.

    Unvalidated model tokens are never streamed. 401/422 are ordinary HTTP errors returned
    before the stream starts. Clients must use fetch (EventSource cannot POST).
    """
    request_id = uuid.uuid4().hex
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def worker() -> None:
        try:
            async with asyncio.timeout(settings.chat_timeout_seconds):
                resp = await service.run(
                    principal, body.question, body.collection_ids, emit, request_id
                )
            await queue.put(("answer", resp.model_dump(mode="json")))
        except ChatError as exc:
            await queue.put(("error", {"code": exc.code, "message": exc.message,
                                       "request_id": request_id}))  # fmt: skip
        except TimeoutError:
            await queue.put(("error", {"code": "timeout", "message": "The request timed out.",
                                       "request_id": request_id}))  # fmt: skip
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Type only: tracebacks can embed bound SQL parameters (the question text).
            logger.error(
                "chat failed", extra={"request_id": request_id, "error": type(exc).__name__}
            )
            await queue.put(("error", {"code": "internal_error", "message": "Something went wrong.",
                                       "request_id": request_id}))  # fmt: skip
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=settings.chat_heartbeat_seconds
                    )
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                if await request.is_disconnected():
                    break
                yield sse(*item)
        finally:
            if not task.done():
                task.cancel()  # client went away: stop paying for the LLM call
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
