import json
import logging

from app.logging import JsonFormatter


def test_structured_extra_fields_reach_the_json_output() -> None:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "retrieval", (), None)
    rec.chunk_ids = ["c1", "c2"]
    rec.user = "alice"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "retrieval"
    assert out["chunk_ids"] == ["c1", "c2"] and out["user"] == "alice"
    assert {"ts", "level", "logger"} <= set(out)


def test_extra_cannot_overwrite_core_fields_and_unserialisable_values_are_stringified() -> None:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    rec.level = "spoofed"
    rec.obj = object()
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "INFO"
    assert isinstance(out["obj"], str)
