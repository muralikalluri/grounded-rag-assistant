"""``logger.x(..., extra={...})`` with a reserved LogRecord key raises KeyError at runtime, but
only when that log level is enabled - so it hides until production. Scan for it statically."""

import ast
import logging
from pathlib import Path

RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
APP = Path(__file__).resolve().parent.parent / "app"


def _extra_dict_keys(call: ast.Call) -> list[str]:
    keys: list[str] = []
    for kw in call.keywords:
        if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
            keys += [
                k.value
                for k in kw.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
    return keys


def test_no_logging_call_uses_a_reserved_extra_key() -> None:
    offenders = []
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                offenders += [
                    f"{path.relative_to(APP)}:{node.lineno} extra[{k!r}]"
                    for k in _extra_dict_keys(node)
                    if k in RESERVED
                ]
    assert offenders == []


def test_the_scanner_itself_detects_a_reserved_key() -> None:
    call = ast.parse('logger.info("x", extra={"created": 1, "ok": 2})').body[0].value  # type: ignore[attr-defined]
    assert [k for k in _extra_dict_keys(call) if k in RESERVED] == ["created"]
