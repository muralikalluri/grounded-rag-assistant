# ruff: noqa: E501, S104  (S104: 0.0.0.0 is test DATA for the SSRF policy, not a bind address)
import asyncio
from collections.abc import AsyncIterator

import pytest

from app.config import Settings
from app.ingestion.connectors.netguard import FetchError, IpPolicy, SafeFetcher, check_url
from app.ingestion.connectors.web import WebCrawler, canonicalise, extract_links, page_name
from app.ingestion.types import RawDocument
from tests.support.site import HTML, LOOPBACK, FakeResolver, Site, page

STRIP = {"utm_source", "gclid"}


# ============================================================================ SSRF policy
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.9.9.9", "10.0.0.1", "172.16.5.5", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1",
        "fd12:3456::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
        "64:ff9b::7f00:1", "2002:7f00:1::1", "::7f00:1", "::127.0.0.1", "::a00:1", "::a9fe:a9fe", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "not-an-ip", "",
    ],
)  # fmt: skip
def test_non_public_addresses_are_never_permitted(ip: str) -> None:
    assert IpPolicy().permits(ip) is False


@pytest.mark.parametrize(
    "ip", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_are_permitted(ip: str) -> None:
    assert IpPolicy().permits(ip) is True


def test_explicit_allowlist_only_opens_the_listed_networks() -> None:
    policy = IpPolicy(LOOPBACK)
    assert (
        policy.permits("127.0.0.1")
        and not policy.permits("10.0.0.1")
        and not policy.permits("169.254.169.254")
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("ftp://example.com/x", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("http://user:pw@example.com/", "credentials_in_url"),
        ("http://example.com:22/", "port_not_allowed"),
        ("http://example.com:8080/", "port_not_allowed"),
        ("http:///nohost", "bad_url"),
        ("http://[::1", "bad_url"),
    ],
)
def test_url_checks_before_any_network_activity(url: str, reason: str) -> None:
    with pytest.raises(FetchError) as exc:
        check_url(url, (80, 443))
    assert exc.value.reason == reason


def test_production_config_refuses_private_crawling() -> None:
    with pytest.raises(ValueError, match="SSRF"):
        Settings(app_env="prod", crawl_allow_private_networks=True, audit_hmac_key="real-secret-value",
                 oidc_issuer="http://kc/r", oidc_jwks_url="http://kc/c")  # fmt: skip
    assert Settings(crawl_allow_private_networks=False).crawl_allow_private_networks is False


# ================================================================== fetcher (real sockets)
@pytest.fixture
async def site() -> AsyncIterator[Site]:
    s = Site()
    await s.start()
    yield s
    await s.stop()


def fetcher(
    site: Site, resolver: FakeResolver | None = None, policy: IpPolicy | None = None, **kw: object
) -> SafeFetcher:
    return SafeFetcher(
        user_agent="TestBot/1.0",
        allowed_ports=[site.port],
        policy=policy or IpPolicy(LOOPBACK),
        resolver=resolver or FakeResolver({"docs.test": ["127.0.0.1"]}),
        **kw,  # type: ignore[arg-type]
    )


async def test_default_policy_blocks_loopback_even_for_literal_and_odd_ip_forms(site: Site) -> None:
    f = SafeFetcher(user_agent="t", allowed_ports=[site.port])  # default: public addresses only
    for host in (
        "127.0.0.1",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "2130706433",
        "0x7f000001",
        "017700000001",
        "localhost",
    ):
        with pytest.raises(FetchError) as exc:
            await f.get(f"http://{host}:{site.port}/")
        assert exc.value.reason in ("address_not_allowed", "dns_failure"), host
    assert site.requests == []  # nothing ever reached the server
    await f.aclose()


async def test_a_dns_answer_mixing_public_and_private_addresses_is_refused(site: Site) -> None:
    f = fetcher(
        site, FakeResolver({"docs.test": ["93.184.216.34", "127.0.0.1"]}), policy=IpPolicy()
    )
    with pytest.raises(FetchError) as exc:
        await f.get(site.url("/"))
    assert exc.value.reason == "address_not_allowed" and site.requests == []
    await f.aclose()


async def test_connection_is_pinned_to_the_validated_ip_and_keeps_the_host_header(
    site: Site,
) -> None:
    site.add("/", page("Home"))
    resolver = FakeResolver({"docs.test": ["127.0.0.1"]})
    f = fetcher(site, resolver)
    res = await f.get(site.url("/"))
    assert res.status == 200 and b"Home" in res.body
    assert resolver.calls == ["docs.test"]  # resolved once; the transport did not re-resolve
    assert site.requests[0][1]["host"] == f"docs.test:{site.port}"  # virtual host preserved
    assert site.requests[0][1]["user-agent"] == "TestBot/1.0"
    assert site.requests[0][1]["accept-encoding"] == "identity"  # no compression -> no bombs
    await f.aclose()


async def test_dns_rebinding_between_hops_is_caught_because_every_hop_is_revalidated(
    site: Site,
) -> None:
    site.add("/start", b"", status=302, headers={"Location": f"http://docs.test:{site.port}/next"})
    site.add("/next", page("Next"))
    resolver = FakeResolver()
    resolver.sequence["docs.test"] = [
        ["127.0.0.1"],
        ["10.0.0.5"],
    ]  # first answer ok, then rebinding
    f = fetcher(site, resolver)
    with pytest.raises(FetchError) as exc:
        await f.get(site.url("/start"))
    assert exc.value.reason == "address_not_allowed"
    assert site.paths() == ["/start"]  # the second hop never connected
    await f.aclose()


async def test_redirect_to_the_cloud_metadata_address_is_refused(site: Site) -> None:
    site.add(
        "/r", b"", status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )
    f = SafeFetcher(user_agent="t", allowed_ports=[site.port, 80], policy=IpPolicy(LOOPBACK),
                    resolver=FakeResolver({"docs.test": ["127.0.0.1"]}))  # fmt: skip
    with pytest.raises(FetchError) as exc:
        await f.get(site.url("/r"))
    assert exc.value.reason == "address_not_allowed"
    await f.aclose()


async def test_redirect_loops_and_scope_vetoes_are_stopped(site: Site) -> None:
    site.add("/a", b"", status=302, headers={"Location": "/b"})
    site.add("/b", b"", status=302, headers={"Location": "/a"})
    f = fetcher(site, max_redirects=3)
    with pytest.raises(FetchError) as exc:
        await f.get(site.url("/a"))
    assert exc.value.reason == "too_many_redirects"
    site.add("/out", b"", status=301, headers={"Location": "http://other.test/"})
    with pytest.raises(FetchError) as exc2:
        await f.get(site.url("/out"), hop_allowed=lambda u: "other.test" not in u)
    assert exc2.value.reason == "redirect_out_of_scope"
    await f.aclose()


async def test_oversized_responses_are_aborted_while_streaming(site: Site) -> None:
    site.add("/big", b"<html>" + b"x" * 200_000)
    f = fetcher(site, max_bytes=10_000)
    with pytest.raises(FetchError) as exc:
        await f.get(site.url("/big"))
    assert exc.value.reason == "response_too_large"
    await f.aclose()


async def test_slow_servers_time_out(site: Site) -> None:
    async def hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    server = await asyncio.start_server(hang, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    f = SafeFetcher(user_agent="t", allowed_ports=[port], policy=IpPolicy(LOOPBACK),
                    resolver=FakeResolver({"slow.test": ["127.0.0.1"]}), timeout=0.3)  # fmt: skip
    with pytest.raises(FetchError) as exc:
        await f.get(f"http://slow.test:{port}/")
    assert exc.value.reason == "timeout"
    server.close()
    await f.aclose()


# ============================================================================= canonical URLs
@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("HTTP://Docs.Test:80/a#frag", "http://docs.test/a"),
        ("https://docs.test:443/a/", "https://docs.test/a/"),
        ("http://docs.test", "http://docs.test/"),
        ("http://docs.test/a?b=2&a=1&utm_source=x&gclid=y", "http://docs.test/a?a=1&b=2"),
        ("http://docs.test/a b", "http://docs.test/a%20b"),
        ("http://docs.test:8080/x", "http://docs.test:8080/x"),
        ("http://Bücher.test/", "http://xn--bcher-kva.test/"),
    ],
)
def test_canonicalisation_makes_equivalent_urls_identical(raw: str, canon: str) -> None:
    assert canonicalise(raw, STRIP) == canon


