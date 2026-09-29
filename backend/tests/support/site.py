"""A tiny real HTTP server on 127.0.0.1 for crawler tests, plus a fake resolver."""

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Callable
from typing import Any

Route = (
    tuple[int, dict[str, str], bytes]
    | Callable[[dict[str, str]], tuple[int, dict[str, str], bytes]]
)

HTML = {"Content-Type": "text/html; charset=utf-8"}


def page(title: str, body: str = "", links: list[str] | None = None, head: str = "") -> bytes:
    anchors = "".join(f'<a href="{h}">link</a> ' for h in (links or []))
    return (
        f"<html><head><title>{title}</title>{head}</head><body><h1>{title}</h1>"
        f"<p>{body}</p>{anchors}</body></html>"
    ).encode()


class Site:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    def add(
        self,
        path: str,
        body: bytes | str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.routes[path] = (status, headers or HTML, data)

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        path = lines[0].split(" ")[1]
        hdrs = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:] if ln)}
        self.requests.append((path, hdrs))
        route = self.routes.get(
            path.split("?")[0]
            if path.split("?")[0] in self.routes and path not in self.routes
            else path
        )
        if route is None:
            status, headers, body = 404, HTML, b"<html><body>not found</body></html>"
        elif callable(route):
            status, headers, body = route(hdrs)
        else:
            status, headers, body = route
        out = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", "Connection: close"]
        out += [f"{k}: {v}" for k, v in headers.items()]
        writer.write(("\r\n".join(out) + "\r\n\r\n").encode() + body)
        try:
            await writer.drain()
        finally:
            writer.close()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def url(self, path: str = "/", host: str = "docs.test") -> str:
        return f"http://{host}:{self.port}{path}"


class FakeResolver:
    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []
        self.sequence: dict[str, list[list[str]]] = {}

    async def resolve(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        if host in self.sequence and self.sequence[host]:
            return self.sequence[host].pop(0)
        return self.mapping.get(host, [])


LOOPBACK = (ipaddress.ip_network("127.0.0.0/8"),)


async def running_site() -> AsyncIterator[Site]:
    site = Site()
    await site.start()
    yield site
    await site.stop()


_ = Any
