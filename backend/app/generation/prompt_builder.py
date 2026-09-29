import html
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.retrieval.types import RetrievedChunk

_PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class BuiltPrompt:
    system: str
    user: str
    # alias ("S1") -> chunk, server-side only. The model never sees real ids.
    sources: dict[str, RetrievedChunk]


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def build_prompt(question: str, chunks: list[RetrievedChunk]) -> BuiltPrompt:
    """Render sources in rerank order with server-generated metadata.

    Chunk text, titles and the question are untrusted: they are HTML-escaped so they cannot
    close the delimiting tags, and the tag name carries a per-request random suffix.
    """
    tag = secrets.token_hex(4)
    blocks: list[str] = []
    sources: dict[str, RetrievedChunk] = {}
    for i, chunk in enumerate(chunks, start=1):
        alias = f"S{i}"
        sources[alias] = chunk
        page = chunk.page_start if chunk.page_start is not None else ""
        if chunk.page_end is not None and chunk.page_end != chunk.page_start:
            page = f"{chunk.page_start}-{chunk.page_end}"
        date = (
            chunk.source_modified_at.date().isoformat() if chunk.source_modified_at else "unknown"
        )
        warning = ' untrusted_instructions_detected="true"' if chunk.flags.get("injection") else ""
        blocks.append(
            load_prompt("source_block")
            .replace("{tag}", tag)
            .replace("{alias}", alias)
            .replace("{title}", _esc(chunk.doc_title))
            .replace("{section}", _esc(chunk.section_path or ""))
            .replace("{page}", _esc(page))
            .replace("{date}", date)
            .replace("{warning}", warning)
            .replace("{text}", _esc(chunk.text))
            .rstrip("\n")
        )
    user = (
        load_prompt("user")
        .replace("{tag}", tag)
        .replace("{question}", _esc(question))
        .replace("{sources}", "\n".join(blocks))
    )
    return BuiltPrompt(
        system=load_prompt("answer").replace("{tag}", tag), user=user, sources=sources
    )
