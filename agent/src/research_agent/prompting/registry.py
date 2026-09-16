"""Resolve the prompt text for each signature: Langfuse first, the repo YAML seed as fallback.

A version is only accepted if it fits the code:

* its `output_schema_hash` equals the hash of the signature's Pydantic output model, and
* its template uses exactly the signature's input variables - no unknown ones (they would fail
  at render time) and no missing ones (a prompt that silently drops `{ledger}` would let the
  synthesiser write from memory).

A Langfuse version that fails either check is refused (`PROMPT_SCHEMA_MISMATCH`) and the seed is
used; an unreachable Langfuse degrades to the seed (`PROMPT_REGISTRY_DEGRADED`). Versions are
resolved once per run, so editing a prompt mid-run never changes that run (v0.6 §20.2).

A Langfuse version that descends from an older repo seed (its config carries `seed_version`) is
replaced when the repo seed version is bumped, so prompt fixes in the repo reach Langfuse without
manual steps. Edits made in Langfuse keep that config, so a seed bump wins over them - bump the
seed only for changes that should.
"""

from __future__ import annotations

import hashlib
import json
import string
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from research_agent.agent.runtime import EventSink
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.logging import best_effort
from research_agent.paths import config_dir
from research_agent.prompting.signatures import SIGNATURES, Signature


class PromptVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: str
    source: str  # "yaml" | "langfuse"
    description: str = ""
    instructions: str
    template: str
    demos: list[dict[str, Any]] = Field(default_factory=list)
    output_schema_hash: str
    label: str | None = None
    url: str | None = None
    seed_version: str | None = None
    """For a Langfuse version created from a repo seed: which seed version it was."""

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            [self.instructions, self.template, self.demos], sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    def reference(self) -> dict[str, str]:
        """What `runs.prompt_versions` stores for this signature."""
        return {
            "version": self.version,
            "source": self.source,
            "content_hash": self.content_hash,
            **({"label": self.label} if self.label else {}),
        }


class PromptProblem(ValueError):
    pass


def template_fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def check_version(version: PromptVersion, signature: Signature) -> None:
    if version.output_schema_hash != signature.schema_hash:
        raise PromptProblem(
            f"{version.id}@{version.version}: output schema hash {version.output_schema_hash} "
            f"does not match the code ({signature.schema_hash})"
        )
    fields = template_fields(version.template)
    unknown = fields - signature.inputs
    missing = signature.inputs - fields
    if unknown or missing:
        raise PromptProblem(
            f"{version.id}@{version.version}: template variables do not match the signature "
            f"(unknown: {sorted(unknown)}, missing: {sorted(missing)})"
        )


class RemotePrompts(Protocol):
    """Implemented by the Langfuse client wrapper."""

    async def get_prompt(self, name: str, *, label: str) -> PromptVersion | None: ...


def _read_seed(path: Path) -> PromptVersion:
    with path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    return PromptVersion(
        id=raw["id"],
        version=str(raw["version"]),
        source="yaml",
        description=raw.get("description", ""),
        instructions=raw["instructions"],
        template=raw["template"],
        demos=raw.get("demos") or [],
        output_schema_hash=raw["output_schema_hash"],
    )


@cache
def load_seeds(directory: Path) -> dict[str, PromptVersion]:
    seeds = {}
    for path in sorted(directory.glob("*.yaml")):
        seed = _read_seed(path)
        seeds[seed.id] = seed
    return seeds


def seed_directory() -> Path:
    return config_dir() / "prompts"


class PromptRegistry:
    def __init__(
        self,
        *,
        directory: Path | None = None,
        remote: RemotePrompts | None = None,
        label: str = "production",
    ) -> None:
        self._seeds = load_seeds(directory or seed_directory())
        self._remote = remote
        self._label = label

    def seed(self, signature_id: str) -> PromptVersion:
        return self._seeds[signature_id]

    async def resolve(self, signature: Signature, events: EventSink | None = None) -> PromptVersion:
        seed = self._seeds.get(signature.id)
        if seed is None:
            raise PromptProblem(f"no seed prompt for signature {signature.id}")
        check_version(seed, signature)  # a broken seed is a bug, not a degradation

        if self._remote is None:
            return seed
        try:
            remote = await self._remote.get_prompt(signature.id, label=self._label)
        except Exception as exc:
            if events is not None:
                await events.error(
                    AgentError(
                        code=ErrorCode.PROMPT_REGISTRY_DEGRADED,
                        node=events.node,
                        decision=f"use the repo seed for {signature.id}",
                        outcome=f"yaml@{seed.version}",
                        cause=f"{type(exc).__name__}: {exc}"[:300],
                    )
                )
            return seed
        if remote is None:
            # Seed on first use: `migrate` may have run before Langfuse was up.
            await self._publish(seed)
            return seed
        if _outdated_seed(remote, seed):
            # The repo seed moved on and Langfuse still serves a version based on an older
            # one: publish the new seed and use it.
            await self._publish(seed)
            return seed
        try:
            check_version(remote, signature)
        except PromptProblem as problem:
            # The code's output contract moved on (a new seed version); publish the seed so the
            # label points at a compatible version from the next run on.
            published = await self._publish(seed)
            if events is not None:
                await events.error(
                    AgentError(
                        code=ErrorCode.PROMPT_SCHEMA_MISMATCH,
                        node=events.node,
                        decision=f"refuse {signature.id}@{remote.version} and use the repo seed"
                        + (" (published to Langfuse)" if published else ""),
                        outcome=f"yaml@{seed.version}",
                        cause=str(problem)[:300],
                    )
                )
            return seed
        return remote

    async def _publish(self, seed: PromptVersion) -> bool:
        create = getattr(self._remote, "create_prompt", None)
        if not callable(create):
            return False
        with best_effort("seeding a prompt into Langfuse", prompt=seed.id):
            await create(seed, labels=[self._label, "seed"])
            return True
        return False

    async def resolve_all(self, events: EventSink | None = None) -> RunPrompts:
        versions = {}
        for signature in SIGNATURES.values():
            versions[signature.id] = await self.resolve(signature, events)
        return RunPrompts(versions)


def _outdated_seed(remote: PromptVersion, seed: PromptVersion) -> bool:
    if remote.seed_version is None:
        return False
    try:
        return int(remote.seed_version) < int(seed.version)
    except ValueError:
        return False


class RunPrompts:
    """The prompt versions pinned for one run."""

    def __init__(self, versions: dict[str, PromptVersion]) -> None:
        self._versions = versions

    def __getitem__(self, signature_id: str) -> PromptVersion:
        return self._versions[signature_id]

    def references(self) -> dict[str, dict[str, str]]:
        return {sid: version.reference() for sid, version in sorted(self._versions.items())}
