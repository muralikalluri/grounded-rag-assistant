import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.api.deps import get_principal
from app.auth.principal import Principal
from app.config import Settings, get_settings
from app.preview.service import BadRequest, NotFound, PreviewService

router = APIRouter(prefix="/api/documents", tags=["preview"])

# Content of ACL-protected documents: never cached by shared caches or stored on disk by browsers.
_PRIVATE = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Vary": "Cookie, X-Dev-User, Authorization",
}


def get_preview_service(request: Request) -> PreviewService:
    svc: PreviewService | None = getattr(request.app.state, "preview_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Preview service is not ready")
    return svc


def _not_found() -> HTTPException:
    # Identical for: missing, forbidden, mismatched ids, wrong page, unreadable file.
    return HTTPException(status_code=404, detail="Not found", headers=_PRIVATE)


class ChunkOut(BaseModel):
    chunk_id: str
    document_id: str
    doc_title: str
    section_path: str | None
    page_start: int | None
    page_end: int | None
    text: str
    is_pdf: bool
    source_date: str | None
    flags: list[str]


class SegmentOut(BaseModel):
    page: int
    rects: list[tuple[float, float, float, float]]


class LocateOut(BaseModel):
    found: bool
    ambiguous: bool
    method: str
    segments: list[SegmentOut]


@router.get("/{document_id}/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    svc: Annotated[PreviewService, Depends(get_preview_service)],
) -> Any:
    try:
        view = await svc.get_chunk(principal, document_id, chunk_id)
    except NotFound:
        raise _not_found() from None
    response.headers.update(_PRIVATE)
    return ChunkOut(
        chunk_id=str(view.chunk_id), document_id=str(view.document_id),
        doc_title=view.doc_title, section_path=view.section_path,
        page_start=view.page_start, page_end=view.page_end, text=view.text,
        is_pdf=view.is_pdf, source_date=view.source_date,
        flags=["injection_flagged"] if view.flags.get("injection") else [],
    )  # fmt: skip


@router.get("/{document_id}/chunks/{chunk_id}/locate", response_model=LocateOut)
async def locate(
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    svc: Annotated[PreviewService, Depends(get_preview_service)],
    start: Annotated[int, Query(ge=0)],
    end: Annotated[int, Query(ge=1)],
) -> Any:
    """Where on the cited page(s) the quote (chunk text ``start:end``) is. Rects are 0..1."""
    try:
        result = await svc.locate(principal, document_id, chunk_id, start, end)
    except NotFound:
        raise _not_found() from None
    except BadRequest:
        raise HTTPException(status_code=422, detail="Invalid range", headers=_PRIVATE) from None
    response.headers.update(_PRIVATE)
    return LocateOut(
        found=result.found, ambiguous=result.ambiguous, method=result.method,
        segments=[SegmentOut(page=s.page, rects=s.rects) for s in result.segments],
    )  # fmt: skip


@router.get(
    "/{document_id}/chunks/{chunk_id}/page",
    responses={200: {"content": {"image/png": {}}}},
)
async def page_image(
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    svc: Annotated[PreviewService, Depends(get_preview_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1)],
) -> Response:
    """PNG of one page of the cited chunk. Only pages inside the chunk's own page range."""
    if page > settings.pdf_max_pages:
        raise _not_found()
    try:
        png = await svc.page_png(principal, document_id, chunk_id, page)
    except NotFound:
        raise _not_found() from None
    return Response(
        content=png,
        media_type="image/png",
        headers={**_PRIVATE, "Content-Disposition": "inline"},
    )
