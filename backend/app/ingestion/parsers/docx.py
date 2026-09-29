import io
import re
import zipfile

import defusedxml.ElementTree as safe_et
from defusedxml.common import DefusedXmlException
from docx import Document as load_docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.config import Settings
from app.ingestion.types import Block, ParsedDocument, ParseError

_NESTED_ARCHIVE = (".zip", ".jar", ".7z", ".rar", ".gz", ".tar")
_HEADING_STYLE = re.compile(r"^heading\s+(\d+)$", re.IGNORECASE)


def check_docx_zip(content: bytes, settings: Settings) -> None:
    """Reject zip bombs, path traversal, nested archives and XML entity tricks before parsing."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ParseError("not a valid docx (bad zip)") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > settings.docx_max_entries:
            raise ParseError("docx has too many entries")
        total = sum(i.file_size for i in infos)
        if total > settings.docx_max_uncompressed_bytes:
            raise ParseError("docx uncompressed size too large")
        if total / max(len(content), 1) > settings.docx_max_compression_ratio:
            raise ParseError("docx compression ratio suspicious")
        for info in infos:
            name = info.filename
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise ParseError("docx contains unsafe path")
            if name.lower().endswith(_NESTED_ARCHIVE):
                raise ParseError("docx contains nested archive")
            if name.endswith((".xml", ".rels")):
                # Bounded read: declared sizes can lie.
                with zf.open(info) as fh:
                    data = fh.read(settings.docx_max_uncompressed_bytes + 1)
                if len(data) > settings.docx_max_uncompressed_bytes:
                    raise ParseError("docx part too large")
                try:
                    safe_et.fromstring(data)
                except DefusedXmlException as exc:
                    raise ParseError("docx contains forbidden XML constructs") from exc
                except safe_et.ParseError as exc:
                    raise ParseError("docx contains malformed XML") from exc


def parse_docx(content: bytes, fallback_title: str, settings: Settings) -> ParsedDocument:
    check_docx_zip(content, settings)
    try:
        doc = load_docx(io.BytesIO(content))
    except Exception as exc:
        raise ParseError("could not open docx") from exc

    title = (doc.core_properties.title or "").strip() or fallback_title
    blocks: list[Block] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = " ".join(para.text.split())
            if not text:
                continue
            style = (para.style.name if para.style is not None else "") or ""
            m = _HEADING_STYLE.match(style)
            if style.lower() == "title":
                if title == fallback_title:
                    title = text
                blocks.append(Block("heading", text, level=0))
            elif m:
                blocks.append(Block("heading", text, level=int(m.group(1))))
            else:
                blocks.append(Block("paragraph", text))
        elif tag == "tbl":
            table = Table(child, doc)
            for row in table.rows:
                cells: list[str] = []
                for cell in row.cells:
                    t = " ".join(cell.text.split())
                    if t and (not cells or cells[-1] != t):  # merged cells repeat
                        cells.append(t)
                if cells:
                    blocks.append(Block("paragraph", " | ".join(cells)))
    return ParsedDocument(title=title, blocks=blocks)
