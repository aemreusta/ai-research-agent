"""Research skills in the Agent Skills format: `skills/<name>/SKILL.md` + `references/`.

A skill is domain expertise as data - guidance text, trusted domains, query patterns. It never
contains executable code, and it cannot change budgets, termination or gate rules (§20.3):
the only things it can influence are the prompts it is injected into and the domain tier list,
where it can add domains but never remove or demote one.

Progressive disclosure: `analyze_query` sees only names and descriptions and picks 0-2; only the
chosen skills' bodies are injected into later prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from research_agent.paths import repo_root

_FRONTMATTER = re.compile(r"^---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)$", re.DOTALL)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MAX_SELECTED = 2


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    domains: dict[int, list[str]] = field(default_factory=dict)
    query_patterns: list[str] = field(default_factory=list)
    version: str = "1"
    source: str = "repo"


class SkillError(ValueError):
    pass


def parse_skill(directory: Path) -> Skill:
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillError(f"{directory.name}: SKILL.md needs YAML frontmatter")
    meta: dict[str, Any] = yaml.safe_load(match["meta"]) or {}
    name = str(meta.get("name", ""))
    if not _NAME.match(name) or name != directory.name:
        raise SkillError(f"{directory.name}: name must match the folder and be kebab-case")
    description = str(meta.get("description", "")).strip()
    if not description:
        raise SkillError(f"{name}: description is required (it is all analyze_query sees)")
    for forbidden in ("scripts", "bin"):
        if (directory / forbidden).exists():
            raise SkillError(f"{name}: skills may not ship executable code ({forbidden}/)")

    domains: dict[int, list[str]] = {}
    patterns: list[str] = []
    references = directory / "references"
    if (references / "domains.yaml").is_file():
        raw = yaml.safe_load((references / "domains.yaml").read_text(encoding="utf-8")) or {}
        for key, values in raw.items():
            tier = int(str(key).removeprefix("tier_"))
            if tier not in (1, 2):
                raise SkillError(f"{name}: skills may only add tier 1 or tier 2 domains")
            domains[tier] = [str(value).lower() for value in values]
    if (references / "queries.yaml").is_file():
        raw = yaml.safe_load((references / "queries.yaml").read_text(encoding="utf-8")) or {}
        patterns = [str(item) for item in raw.get("patterns", [])]

    return Skill(
        name=name,
        description=description,
        body=match["body"].strip(),
        domains=domains,
        query_patterns=patterns,
        version=str(meta.get("version", "1")),
    )


def skills_directory() -> Path:
    return repo_root() / "skills"


@cache
def load_skills(directory: Path | None = None) -> dict[str, Skill]:
    root = directory or skills_directory()
    if not root.is_dir():
        return {}
    skills = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            skill = parse_skill(child)
            skills[skill.name] = skill
    return skills


def menu(skills: dict[str, Skill]) -> str:
    if not skills:
        return "(no skills available)"
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills.values())


def select(requested: list[str], skills: dict[str, Skill]) -> list[Skill]:
    """Keep only known skills, at most two, in the order the model gave."""
    chosen: list[Skill] = []
    for name in requested:
        skill = skills.get(name.strip())
        if skill is not None and skill not in chosen:
            chosen.append(skill)
        if len(chosen) == MAX_SELECTED:
            break
    return chosen


def guidance(chosen: list[Skill]) -> str:
    if not chosen:
        return ""
    blocks = []
    for skill in chosen:
        block = f"### Domain guidance: {skill.name}\n{skill.body}"
        if skill.query_patterns:
            block += "\n\nUseful query patterns:\n" + "\n".join(
                f"- {pattern}" for pattern in skill.query_patterns
            )
        blocks.append(block)
    return "\n\n".join(blocks)
