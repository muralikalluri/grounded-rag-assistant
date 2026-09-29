import re

from app.ingestion.types import Block, ParsedDocument, ParseError

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def parse_text(content: bytes, fallback_title: str, *, markdown: bool) -> ParsedDocument:
    try:
        text = decode_text(content)
    except Exception as exc:  # pragma: no cover - latin-1 never fails
        raise ParseError("could not decode text") from exc

    blocks: list[Block] = []
    title = fallback_title
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("paragraph", " ".join(para)))
            para.clear()

    for line in text.splitlines():
        stripped = line.strip()
        m = _HEADING.match(stripped) if markdown else None
        if m:
            flush()
            level = len(m.group(1))
            blocks.append(Block("heading", m.group(2), level=level))
            if level == 1 and title == fallback_title:
                title = m.group(2)
        elif not stripped:
            flush()
        else:
            para.append(stripped)
    flush()
    return ParsedDocument(title=title, blocks=blocks)
