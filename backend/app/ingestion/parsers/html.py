import re

import trafilatura
from lxml import etree

from app.ingestion.parsers.text import decode_text
from app.ingestion.types import Block, ParsedDocument, ParseError

_HEAD_LEVEL = re.compile(r"h([1-6])")


def _text(el: etree._Element) -> str:
    return " ".join("".join(str(t) for t in el.itertext()).split())


def parse_html(content: bytes, fallback_title: str) -> ParsedDocument:
    html = decode_text(content)
    # Never fetches external resources; trafilatura only parses the given string.
    xml = trafilatura.extract(
        html, output_format="xml", include_formatting=False, favor_recall=True
    )
    title = fallback_title
    meta = trafilatura.extract_metadata(html)
    if meta is not None and meta.title:
        title = meta.title.strip()
    if not xml:
        return ParsedDocument(title=title)
    try:
        root = etree.fromstring(
            xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except etree.XMLSyntaxError as exc:
        raise ParseError("could not parse extracted html") from exc

    main = root.find("main")
    blocks: list[Block] = []
    for child in main if main is not None else []:
        if child.tag == "head":
            m = _HEAD_LEVEL.fullmatch(child.get("rend", "h2"))
            text = _text(child)
            if text:
                blocks.append(Block("heading", text, level=int(m.group(1)) if m else 2))
        elif child.tag == "list":
            blocks.extend(Block("paragraph", t) for t in (_text(i) for i in child) if t)
        elif child.tag == "table":
            for row in child:
                t = " | ".join(c for c in (_text(cell) for cell in row) if c)
                if t:
                    blocks.append(Block("paragraph", t))
        else:
            t = _text(child)
            if t:
                blocks.append(Block("paragraph", t))
    return ParsedDocument(title=title, blocks=blocks)