@pytest.mark.parametrize(
    "raw", ["javascript:alert(1)", "mailto:a@b.c", "ftp://x/", "data:text/html,x", "//nohost", ""]
)
def test_non_http_urls_are_not_crawlable(raw: str) -> None:
    assert canonicalise(raw, STRIP) is None


def test_link_extraction_keeps_only_http_links_and_ignores_foreign_base() -> None:
    body = b"""<html><head><base href="http://evil.test/"><meta http-equiv="Refresh" content="0; url=/moved"></head>
    <body><a href="/a">a</a><a href="javascript:x()">j</a><a href="mailto:x@y.z">m</a><a href="tel:1">t</a>
    <a href="data:text/html,hi">d</a><a href="http://other.test/o">o</a><a href="rel/b">b</a></body></html>"""
    links = extract_links(body, "http://docs.test/dir/page")
    assert "http://docs.test/a" in links and "http://docs.test/moved" in links
    assert "http://docs.test/dir/rel/b" in links  # foreign <base> ignored
    assert "http://other.test/o" in links  # extracted; scope is applied by the crawler
    assert not [ln for ln in links if not ln.startswith("http")]
    assert extract_links(b"\x00\x01not html", "http://docs.test/") == []
    assert page_name("http://docs.test/a/b/report.html") == "report.html"
    assert page_name("http://docs.test/") == "docs.test.html"
    assert page_name("http://docs.test/we ird/na$me") == "na_me.html"


