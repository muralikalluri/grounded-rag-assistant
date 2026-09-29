"""SSRF protection and a pinned-IP HTTP fetcher for the web crawler.

Rules: http/https only; no userinfo; port allowlist; EVERY resolved address must be globally
routable (blocks loopback, RFC1918, link-local incl. cloud metadata, CGNAT, ULA, multicast,
IPv4-mapped/NAT64/6to4 forms of those); the request then goes to the validated IP itself
(Host header and TLS SNI keep the hostname), so a DNS answer that changes between "check" and
"connect" (rebinding) cannot redirect us. Redirects are never followed by the HTTP client: each
hop is re-validated here. Proxies from the environment are ignored.
"""

import asyncio
import inspect
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


class FetchError(Exception):
    """Fetch refused or failed. ``reason`` is a short code; never contains page content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> list[str]: ...


class SystemResolver:
    async def resolve(self, host: str, port: int) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise FetchError("dns_failure") from None
        return sorted({str(info[4][0]) for info in infos})


@dataclass(frozen=True)
class IpPolicy:
    """Which addresses may be contacted. Default: only globally routable ones.

    ``allow`` is an explicit allowlist of networks that bypass the check. It is set only through
    this constructor (tests) or by ``crawl_allow_private_networks`` (refused in prod).
    """

    allow: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()

    @classmethod
    def allow_private(cls) -> "IpPolicy":
        return cls(
            (
                ipaddress.ip_network("127.0.0.0/8"),
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("::1/128"),
                ipaddress.ip_network("fc00::/7"),
            )
        )

    def permits(self, ip_text: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        if any(ip in net for net in self.allow if net.version == ip.version):
            return True
        return _is_public(ip)


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # Forms that embed an IPv4 address must be judged by the embedded address.
        if ip.ipv4_mapped is not None:
            return _is_public(ip.ipv4_mapped)
        if ip in _NAT64:
            return _is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in _SIXTOFOUR:
            return _is_public(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
        if ip in _TEREDO:
            return False
        if int(ip) >> 32 == 0:
            return False  # ::/96 (IPv4-compatible, "::127.0.0.1") and ::1, ::: never routable
    return bool(ip.is_global) and not ip.is_multicast


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    headers: dict[str, str]
    body: bytes
    redirected_from: tuple[str, ...] = field(default_factory=tuple)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def check_url(url: str, allowed_ports: Iterable[int]) -> SplitResult:
    """Syntactic checks before any DNS or network activity."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("bad_url") from None
    if parts.scheme not in ("http", "https"):
        raise FetchError("scheme_not_allowed")
    if not parts.hostname:
        raise FetchError("bad_url")
    if parts.username is not None or parts.password is not None:
        raise FetchError("credentials_in_url")
    effective = port or (443 if parts.scheme == "https" else 80)
    if effective not in set(allowed_ports):
        raise FetchError("port_not_allowed")
    return parts


class SafeFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        allowed_ports: Iterable[int] = (80, 443),
        policy: IpPolicy | None = None,
        resolver: Resolver | None = None,
        timeout: float = 10.0,
        max_bytes: int = 5 * 1024 * 1024,
        max_redirects: int = 5,
        deadline: Callable[[], float] | None = None,
        max_request_seconds: float = 30.0,
    ) -> None:
        self._max_request = max_request_seconds
        self._ua = user_agent
        self._ports = tuple(allowed_ports)
        self._policy = policy or IpPolicy()
        self._resolver = resolver or SystemResolver()
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._deadline = deadline
        # trust_env=False: no HTTP(S)_PROXY, no netrc. follow_redirects=False: we check each hop.
        self._http = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, verify=True
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _pin(self, parts: SplitResult) -> tuple[str, int]:
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            literal = ipaddress.ip_address(host)
            addrs = [str(literal)]
        except ValueError:
            addrs = await self._resolver.resolve(host, port)
        if not addrs:
            raise FetchError("dns_failure")
        # ALL answers must be permitted: a mixed public/private answer is an attack, not a hint.
        if not all(self._policy.permits(a) for a in addrs):
            raise FetchError("address_not_allowed")
        return addrs[0], port

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        hop_allowed: Callable[[str], bool | Awaitable[bool]] | None = None,
    ) -> FetchResult:
        """GET with manual, validated redirects. ``hop_allowed`` (sync or async) can veto a hop.

        The whole exchange (all hops, headers and streamed body) is bounded by the crawl deadline
        and by ``max_request_seconds``: a server that drips bytes cannot hold the crawl open.
        """
        limit = self._max_request
        if self._deadline is not None:
            limit = min(limit, self._deadline() - time.monotonic())
        if limit <= 0:
            raise FetchError("crawl_deadline")
        try:
            async with asyncio.timeout(limit):
                return await self._get(url, headers, hop_allowed)
        except TimeoutError:
            raise FetchError(
                "crawl_deadline"
                if self._deadline is not None and time.monotonic() >= self._deadline()
                else "timeout"
            ) from None

    async def _get(
        self,
        url: str,
        headers: dict[str, str] | None,
        hop_allowed: Callable[[str], bool | Awaitable[bool]] | None,
    ) -> FetchResult:
        chain: list[str] = []
        current = url
        for _ in range(self._max_redirects + 1):
            if self._deadline is not None and time.monotonic() > self._deadline():
                raise FetchError("crawl_deadline")
            parts = check_url(current, self._ports)
            ip, port = await self._pin(parts)
            netloc = f"[{ip}]" if ":" in ip else ip
            target = f"{parts.scheme}://{netloc}:{port}{parts.path or '/'}"
            if parts.query:
                target += f"?{parts.query}"
            req_headers = {
                "Host": parts.netloc.rsplit("@", 1)[-1],
                "User-Agent": self._ua,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity",  # no compression: nothing to inflate into a bomb
                **(headers or {}),
            }
            try:
                async with self._http.stream(
                    "GET",
                    target,
                    headers=req_headers,
                    extensions={"sni_hostname": parts.hostname},
                ) as resp:
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = hdrs.get("location")
                        if not loc:
                            raise FetchError("redirect_without_location")
                        chain.append(current)
                        current = urljoin(current, loc)
                        if hop_allowed is not None:
                            verdict = hop_allowed(current)
                            if inspect.isawaitable(verdict):
                                verdict = await verdict
                            if not verdict:
                                raise FetchError("redirect_out_of_scope")
                        continue
                    body = bytearray()
                    async for piece in resp.aiter_raw():  # raw bytes: we asked for identity
                        body.extend(piece)
                        if len(body) > self._max_bytes:
                            raise FetchError("response_too_large")
                    return FetchResult(current, resp.status_code, hdrs, bytes(body), tuple(chain))
            except FetchError:
                raise
            except httpx.TimeoutException:
                raise FetchError("timeout") from None
            except httpx.HTTPError:
                raise FetchError("network_error") from None
        raise FetchError("too_many_redirects")
