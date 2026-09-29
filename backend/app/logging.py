import json
import logging
import re
from datetime import UTC, datetime

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]*)?")


def redact(text: str) -> str:
    """Belt and braces: a token must never reach the logs even if someone logs it by mistake."""
    return _JWT.sub("[redacted-jwt]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        # Structured fields passed via ``extra=`` (ids, counts; never document text).
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in (
        "httpx",
        "httpcore",
        "huggingface_hub",
        "sentence_transformers",
        "LiteLLM",  # logs full prompts at DEBUG
        "LiteLLM Router",
        "LiteLLM Proxy",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