# ==================================================================================== crawler
def crawler(site: Site, sleeps: list[float] | None = None, **cfg: object) -> WebCrawler:
    async def fake_sleep(d: float) -> None:
        if sleeps is not None:
            sleeps.append(d)

    settings = Settings(crawl_allowed_ports=[site.port], crawl_delay_seconds=1.0, **cfg)  # type: ignore[arg-type]
    return WebCrawler(settings, resolver=FakeResolver({"docs.test": ["127.0.0.1"]}), policy=IpPolicy(LOOPBACK),
                      sleep=fake_sleep, allowed_ports=[site.port])  # fmt: skip


async def run_crawl(c: WebCrawler, url: str) -> tuple[list[RawDocument], object]:
    docs: list[RawDocument] = []

    async def on_page(d: RawDocument) -> None:
        docs.append(d)

    res = await c.crawl(url, on_page)
    await c.aclose()
    return docs, res


def build_site(site: Site) -> None:
    site.add(
        "/robots.txt", "User-agent: *\nDisallow: /private\n", headers={"Content-Type": "text/plain"}
    )
    site.add(
        "/",
        page(
            "Home",
            "Welcome",
            ["/a", "/b#frag", "/private/secret", "http://other.test/x", "mailto:a@b.c", "/doc.pdf"],
        ),
    )
    site.add("/a", page("Page A", "About A", ["/", "/b", "/deep"]))
    site.add("/b", page("Page B", "About B", ["/a?utm_source=z"]))
    site.add("/deep", page("Deep", "Deep page", ["/deeper"]))
    site.add("/deeper", page("Deeper", "Too deep"))
    site.add("/private/secret", page("Secret", "Should not be fetched"))
    site.add("/doc.pdf", b"%PDF-1.4 binary", headers={"Content-Type": "application/pdf"})


async def test_crawls_same_host_pages_once_and_respects_robots_scope_and_type(site: Site) -> None:
    build_site(site)
    sleeps: list[float] = []
    docs, res = await run_crawl(crawler(site, sleeps, crawl_max_depth=3), site.url("/"))
    uris = sorted(d.item.uri for d in docs)
    assert uris == sorted(site.url(p) for p in ["/", "/a", "/b", "/deep", "/deeper"])
    assert "/private/secret" not in site.paths()  # robots.txt Disallow honoured
    assert "/doc.pdf" in site.paths() and all(
        "pdf" not in d.item.uri for d in docs
    )  # fetched, not ingested
    assert "http://other.test/x" not in " ".join(uris) and not any(
        "other.test" in h.get("host", "") for _, h in site.requests
    )
    assert (
        site.paths().count("/a") == 1 and site.paths().count("/b") == 1
    )  # cycle A<->B visited once
    assert (
        res.skipped["robots"] == 1
        and res.skipped["not_html"] == 1
        and res.skipped["other_host"] >= 1
    )  # type: ignore[attr-defined]
    assert res.truncated is False and all(
        d.mime_type == "text/html" and d.source_type == "web" for d in docs
    )  # type: ignore[attr-defined]
    assert sleeps and all(s >= 1.0 for s in sleeps)  # polite delay between requests


async def test_depth_and_page_limits_bound_the_crawl_and_flag_truncation(site: Site) -> None:
    build_site(site)
    docs, res = await run_crawl(crawler(site, crawl_max_depth=1), site.url("/"))
    assert sorted(d.item.uri for d in docs) == sorted(site.url(p) for p in ["/", "/a", "/b"])
    assert "/deep" not in site.paths()
    assert res.truncated is False  # type: ignore[attr-defined]  (depth limit is a scope choice, not an early stop)
    site.requests.clear()
    docs2, res2 = await run_crawl(
        crawler(site, crawl_max_depth=3, crawl_max_pages=2), site.url("/")
    )
    assert len(docs2) == 2 and res2.truncated is True  # type: ignore[attr-defined]


