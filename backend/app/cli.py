"""Command line entry points: ``python -m app.cli ingest-demo [--enqueue]``."""

import argparse
import asyncio
import json
import logging
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.ingestion.acl import set_collection_acl
from app.ingestion.connectors.upload import UploadConnector, UploadRejected
from app.ingestion.embedding import LocalEmbedder
from app.ingestion.jobs import create_job, record_result
from app.ingestion.pipeline import IngestOutcome, IngestPipeline
from app.logging import configure_logging
from app.models import Chunk, Collection, Document, IngestionJob
from app.worker import verify_embedding_setup

logger = logging.getLogger(__name__)


async def _ensure_collections(
    sf: async_sessionmaker[AsyncSession], manifest: dict[str, Any]
) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    async with sf() as session, session.begin():
        for spec in manifest["collections"]:
            coll = await session.scalar(select(Collection).where(Collection.name == spec["name"]))
            if coll is None:
                coll = Collection(name=spec["name"], description=spec["description"])
                session.add(coll)
                await session.flush()
            await set_collection_acl(session, coll.id, spec["acl_groups"])
            ids[spec["name"]] = coll.id
    return ids


async def _ingest_in_process(
    sf: async_sessionmaker[AsyncSession],
    pipeline: IngestPipeline,
    settings: Settings,
    job_id: uuid.UUID,
    collection_id: uuid.UUID,
    path: Path,
    modified_at: datetime | None,
) -> None:
    content = await asyncio.to_thread(path.read_bytes)
    try:
        connector = UploadConnector(
            path.name, content, max_bytes=settings.upload_max_bytes, modified_at=modified_at
        )
        items = [i async for i in connector.list_items(None)]
        outcome = await pipeline.ingest(collection_id, await connector.fetch(items[0]))
    except UploadRejected as exc:
        outcome = IngestOutcome("failed", None, "failed", error=exc.reason)
    except Exception as exc:
        logger.error("ingest failed", extra={"error": type(exc).__name__})
        outcome = IngestOutcome("failed", None, "failed", error=type(exc).__name__)
    await record_result(sf, job_id, outcome)


async def _enqueue(
    pool: ArqRedis,
    settings: Settings,
    job_id: uuid.UUID,
    collection_id: uuid.UUID,
    path: Path,
    modified: str,
) -> None:
    staging = settings.storage_dir / "staging"
    await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
    staged = staging / f"{uuid.uuid4().hex}{path.suffix.lower()}"
    await asyncio.to_thread(shutil.copyfile, path, staged)
    await pool.enqueue_job(
        "ingest_document", str(job_id), str(collection_id), str(staged), path.name, modified
    )


async def _wait_for_jobs(sf: async_sessionmaker[AsyncSession], ids: list[uuid.UUID]) -> None:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        async with sf() as session:
            statuses = (
                await session.scalars(select(IngestionJob.status).where(IngestionJob.id.in_(ids)))
            ).all()
        if all(s.startswith("completed") for s in statuses):
            return
        await asyncio.sleep(2)
    raise TimeoutError("ingestion jobs did not finish in time")


async def ingest_demo(enqueue: bool) -> dict[str, Any]:
    settings = get_settings()
    manifest = json.loads((settings.demo_corpus_dir / "manifest.json").read_text())
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    embedder = LocalEmbedder(settings)
    await verify_embedding_setup(engine, embedder, settings)
    pipeline = IngestPipeline(sf, embedder, settings)
    collection_ids = await _ensure_collections(sf, manifest)

    docs_by_collection: dict[str, list[dict[str, str]]] = {}
    for d in manifest["documents"]:
        docs_by_collection.setdefault(d["collection"], []).append(d)

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url)) if enqueue else None
    started = time.perf_counter()
    job_ids: list[uuid.UUID] = []
    for name, docs in docs_by_collection.items():
        cid = collection_ids[name]
        job_id = await create_job(sf, cid, "upload", total=len(docs))
        job_ids.append(job_id)
        for d in docs:
            path = settings.demo_corpus_dir / d["path"]
            if pool is not None:
                await _enqueue(pool, settings, job_id, cid, path, d["modified_at"])
            else:
                modified = datetime.fromisoformat(d["modified_at"]).replace(tzinfo=UTC)
                await _ingest_in_process(sf, pipeline, settings, job_id, cid, path, modified)
    if pool is not None:
        await _wait_for_jobs(sf, job_ids)
        await pool.aclose()
    seconds = time.perf_counter() - started

    async with sf() as session:
        jobs = (
            await session.scalars(select(IngestionJob).where(IngestionJob.id.in_(job_ids)))
        ).all()
        actions = {
            k: sum(j.stats.get(k, 0) for j in jobs)
            for k in ("created", "updated", "skipped", "failed")
        }
        by_status = dict(
            (
                await session.execute(
                    select(Document.status, func.count()).group_by(Document.status)
                )
            ).all()
        )
        chunk_count = await session.scalar(select(func.count()).select_from(Chunk))
    await engine.dispose()

    result = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "worker" if enqueue else "in-process",
        "embedding_model": settings.embedding_model,
        "documents_in_manifest": len(manifest["documents"]),
        "actions": actions,
        "documents_by_status": by_status,
        "chunks_total": chunk_count,
        "seconds": round(seconds, 2),
    }
    out_dir = settings.results_dir / "ingestion"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (
        "demo_ingest_run.json"
        if actions["created"] or actions["updated"]
        else "demo_ingest_rerun.json"
    )
    (out_dir / name).write_text(json.dumps(result, indent=2) + "\n")
    return result


