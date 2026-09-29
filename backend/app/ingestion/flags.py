import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def normalise_for_matching(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return " ".join(text.lower().split())


def compile_patterns(patterns: dict[str, str]) -> dict[str, re.Pattern[str]]:
    return {name: re.compile(rx) for name, rx in patterns.items()}


def injection_matches(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    """Names of matching instruction-like patterns. Never returns matched text."""
    norm = normalise_for_matching(text)
    return sorted(name for name, rx in patterns.items() if rx.search(norm))