async def test_robots_5xx_fails_closed_and_4xx_allows_all(site: Site) -> None:
    site.add("/robots.txt", "boom", status=503)
    site.add("/", page("Home"))
    docs, res = await run_crawl(crawler(site), site.url("/"))
    assert docs == [] and site.paths() == ["/robots.txt"]  # nothing but robots.txt was requested
    assert res.skipped["robots"] == 1  # type: ignore[attr-defined]

    site2 = Site()
    await site2.start()
    site2.add("/", page("Home", "hi"))  # no robots.txt at all -> 404 -> allowed
    docs2, _ = await run_crawl(crawler(site2), site2.url("/"))
    assert len(docs2) == 1
    await site2.stop()


async def test_robots_disallow_all_blocks_everything_and_crawl_delay_is_honoured_up_to_the_cap(
    site: Site,
) -> None:
    site.add("/robots.txt", "User-agent: *\nDisallow: /\n")
    site.add("/", page("Home"))
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert docs == []
    site2 = Site()
    await site2.start()
    site2.add("/robots.txt", "User-agent: *\nCrawl-delay: 100\n")
    site2.add("/", page("Home", "", ["/x"]))
    site2.add("/x", page("X"))
    sleeps: list[float] = []
    await run_crawl(crawler(site2, sleeps, crawl_max_crawl_delay_seconds=7.0), site2.url("/"))
    assert sleeps == [7.0]  # the site asked for 100 s; we cap it
    await site2.stop()


async def test_named_bot_rules_in_robots_apply(site: Site) -> None:
    site.add(
        "/robots.txt", "User-agent: GroundedRAGBot\nDisallow: /a\n\nUser-agent: *\nDisallow:\n"
    )
    site.add("/", page("Home", "", ["/a", "/b"]))
    site.add("/a", page("A"))
    site.add("/b", page("B"))
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert sorted(d.item.uri for d in docs) == sorted(site.url(p) for p in ["/", "/b"])


async def test_http_errors_and_missing_pages_are_recorded_not_ingested(site: Site) -> None:
    site.add("/", page("Home", "", ["/gone", "/broken", "/ok"]))
    site.add("/gone", b"no", status=410)
    site.add("/broken", b"err", status=500)
    site.add("/ok", page("OK", "fine"))
    docs, res = await run_crawl(crawler(site), site.url("/"))
    assert sorted(d.item.uri for d in docs) == sorted(site.url(p) for p in ["/", "/ok"])
    assert site.url("/gone") in res.not_found and res.errors["http_500"] == 1  # type: ignore[attr-defined]


async def test_canonical_duplicates_and_tracking_params_collapse_to_one_fetch(site: Site) -> None:
    site.add("/", page("Home", "", ["/a", "/a#top", "/a?utm_source=x", "/a?gclid=1"]))
    site.add("/a", page("A"))
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert len(docs) == 2 and [p for p in site.paths() if p.startswith("/a")] == ["/a"]


async def test_redirect_inside_the_site_lands_on_the_canonical_final_url(site: Site) -> None:
    site.add("/", page("Home", "", ["/old"]))
    site.add("/old", b"", status=301, headers={"Location": "/new"})
    site.add("/new", page("New", "moved here"))
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert site.url("/new") in [d.item.uri for d in docs] and site.url("/old") not in [
        d.item.uri for d in docs
    ]


async def test_redirect_to_another_host_is_not_followed(site: Site) -> None:
    site.add("/", page("Home", "", ["/away"]))
    site.add("/away", b"", status=302, headers={"Location": "http://other.test/"})
    docs, res = await run_crawl(crawler(site), site.url("/"))
    assert [d.item.uri for d in docs] == [site.url("/")] and res.errors[
        "redirect_out_of_scope"
    ] == 1  # type: ignore[attr-defined]


async def test_seed_must_be_a_safe_url(site: Site) -> None:
    for bad in ("file:///etc/passwd", "http://user:pw@docs.test/", "javascript:alert(1)"):
        c = crawler(site)
        with pytest.raises(FetchError):
            await c.crawl(bad, lambda d: asyncio.sleep(0))  # type: ignore[arg-type,return-value]
        await c.aclose()
    assert site.requests == []


