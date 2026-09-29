import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class RawCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    quote: str


class RawSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    citations: list[RawCitation]


class RawAnswer(BaseModel):
    """What the model must return. ``source`` is a per-request alias (S1..Sn), never a UUID."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "not_found"]
    answer_sentences: list[RawSentence]


class InvalidOutput(Exception):
    """Model output is not valid JSON / does not match the schema. Carries no model text."""


# Strict-mode JSON schema for providers that enforce it (OpenAI). Length limits live in
# Pydantic/the validator because strict schemas do not support them.
ANSWER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "answer_sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["source", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "answer_sentences"],
    "additionalProperties": False,
}

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_model_output(text: str) -> RawAnswer:
    """Parse and validate. Errors are reduced to a generic message (never echo model text)."""
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise InvalidOutput("no JSON object in model output")
    try:
        data = json.loads(body[start : end + 1])
        return RawAnswer.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        detail = (
            f"{len(exc.errors(include_input=False))} schema errors"
            if isinstance(exc, ValidationError)
            else "invalid JSON"
        )
        raise InvalidOutput(detail) from None
