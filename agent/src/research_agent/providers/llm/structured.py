"""Parsing model output into Pydantic, and the one-shot repair when it does not fit.

The contract (architecture v0.6 §13.2): invalid output gets exactly one repair attempt that
quotes the validation error back to the model (`LLM_INVALID_OUTPUT`); if that fails too the call
fails with `LLM_REPAIR_FAILED` and the node falls back to its deterministic default. A model is
never asked a third time - that is how a malformed-output loop would start.
"""

from __future__ import annotations

import json
import re
import types
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class OutputParseError(ValueError):
    """The text is not JSON, or the JSON does not satisfy the schema."""

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


def _extract_json(text: str) -> Any:
    candidate = text.strip()
    if match := _FENCE.match(candidate):
        candidate = match.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Some models wrap the object in prose; take the outermost braces as a last resort.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


def _accepts_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(arg is type(None) for arg in get_args(annotation))
    return annotation is Any


def _model_in(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        if found := _model_in(arg):
            return found
    return None


def drop_nulls_for_defaults(model: type[BaseModel], payload: Any) -> Any:
    """Undo the strict-schema trick: `null` for a non-nullable optional field means "default"."""
    if not isinstance(payload, dict):
        return payload
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        field = model.model_fields.get(key)
        if field is None:
            cleaned[key] = value
            continue
        if value is None and not _accepts_none(field.annotation) and not field.is_required():
            continue
        nested = _model_in(field.annotation)
        if nested is not None:
            if isinstance(value, dict):
                value = drop_nulls_for_defaults(nested, value)
            elif isinstance(value, list):
                value = [drop_nulls_for_defaults(nested, item) for item in value]
        cleaned[key] = value
    return cleaned


def parse_output[T: BaseModel](model: type[T], text: str) -> T:
    """Parse and validate, or raise `OutputParseError` with a message fit for a repair prompt."""
    try:
        payload = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OutputParseError(f"the response is not valid JSON: {exc}", raw=text) from exc
    try:
        return model.model_validate(drop_nulls_for_defaults(model, payload))
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
        raise OutputParseError(f"the JSON does not match the schema: {problems}", raw=text) from exc


def repair_instruction(error: OutputParseError) -> str:
    """The follow-up message for the single repair attempt."""
    return (
        "Your previous response could not be used.\n"
        f"Problem: {error}\n"
        "Reply again with ONLY a JSON object that satisfies the schema exactly. "
        "Use null for optional fields you cannot fill. No prose, no code fences."
    )