async def retrieve(user: str, query: str, no_rerank: bool) -> dict[str, Any]:
    """Dev tool: run hybrid retrieval as a demo user and show every stage's numbers."""
    from app.auth.principal import dev_principal
    from app.retrieval.rerank import CrossEncoderReranker
    from app.retrieval.service import RetrievalService

    settings = get_settings()
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    service = RetrievalService(
        sf,
        LocalEmbedder(settings),
        None if no_rerank else CrossEncoderReranker(settings),
        settings,
    )
    await service.check_pgvector_version()
    result = await service.retrieve(dev_principal(user, settings), query)
    await engine.dispose()
    return {
        "user": result.user_id,
        "collections": [str(c) for c in result.allowed_collection_ids],
        "reranked": result.reranked,
        "counts": result.counts,
        "timings_ms": {k: round(v, 1) for k, v in result.timings_ms.items()},
        "results": [
            {
                "chunk_id": str(c.chunk_id),
                "doc": c.doc_title,
                "source": c.source_uri,
                "page": c.page_start,
                "section": c.section_path,
                "vector_rank": c.vector_rank,
                "keyword_rank": c.keyword_rank,
                "rrf": round(c.rrf_score, 5),
                "rerank_logit": None if c.rerank_logit is None else round(c.rerank_logit, 3),
                "rerank_prob": None if c.rerank_prob is None else round(c.rerank_prob, 3),
                "snippet": c.text[:110],
            }
            for c in result.chunks
        ],
    }


async def ask(user: str, question: str) -> dict[str, Any]:
    """Run the full chat pipeline (retrieval -> LLM -> grounding) as a demo user.

    Needs a provider key (ANTHROPIC_API_KEY / OPENAI_API_KEY). This is also the entry point the
    M5 eval harness uses, so eval and production run the same code.
    """
    from app.auth.principal import dev_principal
    from app.generation.answer import ChatError, ChatService
    from app.llm.factory import build_provider
    from app.retrieval.rerank import CrossEncoderReranker
    from app.retrieval.service import RetrievalService

    settings = get_settings()
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    retrieval = RetrievalService(
        sf, LocalEmbedder(settings), CrossEncoderReranker(settings), settings
    )
    service = ChatService(retrieval, build_provider(settings), settings)

    async def emit(event: str, data: dict[str, Any]) -> None:
        print(f"[{event}] {data.get('stage', '')}", flush=True)

    try:
        async with asyncio.timeout(settings.chat_timeout_seconds):
            resp = await service.run(dev_principal(user, settings), question, None, emit)
        return resp.model_dump(mode="json")
    except TimeoutError:
        return {"error": "timeout", "message": "The request timed out."}
    except ChatError as exc:
        return {"error": exc.code, "message": exc.message}
    finally:
        await engine.dispose()


