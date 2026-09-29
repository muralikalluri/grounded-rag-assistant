from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import chat, health, preview
from app.auth.oidc import OidcVerifier
from app.config import get_settings
from app.generation.answer import ChatService
from app.ingestion.embedding import LocalEmbedder
from app.llm.factory import build_provider
from app.logging import configure_logging
from app.preview.service import PreviewService
from app.retrieval.rerank import CrossEncoderReranker
from app.retrieval.service import RetrievalService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    retrieval = RetrievalService(
        session_factory,
        LocalEmbedder(settings),
        CrossEncoderReranker(settings),
        settings,
    )
    await retrieval.check_pgvector_version()
    app.state.oidc = OidcVerifier(settings) if settings.oidc_issuer else None
    app.state.chat_service = ChatService(retrieval, build_provider(settings), settings)
    app.state.preview_service = PreviewService(session_factory, settings)
    yield
    if app.state.oidc is not None:
        await app.state.oidc.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(preview.router)
    return app


app = create_app()
