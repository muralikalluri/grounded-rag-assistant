"""robots.txt per RFC 9309.

Python's ``urllib.robotparser`` applies the FIRST matching rule, so ``Allow: /`` followed by
``Disallow: /private`` lets the crawler into /private. RFC 9309 says the MOST SPECIFIC (longest)
matching rule wins and ``Allow`` wins a tie. Patterns support ``*`` and a trailing ``$``.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(rx + ("$" if anchored else ""))


def _norm(path: str) -> str:
    # Compare decoded paths so /%7Efoo and /~foo are the same; keep "?" and "/" semantics.
    return unquote(path)


@dataclass
class RobotsRules:
    rules: list[_Rule] = field(default_factory=list)
    delay: float | None = None

    @classmethod
    def allow_all(cls) -> "RobotsRules":
        return cls()

    @classmethod
    def disallow_all(cls) -> "RobotsRules":
        return cls([_Rule(False, "/", _compile("/"))])

    @classmethod
    def parse(cls, text: str, product_token: str) -> "RobotsRules":
        token = product_token.lower()
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[str, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not last_was_agent and agents:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow", "crawl-delay"):
                if agents:  # a rule before any User-agent line belongs to no group: ignored
                    rules.append((key, value))
                last_was_agent = False
        if agents:
            groups.append((agents, rules))

        specific = [r for a, r in groups if token in a]
        chosen = specific or [r for a, r in groups if "*" in a]  # merge groups for the same agent
        out = cls()
        for group_rules in chosen:
            for key, value in group_rules:
                if key == "crawl-delay":
                    try:
                        out.delay = max(0.0, float(value))
                    except ValueError:
                        pass
                elif value:  # an empty Disallow means "nothing is disallowed"
                    out.rules.append(_Rule(key == "allow", value, _compile(_norm(value))))
        return out

    def allowed(self, path_and_query: str) -> bool:
        target = _norm(path_and_query or "/")
        best: _Rule | None = None
        for rule in self.rules:
            if rule.regex.match(target):
                if (
                    best is None
                    or len(rule.pattern) > len(best.pattern)
                    or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
                ):
                    best = rule
        return True if best is None else best.allow
