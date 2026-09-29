# ruff: noqa: E501, S105, S108  (test data: a bot token, sample URL paths)
"""RFC 9309 semantics: longest matching rule wins, Allow wins ties (urllib.robotparser gets this wrong)."""

import pytest

from app.ingestion.connectors.robots import RobotsRules

TOKEN = "GroundedRAGBot"


def rules(text: str) -> RobotsRules:
    return RobotsRules.parse(text, TOKEN)


def test_allow_before_a_more_specific_disallow_does_not_fail_open() -> None:
    """The reviewer's reproduction: stdlib returns True for /private/x here."""
    r = rules("User-agent: *\nAllow: /\nDisallow: /private\n")
    assert r.allowed("/private/x") is False and r.allowed("/private") is False
    assert r.allowed("/public") is True and r.allowed("/") is True


def test_longest_match_wins_regardless_of_order() -> None:
    a = rules("User-agent: *\nDisallow: /private\nAllow: /private/public\n")
    b = rules("User-agent: *\nAllow: /private/public\nDisallow: /private\n")
    for r in (a, b):
        assert r.allowed("/private/public/page") is True
        assert r.allowed("/private/other") is False


def test_allow_wins_a_tie_on_equal_length() -> None:
    assert rules("User-agent: *\nDisallow: /a\nAllow: /a\n").allowed("/a/b") is True
    assert rules("User-agent: *\nAllow: /a\nDisallow: /a\n").allowed("/a/b") is True


def test_wildcards_and_end_anchor() -> None:
    r = rules("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*/tmp/\nDisallow: /search?q=*&debug\n")
    assert r.allowed("/a/b.pdf") is False
    assert r.allowed("/a/b.pdf?download=1") is True  # '$' anchors the end of the URL
    assert r.allowed("/x/tmp/file") is False and r.allowed("/tmp/file") is True
    assert r.allowed("/search?q=cats&debug=1") is False and r.allowed("/search?q=cats") is True


def test_empty_disallow_means_everything_is_allowed_and_disallow_slash_blocks_all() -> None:
    assert rules("User-agent: *\nDisallow:\n").allowed("/anything") is True
    r = rules("User-agent: *\nDisallow: /\n")
    assert r.allowed("/") is False and r.allowed("/a/b") is False


def test_a_named_group_beats_the_wildcard_group_and_is_case_insensitive() -> None:
    text = "User-agent: *\nDisallow: /\n\nUser-agent: groundedragbot\nDisallow: /secret\n"
    r = rules(text)
    assert r.allowed("/page") is True and r.allowed("/secret/x") is False
    other = RobotsRules.parse(text, "SomeOtherBot")
    assert other.allowed("/page") is False  # falls back to '*'


def test_multiple_user_agent_lines_share_one_group_and_same_agent_groups_merge() -> None:
    r = rules(
        "User-agent: OtherBot\nUser-agent: GroundedRAGBot\nDisallow: /x\n\nUser-agent: GroundedRAGBot\nDisallow: /y\n"
    )
    assert r.allowed("/x") is False and r.allowed("/y") is False and r.allowed("/z") is True


def test_percent_encoding_is_normalised_before_matching() -> None:
    r = rules("User-agent: *\nDisallow: /%7Euser/\nDisallow: /~other/\n")
    assert r.allowed("/~user/a") is False and r.allowed("/%7Eother/a") is False


def test_crawl_delay_comments_crlf_junk_and_rules_before_any_agent() -> None:
    text = "Disallow: /ignored-no-agent\r\n# comment\r\nUser-agent: * # trailing\r\nCrawl-delay: 5\r\nDisallow: /p # c\r\nSitemap: http://x/s.xml\r\nnot a directive\r\n"
    r = rules(text)
    assert r.delay == 5.0 and r.allowed("/p/a") is False and r.allowed("/ignored-no-agent") is True
    assert rules("User-agent: *\nCrawl-delay: abc\n").delay is None


def test_allow_all_and_disallow_all_constructors() -> None:
    assert RobotsRules.allow_all().allowed("/x") is True
    assert RobotsRules.disallow_all().allowed("/x") is False


@pytest.mark.parametrize("path", ["", "/", "/?q=1"])
def test_root_paths(path: str) -> None:
    assert rules("User-agent: *\nDisallow: /admin\n").allowed(path) is True
