"""Read models for the dashboard screens: costs, prompts & skills, and a run's ledger."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Float, and_, case, cast, func, select

from research_agent.api.deps import Api
from research_agent.db.models import LlmCall, Run, RunArtifact, SearchCall
from research_agent.observability.langfuse import LangfuseClient, langfuse_settings
from research_agent.prompting.registry import PromptProblem, PromptRegistry, check_version
from research_agent.prompting.signatures import SIGNATURES
from research_agent.prompting.skills import load_skills

router = APIRouter(tags=["insights"])


def _money(value: Any) -> float:
    return round(float(value or 0), 6)


@router.get("/api/costs")
async def costs(api: Api, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict[str, Any]:
    """The `#/costs` screen (D33): totals, and breakdowns by model, node and run."""
    async with api.sessionmaker() as session:
        totals = (
            await session.execute(
                select(
                    func.count(Run.id),
                    func.coalesce(func.sum(Run.cost_usd), 0),
                    func.coalesce(func.sum(Run.tokens_in), 0),
                    func.coalesce(func.sum(Run.tokens_out), 0),
                    func.coalesce(func.sum(Run.searches_used), 0),
                )
            )
        ).one()
        llm_calls = (await session.execute(select(func.count(LlmCall.id)))).scalar_one()
        cache = (
            await session.execute(
                select(
                    func.count(SearchCall.id),
                    func.coalesce(func.sum(case((SearchCall.cache_hit.is_(True), 1), else_=0)), 0),
                )
            )
        ).one()

        by_model = (
            await session.execute(
                select(
                    LlmCall.provider,
                    LlmCall.model,
                    LlmCall.tier,
                    func.count(LlmCall.id),
                    func.sum(LlmCall.tokens_in),
                    func.sum(LlmCall.tokens_out),
                    func.sum(LlmCall.cost_usd),
                    func.percentile_cont(0.5).within_group(LlmCall.latency_ms),
                    func.percentile_cont(0.95).within_group(LlmCall.latency_ms),
                    func.sum(case((LlmCall.status != "ok", 1), else_=0)),
                )
                .group_by(LlmCall.provider, LlmCall.model, LlmCall.tier)
                .order_by(func.sum(LlmCall.cost_usd).desc())
            )
        ).all()

        by_node = (
            await session.execute(
                select(
                    LlmCall.node,
                    func.count(LlmCall.id),
                    func.sum(LlmCall.cost_usd),
                    cast(func.avg(LlmCall.latency_ms), Float),
                )
                .group_by(LlmCall.node)
                .order_by(func.sum(LlmCall.cost_usd).desc())
            )
        ).all()

        by_search = (
            await session.execute(
                select(
                    SearchCall.provider,
                    func.count(SearchCall.id),
                    func.sum(case((SearchCall.cache_hit.is_(True), 1), else_=0)),
                    func.sum(case((SearchCall.status != "ok", 1), else_=0)),
                    cast(func.avg(SearchCall.latency_ms), Float),
                ).group_by(SearchCall.provider)
            )
        ).all()

        runs = (
            await session.execute(
                select(
                    Run.id,
                    Run.question_masked,
                    Run.status,
                    Run.cost_usd,
                    Run.tokens_in,
                    Run.tokens_out,
                    Run.searches_used,
                    Run.created_at,
                    Run.finished_at,
                    Run.started_at,
                )
                .order_by(Run.created_at.desc())
                .limit(limit)
            )
        ).all()

    searches, hits = int(cache[0]), int(cache[1])
    return {
        "totals": {
            "runs": int(totals[0]),
            "cost_usd": _money(totals[1]),
            "tokens_in": int(totals[2]),
            "tokens_out": int(totals[3]),
            "searches": int(totals[4]),
            "llm_calls": int(llm_calls),
            "avg_cost_per_run": _money(float(totals[1]) / totals[0]) if totals[0] else 0.0,
            "search_cache_hit_rate": round(hits / searches, 4) if searches else 0.0,
        },
        "by_model": [
            {
                "provider": r[0],
                "model": r[1],
                "tier": r[2],
                "calls": int(r[3]),
                "tokens_in": int(r[4] or 0),
                "tokens_out": int(r[5] or 0),
                "cost_usd": _money(r[6]),
                "latency_p50_ms": round(float(r[7] or 0)),
                "latency_p95_ms": round(float(r[8] or 0)),
                "failures": int(r[9] or 0),
            }
            for r in by_model
        ],
        "by_node": [
            {
                "node": r[0],
                "calls": int(r[1]),
                "cost_usd": _money(r[2]),
                "avg_latency_ms": round(float(r[3] or 0)),
            }
            for r in by_node
        ],
        "by_search_provider": [
            {
                "provider": r[0],
                "calls": int(r[1]),
                "cache_hits": int(r[2] or 0),
                "failures": int(r[3] or 0),
                "avg_latency_ms": round(float(r[4] or 0)),
            }
            for r in by_search
        ],
        "runs": [
            {
                "run_id": str(r[0]),
                "question": r[1],
                "status": r[2],
                "cost_usd": _money(r[3]),
                "tokens_in": r[4],
                "tokens_out": r[5],
                "searches": r[6],
                "created_at": r[7].isoformat(),
                "duration_seconds": ((r[8] - (r[9] or r[7])).total_seconds() if r[8] else None),
            }
            for r in runs
        ],
    }


@router.get("/api/prompts")
async def prompts() -> dict[str, Any]:
    """`#/prompts`: what each signature would use right now, and where to edit it."""
    settings = langfuse_settings()
    remote = None
    reachable = False
    async with httpx.AsyncClient() as http:
        if settings is not None:
            client = LangfuseClient(settings, http)
            reachable = await client.alive()
            remote = client if reachable else None
        registry = PromptRegistry()
        items = []
        for signature in SIGNATURES.values():
            seed = registry.seed(signature.id)
            active = seed
            problem = None
            if remote is not None:
                try:
                    candidate = await remote.get_prompt(signature.id, label="production")
                except httpx.HTTPError as exc:
                    candidate, problem = None, f"unreachable: {type(exc).__name__}"
                if candidate is not None:
                    try:
                        check_version(candidate, signature)
                        active = candidate
                    except PromptProblem as exc:
                        problem = str(exc)
            items.append(
                {
                    "id": signature.id,
                    "tier": signature.tier.value,
                    "output": signature.output.__name__,
                    "schema_hash": signature.schema_hash,
                    "inputs": sorted(signature.inputs),
                    "uses_skills": signature.uses_skills,
                    "active": {
                        "source": active.source,
                        "version": active.version,
                        "content_hash": active.content_hash,
                        "url": active.url,
                    },
                    "seed_version": seed.version,
                    "description": seed.description,
                    "compatible": problem is None,
                    "problem": problem,
                    "edit_url": (
                        f"{settings['public_url']}/project/{settings['project']}/prompts/"
                        f"{signature.id}"
                        if settings
                        else None
                    ),
                }
            )
    return {
        "langfuse": {
            "configured": settings is not None,
            "reachable": reachable,
            "url": settings["public_url"] if settings else None,
        },
        "prompts": items,
    }


@router.get("/api/skills")
async def skills() -> dict[str, Any]:
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "domains": {str(k): v for k, v in s.domains.items()},
                "query_patterns": s.query_patterns,
                "guidance": s.body,
            }
            for s in load_skills().values()
        ]
    }


@router.get("/api/runs/{run_id}/ledger")
async def ledger(run_id: Annotated[uuid.UUID, Path()], api: Api) -> JSONResponse:
    """Plan, sources, findings and contradictions of a finished run (from its state artifact)."""
    async with api.sessionmaker() as session:
        artifact = (
            await session.execute(
                select(RunArtifact.content).where(
                    and_(RunArtifact.run_id == run_id, RunArtifact.kind == "state")
                )
            )
        ).scalar_one_or_none()
    if artifact is None:
        return JSONResponse(
            status_code=404,
            content={"error_code": "NOT_FOUND", "message": "no ledger yet", "expected": True},
        )
    state = json.loads(artifact)
    documents = [
        {
            key: doc.get(key)
            for key in (
                "id",
                "url",
                "domain",
                "title",
                "published_at",
                "provider",
                "origin_id",
                "extracted",
                "fetched",
                "fetch_error",
                "triage_score",
                "score",
                "subq_ids",
            )
        }
        for doc in state.get("documents", {}).values()
    ]
    documents.sort(key=lambda d: -(d.get("score") or {}).get("total", 0))
    return JSONResponse(
        content={
            "question": state.get("question"),
            "language": state.get("language"),
            "analysis": state.get("analysis"),
            "skills": state.get("skills"),
            "plan": state.get("plan"),
            "queries": state.get("queries"),
            "documents": documents,
            "claims": list(state.get("claims", {}).values()),
            "clusters": list(state.get("clusters", {}).values()),
            "contradictions": state.get("contradictions"),
            "stop_reason": state.get("stop_reason"),
            "stop_detail": state.get("stop_detail"),
            "counters": state.get("counters"),
            "budget": state.get("budget"),
        }
    )
