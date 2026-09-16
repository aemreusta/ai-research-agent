"""Turn a Pydantic model into a JSON Schema both providers enforce strictly.

OpenAI's strict structured outputs reject most schemas Pydantic produces as-is: every property
must be listed in `required`, every object must say `additionalProperties: false`, and defaults
are not allowed. Gemini's `responseJsonSchema` is more lenient but happy with the same shape. So
the schema is normalised once here, and optional fields become "required but nullable" - the
model must say `null` explicitly, and Pydantic applies the default afterwards.

The hash of this normalised schema is also what the prompt registry compares, so a prompt
version written against a different output contract is refused (`PROMPT_SCHEMA_MISMATCH`).
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from pydantic import BaseModel

# Keywords strict mode rejects or that carry no meaning for generation.
_DROP = frozenset({"default", "examples", "discriminator"})


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "null":
        return schema
    if "anyOf" in schema:
        if not any(option.get("type") == "null" for option in schema["anyOf"]):
            schema["anyOf"].append({"type": "null"})
        return schema
    if isinstance(schema.get("type"), str) and "$ref" not in schema:
        schema["type"] = [schema["type"], "null"]
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _normalise(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalise(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {key: _normalise(value) for key, value in node.items() if key not in _DROP}

    # `$ref` must stand alone in strict mode.
    if "$ref" in out:
        return {"$ref": out["$ref"]}

    if out.get("type") == "object" or "properties" in out:
        properties: dict[str, Any] = out.get("properties", {})
        required = set(out.get("required", []))
        for name, child in list(properties.items()):
            if name not in required:
                properties[name] = _nullable(child)
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
        out["type"] = "object"
    return out


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Provider-ready schema for `model`, with `$defs` kept and normalised too."""
    raw = copy.deepcopy(model.model_json_schema(mode="validation"))
    definitions = raw.pop("$defs", {})
    schema: dict[str, Any] = _normalise(raw)
    if definitions:
        schema["$defs"] = {name: _normalise(value) for name, value in definitions.items()}
    return schema


def schema_hash(model: type[BaseModel]) -> str:
    """Stable fingerprint of an output contract."""
    canonical = json.dumps(strict_json_schema(model), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
