"""Strict schema normalisation: what OpenAI strict mode demands, checked structurally."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from research_agent.providers.llm.catalog import Tier, catalog
from research_agent.providers.llm.schema import schema_hash, strict_json_schema


class Inner(BaseModel):
    label: str
    weight: float = 0.5


class Outer(BaseModel):
    rationale: str
    kind: Literal["a", "b"]
    items: list[Inner]
    note: str | None = None
    count: int = Field(default=3, ge=0)


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_objects(item))
    return found


def test_every_object_forbids_extra_keys_and_requires_every_property() -> None:
    schema = strict_json_schema(Outer)
    objects = _objects(schema)
    assert len(objects) == 2  # Outer and Inner
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_optional_fields_become_required_but_nullable() -> None:
    properties = strict_json_schema(Outer)["properties"]
    assert properties["note"]["anyOf"][-1] == {"type": "null"} or "null" in str(properties["note"])
    assert properties["count"]["type"] == ["integer", "null"]


def test_defaults_are_stripped() -> None:
    assert '"default"' not in str(strict_json_schema(Outer)).replace("'", '"')


def test_refs_stand_alone() -> None:
    items = strict_json_schema(Outer)["properties"]["items"]["items"]
    assert set(items) == {"$ref"}


def test_the_hash_is_stable_and_changes_with_the_contract() -> None:
    class Changed(Outer):
        extra: str

    assert schema_hash(Outer) == schema_hash(Outer)
    assert schema_hash(Outer) != schema_hash(Changed)


def test_a_normalised_payload_still_validates_with_null_for_optional_fields() -> None:
    """The model sends `null` for an optional field; Pydantic must accept and default it."""
    payload = {
        "rationale": "r",
        "kind": "a",
        "items": [{"label": "x", "weight": None}],
        "note": None,
        "count": None,
    }
    # `None` for a non-nullable defaulted field is dropped before validation by the gateway.
    from research_agent.providers.llm.structured import drop_nulls_for_defaults

    parsed = Outer.model_validate(drop_nulls_for_defaults(Outer, payload))
    assert parsed.count == 3
    assert parsed.items[0].weight == 0.5
    assert parsed.note is None


def test_the_catalog_prices_every_model_it_routes_to() -> None:
    models = catalog()
    for tier in Tier:
        for provider in models.chain:
            model = models.model_for(tier, provider)
            assert model is not None, f"{tier}/{provider} has no model"
            assert model in models.prices_usd_per_million_tokens


def test_cost_arithmetic() -> None:
    models = catalog()
    # gemini-3.1-flash-lite: $0.25 in / $1.50 out per million
    assert abs(models.cost("gemini-3.1-flash-lite", 1_000_000, 1_000_000) - 1.75) < 1e-9
    assert models.cost("unknown-model", 10, 10) == 0.0


def test_every_signature_schema_is_provider_ready() -> None:
    """What actually goes on the wire: no refs, and only keywords both providers accept."""
    import json

    from research_agent.prompting.signatures import SIGNATURES
    from research_agent.providers.llm.schema import inline_refs

    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "description",
        "title",
        "anyOf",
        "minimum",
        "maximum",
    }

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                for name, child in node["properties"].items():
                    walk(child, f"{path}.{name}")
                rest = {k: v for k, v in node.items() if k != "properties"}
            else:
                rest = node
            unexpected = set(rest) - allowed
            assert not unexpected, f"{path}: {unexpected}"
            for key, value in rest.items():
                if key in {"items", "anyOf"}:
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    for signature in SIGNATURES.values():
        schema = inline_refs(strict_json_schema(signature.output))
        assert "$ref" not in json.dumps(schema)
        assert schema["type"] == "object"
        walk(schema, signature.id)
