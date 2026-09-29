"""Web crawl connector (SPEC §5.2): same-host crawl from a seed URL with depth/page limits.

Security (each has a test): every request goes through ``SafeFetcher`` (SSRF guard, pinned IP,
validated redirects); robots.txt is honoured and FAILS CLOSED on 5xx/timeouts; only HTML is
ingested; responses are size-capped while streaming; a page's text is untrusted (the pipeline
flags instruction-like chunks). JavaScript-rendered pages are not supported (documented limit).

Deviation from SPEC §5.2's ``Connector`` protocol (``list_items`` / ``fetch``): discovery and
fetching are the same act for a crawler (you only learn the next URL by fetching the last page),
so the crawler exposes ``crawl(seed, on_page)`` and hands each page to the ingestion pipeline.
Not implemented (Later): conditional GET (ETag / Last-Modified; the checksum dedup is the
backstop), sitemaps. robots.txt-disallowed pages that were ingested earlier are not retired.
"""

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from app.config import Settings
from app.ingestion.connectors.netguard import (
    FetchError,
    FetchResult,
    IpPolicy,
    Resolver,
    SafeFetcher,
    check_url,
)
from app.ingestion.connectors.robots import RobotsRules
from app.ingestion.types import MIME_HTML, RawDocument, SourceItem

_REFRESH = re.compile(r"^\s*\d+\s*;\s*url\s*=\s*['\"]?([^'\"]+)", re.IGNORECASE)
_SAFE_PATH = "/-._~!$&'()*+,;=:@%"


def canonicalise(url: str, strip_params: set[str]) -> str | None:
    """Stable form of a URL for de-duplication; None if it is not a crawlable http(s) URL."""
    try:
        p = urlsplit(url.strip())
        port = p.port
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    try:
        host = p.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    scheme = p.scheme.lower()
    default = 443 if scheme == "https" else 80
    netloc = f"[{host}]" if ":" in host else host
    if port and port != default:
        netloc += f":{port}"
    path = quote(unquote(p.path or "/"), safe=_SAFE_PATH)
    query = urlencode(
        sorted(
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in strip_params
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))  # fragment dropped


def page_name(url: str) -> str:
    """Filename used only for storage suffix / title fallback (never trusted as a path)."""
    p = urlsplit(url)
    last = (p.path.rstrip("/").rsplit("/", 1)[-1] or p.hostname or "page")[:80]
    last = re.sub(r"[^A-Za-z0-9._-]", "_", last)
    return last if last.endswith((".html", ".htm")) else f"{last}.html"


def _xpath(doc: Any, expr: str) -> list[str]:
    return [str(x) for x in doc.xpath(expr)]


def extract_links(body: bytes, page_url: str, max_links: int = 500) -> list[str]:
    """http(s) hrefs from <a>, plus a <meta refresh> target. Everything else is dropped."""
    try:
        doc = lxml_html.fromstring(body)
    except (ValueError, lxml_html.etree.ParserError):
        return []
    base = page_url
    bases = _xpath(doc, "//base/@href")
    if bases:
        candidate = urljoin(page_url, bases[0])
        if urlsplit(candidate).hostname == urlsplit(page_url).hostname:
            base = candidate  # a <base> pointing elsewhere is ignored
    hrefs = _xpath(doc, "//a/@href")
    for content in _xpath(doc, "//meta[translate(@http-equiv,'REFSH','refsh')='refresh']/@content"):
        m = _REFRESH.match(content)
        if m:
            hrefs.append(m.group(1))
    out: list[str] = []
    for h in hrefs[:max_links]:
        absolute = urljoin(base, h.strip())
        if urlsplit(absolute).scheme in (
            "http",
            "https",
        ):  # drops javascript:, mailto:, data:, tel:
            out.append(absolute)
    return out