async def crawl(
    collection: str, url: str, max_pages: int | None, max_depth: int | None
) -> dict[str, Any]:
    """Operator command (run by whoever administers the deployment): crawl a site into a collection.

    The crawler obeys robots.txt, refuses private/loopback/metadata addresses, and stays on the
    seed host. Crawled pages get the collection's ACL.
    """
    from app.ingestion.crawl import crawl_into_collection

    settings = get_settings()
    overrides: dict[str, Any] = {}
    if max_pages:
        overrides["crawl_max_pages"] = max_pages
    if max_depth is not None:
        overrides["crawl_max_depth"] = max_depth
    settings = settings.model_copy(update=overrides)
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    embedder = LocalEmbedder(settings)
    await verify_embedding_setup(engine, embedder, settings)
    async with sf() as session:
        coll = await session.scalar(select(Collection).where(Collection.name == collection))
    if coll is None:
        raise SystemExit(f"collection {collection!r} does not exist")
    pipeline = IngestPipeline(sf, embedder, settings)
    try:
        return await crawl_into_collection(pipeline, sf, settings, coll.id, url)
    finally:
        await engine.dispose()


async def retrieval_smoke() -> dict[str, Any]:
    """Run the demo smoke queries with the real embedder + reranker; write a results file."""
    from app.auth.principal import dev_principal
    from app.retrieval.rerank import CrossEncoderReranker
    from app.retrieval.service import RetrievalService

    settings = get_settings()
    queries = json.loads((settings.demo_corpus_dir / "smoke_queries.json").read_text())
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    service = RetrievalService(
        sf, LocalEmbedder(settings), CrossEncoderReranker(settings), settings
    )
    await service.check_pgvector_version()
    rows = []
    for q in queries:
        res = await service.retrieve(dev_principal(q["as"], settings), q["query"])
        sources = [c.source_uri.removeprefix("upload://") for c in res.chunks]
        expect = q.get("expect")
        rank = None
        if expect:
            rank = next((i for i, s in enumerate(sources, 1) if s == expect), None)
            if rank and q.get("page"):
                page_ok = res.chunks[rank - 1].page_start == q["page"]
            else:
                page_ok = None
        else:
            page_ok = None
        leaked = [s for s in sources if any(f in s for f in q.get("forbid", []))]
        rows.append(
            {
                "query": q["query"],
                "as": q["as"],
                "expect": expect,
                "rank_of_expected": rank,
                "page_ok": page_ok,
                "forbidden_returned": leaked,
                "top_rerank_prob": res.chunks[0].rerank_prob if res.chunks else None,
                "returned": len(res.chunks),
                "timings_ms": {k: round(v, 1) for k, v in res.timings_ms.items()},
            }
        )
    await engine.dispose()
    with_expect = [r for r in rows if r["expect"]]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding_model": settings.embedding_model,
        "rerank_model": settings.rerank_model,
        "queries": len(rows),
        "expected_in_top_n": sum(1 for r in with_expect if r["rank_of_expected"]),
        "expected_total": len(with_expect),
        "forbidden_returned_total": sum(len(r["forbidden_returned"]) for r in rows),
        "results": rows,
    }
    out_dir = settings.results_dir / "retrieval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("ingest-demo", help="ingest demo-corpus into the database")
    demo.add_argument(
        "--enqueue", action="store_true", help="use the Arq worker instead of in-process"
    )
    ask_p = sub.add_parser("ask", help="full chat pipeline as a demo user (needs an LLM key)")
    ask_p.add_argument("question")
    ask_p.add_argument("--as", dest="user", default="alice")
    cr = sub.add_parser("crawl", help="crawl a website into a collection (operator command)")
    cr.add_argument("url")
    cr.add_argument("--collection", required=True)
    cr.add_argument("--max-pages", type=int)
    cr.add_argument("--max-depth", type=int)
    sub.add_parser("retrieval-smoke", help="run demo smoke queries; writes results/retrieval")
    ret = sub.add_parser("retrieve", help="hybrid retrieval as a demo user (dev tool)")
    ret.add_argument("query")
    ret.add_argument("--as", dest="user", default="alice", help="demo user: alice, bob, admin")
    ret.add_argument("--no-rerank", action="store_true")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    if args.command == "ingest-demo":
        print(json.dumps(asyncio.run(ingest_demo(args.enqueue)), indent=2))
    elif args.command == "ask":
        print(json.dumps(asyncio.run(ask(args.user, args.question)), indent=2))
    elif args.command == "crawl":
        print(
            json.dumps(
                asyncio.run(crawl(args.collection, args.url, args.max_pages, args.max_depth)),
                indent=2,
            )
        )
    elif args.command == "retrieval-smoke":
        print(json.dumps(asyncio.run(retrieval_smoke()), indent=2, default=str))
    elif args.command == "retrieve":
        out = asyncio.run(retrieve(args.user, args.query, args.no_rerank))
        print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
