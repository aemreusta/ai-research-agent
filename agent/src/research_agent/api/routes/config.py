"""The advanced-settings form is generated from here, never hand-written (D24)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from research_agent.api.schemas import ConfigField, ConfigSchema
from research_agent.config.loader import load_settings
from research_agent.config.schema import tunable_fields
from research_agent.providers.llm.catalog import catalog

router = APIRouter(prefix="/api/config", tags=["config"])


def _json_type(annotation: Any) -> tuple[str, bool]:
    """Collapse a Python annotation into what an HTML input needs to know."""
    text = str(annotation)
    nullable = "None" in text
    for name, kind in (("bool", "boolean"), ("int", "integer"), ("float", "number")):
        if name in text:
            return kind, nullable
    return "string", nullable


def _lookup(snapshot: dict[str, Any], path: str) -> Any:
    node: Any = snapshot
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


@router.get("/schema", response_model=ConfigSchema)
async def config_schema() -> ConfigSchema:
    """Tunable settings only: locked ones are omitted here and refused on submit.

    Defaults are the *effective* values (YAML over code defaults), because that is what a run
    started without overrides would actually use.
    """
    effective = load_settings()
    fields = []
    for field in tunable_fields():
        kind, nullable = _json_type(field.annotation)
        fields.append(
            ConfigField(
                path=field.path,
                group=field.group,
                description=field.description,
                type=kind,
                default=_lookup(effective.snapshot, field.path),
                minimum=field.minimum,
                maximum=field.maximum,
                nullable=nullable,
                options=(
                    [{"value": "", "label": "Automatic (configured provider chain)"}]
                    + [
                        {"value": model, "label": choice.label}
                        for model, choice in catalog().choices.items()
                    ]
                    if field.path in {"llm.reasoning_model", "llm.fast_model"}
                    else None
                ),
            )
        )
    groups = list(dict.fromkeys(field.group for field in fields))
    return ConfigSchema(fields=fields, groups=groups, config_hash=effective.config_hash)