class Robots:
    """robots.txt per origin (RFC 9309). 200 -> parsed; 4xx -> allow all; 5xx/errors -> disallow."""

    def __init__(self, fetcher: SafeFetcher, ua: str, delay_cap: float, max_bytes: int) -> None:
        self._fetcher = fetcher
        self._token = ua.split("/")[0].split()[0]
        self._cap = delay_cap
        self._max_bytes = max_bytes
        self._cache: dict[str, RobotsRules] = {}

    async def _rules(self, url: str) -> RobotsRules:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._cache:
            return self._cache[origin]
        try:
            res = await self._fetcher.get(
                f"{origin}/robots.txt", hop_allowed=lambda u: urlsplit(u).netloc == p.netloc
            )
            if res.status == 200:
                text = res.body[: self._max_bytes].decode("utf-8", "replace")
                rules = RobotsRules.parse(text, self._token)
            elif 400 <= res.status < 500:
                rules = RobotsRules.allow_all()  # no robots.txt: everything allowed
            else:
                rules = RobotsRules.disallow_all()  # server error: fail closed
        except FetchError:
            rules = RobotsRules.disallow_all()  # unreachable: fail closed
        self._cache[origin] = rules
        return rules

    async def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        return (await self._rules(url)).allowed(p.path + (f"?{p.query}" if p.query else ""))

    async def crawl_delay(self, url: str) -> float:
        d = (await self._rules(url)).delay
        return min(float(d), self._cap) if d else 0.0


