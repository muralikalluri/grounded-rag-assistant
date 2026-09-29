from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from app.ingestion.types import RawDocument, SourceItem


class Connector(Protocol):
    name: str

    def list_items(self, since: datetime | None) -> AsyncIterator[SourceItem]: ...

    async def fetch(self, item: SourceItem) -> RawDocument: ...
