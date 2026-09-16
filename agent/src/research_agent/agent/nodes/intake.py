"""intake_guard, analyze_query, plan."""

from __future__ import annotations

import re
from datetime import date

from research_agent.agent.deps import AgentDeps
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import (
    Facet,
    QueryAnalysis,
    ResearchState,
    SubQuestion,
    TimeScope,
)
from research_agent.agent.text import detect_language, token_jaccard
from research_agent.errors import AgentError, AgentException, ErrorCode
from research_agent.observability.events import EventType
from research_agent.prompting.schemas import AnalyzeOutput, PlanOutput
from research_agent.prompting.skills import guidance, menu, select
from research_agent.providers.llm.gateway import LLMFailure

MAX_QUESTION_CHARS = 2000
_YEAR = re.compile(r"\b(20\d{2})\b")
_ENTITY = re.compile(r"\b(?:[A-ZÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ'\u2019.&-]*[A-Za-zçğıöşü0-9]|[A-Z]{2,})\b")
_ACTION_WORDS = re.compile(
    r"\b(?:plan|aksiyon|öneri|recommend|should|nasıl|how to|ne yapmal|steps?)\w*", re.IGNORECASE
)


# --- intake_guard ------------------------------------------------------------------------------


async def intake_guard(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    """Validate, mask (boundary B1 - idempotent: the API already masked), detect the language."""
    question = " ".join(state.question.split())
    if not question:
        raise AgentException(
            AgentError(
                code=ErrorCode.CONFIG_INVALID,
                node="intake_guard",
                decision="reject the run",
                outcome="empty question",
            )
        )
    if len(question) > MAX_QUESTION_CHARS:
        raise AgentException(
            AgentError(
                code=ErrorCode.CONFIG_INVALID,
                node="intake_guard",
                decision="reject the run",
                outcome=f"question longer than {MAX_QUESTION_CHARS} characters",
            )
        )

    masked = await deps.masker.amask(question)
    if masked.degraded:
        await events.error(
            AgentError(
                code=ErrorCode.PII_ENGINE_DEGRADED,
                node="intake_guard",
                decision="mask with patterns only",
                outcome="known identifiers are still masked",
            )
        )
    state.question = masked.text
    state.language = detect_language(masked.text)
    state.as_of = deps.today
    summary = masked.summary()
    note = f", masked {sum(summary.values())} identifier(s)" if summary else ""
    await events.info(
        EventType.NODE_FINISHED,
        f"Question accepted ({state.language}){note}.",
        label="Intake",
        data={"language": state.language, "pii_masked": summary, "as_of": state.as_of.isoformat()},
    )


# --- analyze_query -----------------------------------------------------------------------------


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def heuristic_analysis(question: str, language: str) -> QueryAnalysis:
    years = sorted({int(year) for year in _YEAR.findall(question)})
    scope = TimeScope()
    if years:
        scope = TimeScope(
            start=date(years[0], 1, 1),
            end=date(years[-1], 12, 31),
            description=f"{years[0]}-{years[-1]}" if len(years) > 1 else str(years[0]),
        )
    words = question.split()
    entities = []
    for match in _ENTITY.finditer(question):
        token = match.group(0).strip(".")
        if match.start() == 0 and not token.isupper() and len(words) > 1:
            continue
        if token not in entities:
            entities.append(token)
    return QueryAnalysis(
        language=language,
        intent=question,
        entities=entities[:8],
        time_scope=scope,
        answer_type="action_plan" if _ACTION_WORDS.search(question) else "overview",
        fallback=True,
        rationale="Heuristic analysis (the model was unavailable).",
    )


async def analyze_query(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    try:
        result = await deps.predictor(
            "analyze_query",
            events,
            question=state.question,
            today=deps.today.isoformat(),
            skills_menu=menu(deps.skills),
        )
        out: AnalyzeOutput = result.value
        start, end = _parse_date(out.time_scope.start), _parse_date(out.time_scope.end)
        if start and end and start > end:
            start, end = end, start
        analysis = QueryAnalysis(
            language=out.language,
            intent=out.intent,
            entities=[e for e in dict.fromkeys(out.entities) if e.strip()][:10],
            time_scope=TimeScope(start=start, end=end, description=out.time_scope.description),
            answer_type=out.answer_type,
            domain=out.domain,
            source_hints=out.source_hints[:8],
            skills=out.skills,
            rationale=out.rationale,
        )
    except LLMFailure:
        analysis = heuristic_analysis(state.question, state.language)

    # The detector is decisive when the text has Turkish letters; otherwise trust the model.
    if detect_language(state.question) == "tr":
        analysis.language = "tr"
    state.language = analysis.language
    state.analysis = analysis

    chosen = select(analysis.skills, deps.skills)
    state.skills = [skill.name for skill in chosen]
    analysis.skills = state.skills
    deps.predictor.skill_guidance = guidance(chosen)
    domains: dict[int, list[str]] = {}
    for skill in chosen:
        for tier, listed in skill.domains.items():
            domains.setdefault(tier, []).extend(listed)
    state.skill_domains = domains
    deps.tiers = deps.tiers.extended(domains)

    scope = analysis.time_scope.description or "no explicit time frame"
    await events.info(
        EventType.DECISION,
        f"Intent: {analysis.intent} Scope: {scope}.",
        label="Analyzer",
        data={
            "analysis": analysis.model_dump(mode="json"),
            "rationale": analysis.rationale,
            "fallback": analysis.fallback,
        },
    )
    if chosen:
        await events.info(
            EventType.SKILLS_ACTIVATED,
            f'Activated: {", ".join(state.skills)} - "{analysis.rationale[:120]}"',
            label="Skills",
            data={"skills": state.skills, "added_domains": domains},
        )


# --- plan --------------------------------------------------------------------------------------


def _fallback_plan(state: ResearchState) -> list[SubQuestion]:
    facets = [
        Facet(
            id="s1f1", name="direct answer", description="A sourced answer to the question itself."
        )
    ]
    if state.analysis.answer_type == "action_plan":
        facets.append(
            Facet(
                id="s1f2",
                name="obligations",
                description="The requirements the actions must satisfy.",
            )
        )
    return [SubQuestion(id="s1", text=state.question, priority="must", facets=facets)]


def normalise_plan(out: PlanOutput, state: ResearchState, deps: AgentDeps) -> list[SubQuestion]:
    settings = deps.settings.plan
    kept: list[SubQuestion] = []
    ranked = sorted(out.subquestions, key=lambda sq: 0 if sq.priority == "must" else 1)
    for candidate in ranked:
        text = " ".join(candidate.text.split())
        if not text or any(token_jaccard(text, other.text) >= 0.8 for other in kept):
            continue
        subq_id = f"s{len(kept) + 1}"
        facets: list[Facet] = []
        for facet in candidate.facets:
            name = " ".join(facet.name.split())
            if not name or any(token_jaccard(name, f.name) >= 0.8 for f in facets):
                continue
            facets.append(
                Facet(
                    id=f"{subq_id}f{len(facets) + 1}",
                    name=name,
                    description=facet.description.strip(),
                )
            )
            if len(facets) >= settings.max_facets_per_subquestion:
                break
        if not facets:
            facets = [Facet(id=f"{subq_id}f1", name="direct answer", description=text)]
        kept.append(
            SubQuestion(
                id=subq_id,
                text=text,
                priority=candidate.priority,
                facets=facets,
                expected_sources=candidate.expected_sources[:5],
            )
        )
        if len(kept) >= settings.max_subquestions:
            break
    if kept and not any(subq.priority == "must" for subq in kept):
        kept[0].priority = "must"
    return kept


async def plan(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    settings = deps.settings.plan
    rationale = ""
    fallback = False
    try:
        result = await deps.predictor(
            "plan",
            events,
            question=state.question,
            analysis=state.analysis.model_dump(mode="json", exclude={"fallback"}),
            min_subquestions=settings.min_subquestions,
            max_subquestions=settings.max_subquestions,
            max_facets=settings.max_facets_per_subquestion,
        )
        subquestions = normalise_plan(result.value, state, deps)
        rationale = result.value.rationale
    except LLMFailure:
        subquestions = []
    if not subquestions:
        subquestions = _fallback_plan(state)
        fallback = True
        rationale = "Fallback plan: the question itself as a single sub-question."
    state.plan = subquestions

    musts = sum(1 for subq in subquestions if subq.priority == "must")
    await events.info(
        EventType.PLAN_CREATED,
        f"Created {len(subquestions)} research tasks.",
        label="Planner",
        data={
            "rationale": rationale,
            "fallback": fallback,
            "must": musts,
            "subquestions": [
                {
                    "id": s.id,
                    "text": s.text,
                    "priority": s.priority,
                    "facets": [f.name for f in s.facets],
                }
                for s in subquestions
            ],
        },
    )