@dataclass
class CrawlResult:
    seen: set[str] = field(default_factory=set)
    not_found: set[str] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    pages: int = 0
    requests: int = 0  # every page request, including 404s and non-HTML
    total_bytes: int = 0  # every body downloaded, ingested or not
    truncated: bool = False  # stopped early (limits/deadline): the crawl is NOT a complete picture

    def bump(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


OnPage = Callable[[RawDocument], Awaitable[None]]


class WebCrawler:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver | None = None,
        policy: IpPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        allowed_ports: list[int] | None = None,
    ) -> None:
        self._s = settings
        self._sleep = sleep
        self._started = time.monotonic()
        if policy is None:
            policy = (
                IpPolicy.allow_private() if settings.crawl_allow_private_networks else IpPolicy()
            )
        self._fetcher = SafeFetcher(
            user_agent=settings.crawl_user_agent,
            allowed_ports=allowed_ports or settings.crawl_allowed_ports,
            policy=policy,
            resolver=resolver,
            timeout=settings.crawl_request_timeout_seconds,
            max_bytes=settings.crawl_max_bytes_per_page,
            max_redirects=settings.crawl_max_redirects,
            deadline=lambda: self._started + settings.crawl_total_timeout_seconds,
            max_request_seconds=settings.crawl_max_request_seconds,
        )
        self._robots = Robots(
            self._fetcher,
            settings.crawl_user_agent,
            settings.crawl_max_crawl_delay_seconds,
            settings.crawl_max_robots_bytes,
        )
        self._strip = set(settings.crawl_strip_params)

    async def aclose(self) -> None:
        await self._fetcher.aclose()

    def _canon(self, url: str) -> str | None:
        return canonicalise(url, self._strip)

    async def fetch_page(self, url: str, host: str) -> FetchResult:
        async def hop_allowed(target: str) -> bool:
            # A redirect must stay on the host AND be allowed by robots.txt: otherwise a
            # permitted URL that 302s into a disallowed one would bypass the rules.
            same_host = (urlsplit(target).hostname or "").lower() == host
            return same_host and await self._robots.allowed(target)

        return await self._fetcher.get(url, hop_allowed=hop_allowed)

    def _to_raw(self, res: FetchResult, canonical: str) -> RawDocument | None:
        ctype = res.content_type
        head = res.body[:2048].lower()
        looks_html = b"<" in head and b"\x00" not in head
        if ctype not in ("text/html", "application/xhtml+xml") or not looks_html:
            return None
        modified: datetime | None = None
        if "last-modified" in res.headers:
            try:
                modified = parsedate_to_datetime(res.headers["last-modified"]).astimezone(UTC)
            except (TypeError, ValueError):
                modified = None
        return RawDocument(
            item=SourceItem(uri=canonical, filename=page_name(canonical), modified_at=modified),
            content=res.body,
            mime_type=MIME_HTML,
            source_type="web",
        )

    async def fetch_one(self, url: str) -> tuple[str, RawDocument | None]:
        """Re-check a single known URL: ('ok', doc) | ('gone', None) | ('unknown', None)."""
        canon = self._canon(url)
        if canon is None:
            return "unknown", None
        host = (urlsplit(canon).hostname or "").lower()
        try:
            if not await self._robots.allowed(canon):
                return "unknown", None
            res = await self.fetch_page(canon, host)
        except FetchError:
            return "unknown", None
        if res.status in (404, 410):
            return "gone", None
        if res.status != 200:
            return "unknown", None
        final = self._canon(res.url) or canon
        raw = self._to_raw(res, final)
        return ("ok", raw) if raw else ("unknown", None)

    async def crawl(self, seed: str, on_page: OnPage) -> CrawlResult:
        s = self._s
        result = CrawlResult()
        seed_canon = self._canon(seed)
        if seed_canon is None:
            raise FetchError("bad_seed")
        check_url(
            seed_canon, s.crawl_allowed_ports if not self._fetcher._ports else self._fetcher._ports
        )  # noqa: SLF001
        host = (urlsplit(seed_canon).hostname or "").lower()
        queue: deque[tuple[str, int]] = deque([(seed_canon, 0)])
        queued = {seed_canon}
        first = True
        while queue:
            if (
                result.pages >= s.crawl_max_pages
                or result.requests >= s.crawl_max_requests
                or result.total_bytes >= s.crawl_max_total_bytes
            ):
                result.truncated = True
                break
            if time.monotonic() - self._started > s.crawl_total_timeout_seconds:
                result.truncated = True
                break
            url, depth = queue.popleft()
            try:
                if not await self._robots.allowed(url):
                    result.bump(result.skipped, "robots")
                    continue
                if not first:  # politeness: one request at a time, with a delay
                    delay = max(s.crawl_delay_seconds, await self._robots.crawl_delay(url))
                    await self._sleep(delay)
                first = False
                result.requests += 1
                res = await self.fetch_page(url, host)
            except FetchError as exc:
                result.bump(result.errors, exc.reason)
                if exc.reason == "crawl_deadline":
                    result.truncated = True
                    break
                continue
            result.total_bytes += len(res.body)  # count everything we downloaded
            final = self._canon(res.url) or url
            if res.status in (404, 410):
                result.not_found.add(final)
                result.bump(result.skipped, f"http_{res.status}")
                continue
            if res.status != 200:
                result.bump(result.errors, f"http_{res.status}")
                continue
            if final in result.seen:
                continue  # redirect landed on a page we already have
            raw = self._to_raw(res, final)
            if raw is None:
                result.bump(result.skipped, "not_html")
                continue
            result.seen.add(final)
            queued.add(final)
            result.pages += 1
            await on_page(raw)
            if depth < s.crawl_max_depth:
                for link in extract_links(res.body, final, s.crawl_max_links_per_page):
                    canon = self._canon(link)
                    if canon is None or canon in queued:
                        continue
                    if (urlsplit(canon).hostname or "").lower() != host:
                        result.bump(result.skipped, "other_host")
                        continue
                    queued.add(canon)
                    queue.append((canon, depth + 1))
        if queue:
            result.truncated = True  # left work behind
        return result


def summarize(result: CrawlResult) -> dict[str, Any]:
    return {
        "pages": result.pages,
        "bytes": result.total_bytes,
        "truncated": result.truncated,
        "skip_reasons": result.skipped,
        "errors": result.errors,
        "not_found": len(result.not_found),
    }