async def test_last_modified_header_becomes_the_source_modified_date(site: Site) -> None:
    site.add("/", page("Home"), headers={**HTML, "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"})
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert docs[0].item.modified_at is not None and docs[0].item.modified_at.year == 2015


# ================================================================= review fixes (M6 review)
async def test_a_redirect_into_a_robots_disallowed_path_is_not_followed(site: Site) -> None:
    site.add(
        "/robots.txt", "User-agent: *\nDisallow: /private\n", headers={"Content-Type": "text/plain"}
    )
    site.add("/", page("Home", "", ["/go"]))
    site.add("/go", b"", status=302, headers={"Location": "/private/secret"})
    site.add("/private/secret", page("Secret", "must never be fetched"))
    docs, res = await run_crawl(crawler(site), site.url("/"))
    assert [d.item.uri for d in docs] == [site.url("/")]
    assert "/private/secret" not in site.paths()  # the disallowed target was never requested
    assert res.errors["redirect_out_of_scope"] == 1  # type: ignore[attr-defined]


async def test_allow_slash_followed_by_a_disallow_is_honoured_end_to_end(site: Site) -> None:
    site.add(
        "/robots.txt",
        "User-agent: *\nAllow: /\nDisallow: /private\n",
        headers={"Content-Type": "text/plain"},
    )
    site.add("/", page("Home", "", ["/private/x", "/ok"]))
    site.add("/ok", page("OK"))
    site.add("/private/x", page("X"))
    docs, _ = await run_crawl(crawler(site), site.url("/"))
    assert sorted(d.item.uri for d in docs) == sorted(site.url(p) for p in ["/", "/ok"])
    assert "/private/x" not in site.paths()


async def test_a_server_dripping_bytes_cannot_outlive_the_crawl_deadline() -> None:
    import time

    async def drip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n"
        )
        for _ in range(50):  # one byte every 0.2 s: each read is well inside the per-read timeout
            writer.write(b"<")
            await writer.drain()
            await asyncio.sleep(0.2)

    server = await asyncio.start_server(drip, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    start = time.monotonic()
    f = SafeFetcher(user_agent="t", allowed_ports=[port], policy=IpPolicy(LOOPBACK),
                    resolver=FakeResolver({"slow.test": ["127.0.0.1"]}), timeout=5.0,
                    deadline=lambda: start + 1.0)  # fmt: skip
    with pytest.raises(FetchError) as exc:
        await f.get(f"http://slow.test:{port}/")
    assert exc.value.reason == "crawl_deadline" and time.monotonic() - start < 2.0
    server.close()
    await f.aclose()


async def test_a_single_slow_request_is_bounded_by_the_per_request_limit() -> None:
    import time

    async def drip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n"
        )
        for _ in range(50):
            writer.write(b"<")
            await writer.drain()
            await asyncio.sleep(0.2)

    server = await asyncio.start_server(drip, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    f = SafeFetcher(user_agent="t", allowed_ports=[port], policy=IpPolicy(LOOPBACK),
                    resolver=FakeResolver({"slow.test": ["127.0.0.1"]}), timeout=5.0,
                    max_request_seconds=0.6)  # fmt: skip
    start = time.monotonic()
    with pytest.raises(FetchError) as exc:
        await f.get(f"http://slow.test:{port}/")
    assert exc.value.reason == "timeout" and time.monotonic() - start < 1.5
    server.close()
    await f.aclose()


async def test_every_request_counts_toward_the_request_cap_not_just_ingested_pages(
    site: Site,
) -> None:
    site.add("/", page("Home", "", [f"/missing{i}" for i in range(20)]))
    docs, res = await run_crawl(
        crawler(site, crawl_max_requests=4, crawl_max_pages=50), site.url("/")
    )
    assert len(docs) == 1 and res.truncated is True  # type: ignore[attr-defined]
    assert res.requests == 4 and len([p for p in site.paths() if p.startswith("/missing")]) == 3  # type: ignore[attr-defined]


async def test_non_html_and_error_bodies_count_toward_the_byte_cap(site: Site) -> None:
    site.add("/", page("Home", "", ["/big.bin", "/next"]))
    site.add("/big.bin", b"x" * 30_000, headers={"Content-Type": "application/octet-stream"})
    site.add("/next", page("Next"))
    docs, res = await run_crawl(crawler(site, crawl_max_total_bytes=10_000), site.url("/"))
    assert [d.item.uri for d in docs] == [site.url("/")]
    assert "/next" not in site.paths() and res.truncated is True  # type: ignore[attr-defined]
    assert res.total_bytes >= 30_000  # type: ignore[attr-defined]
