import uuid
from pathlib import Path, PurePath

from app.config import Settings


def store_raw_file(
    settings: Settings, collection_id: uuid.UUID, checksum: str, filename: str, content: bytes
) -> str:
    """Store under a server-generated, content-addressed name.

    Returns a path relative to storage_dir. Blocking: call via asyncio.to_thread.
    """
    ext = PurePath(filename).suffix.lower()
    rel = Path(str(collection_id)) / f"{checksum}{ext}"
    target = settings.storage_dir / rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(content)
        tmp.replace(target)
    return str(rel)
