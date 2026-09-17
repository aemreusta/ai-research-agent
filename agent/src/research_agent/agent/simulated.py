"""A rule-based stand-in for the LLM, so the whole graph runs offline.

It reads the rendered prompts (the templates are ours, so the JSON sections are easy to find)
and answers every signature the way a careful but unimaginative model would: claims are real
sentences copied from the page, the report restates findings verbatim, verification checks
containment. Scenario tests and `research run --simulate` use it; nothing in production does.

It is deliberately *not* clever. Its value is that the orchestration, the ledger and the gate
are exercised end to end with outputs whose correctness is obvious.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from research_agent.agent.clustering import normalise_entity
from research_agent.agent.text import detect_language, overlap_ratio, tokens
from research_agent.gate.numeric import extract_quantities
from research_agent.providers.llm.base import Message
from research_agent.providers.llm.catalog import ModelCatalog, Tier
from research_agent.providers.llm.fake import FakeLLMProvider
from research_agent.providers.search.fake import CorpusPage, FakeSearchProvider

_DOC = re.compile(
    r'<untrusted_source id="(?P<id>[^"]+)"[^>]*>\n(?P<body>.*?)\n</untrusted_source>', re.DOTALL
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9\"'])")
_CAPS = re.compile(r"\b[A-ZÇĞİÖŞÜ][\w'\u2019&.-]*(?:\s+[A-ZÇĞİÖŞÜ][\w'\u2019&.-]*)*")


def _after(text: str, heading: str) -> Any:
    start = text.find(heading)
    if start < 0:
        return None
    index = start + len(heading)
    while index < len(text) and text[index] not in "[{":
        index += 1
    if index >= len(text):
        return None
    value, _ = json.JSONDecoder().raw_decode(text, index)
    return value


def _line_after(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    rest = text[start + len(heading) :].lstrip("\n")
    return rest.split("\n\n", 1)[0].strip()


def _entity(sentence: str) -> str | None:
    for match in _CAPS.finditer(sentence):
        candidate = match.group(0).strip(".")
        if match.start() == 0 and " " not in candidate and not candidate.isupper():
            continue
        if len(candidate) > 1:
            return candidate
    return None


def _attribute(sentence: str) -> str:
    """The sentence minus its quantities: two sentences that differ only in a value share it."""
    stripped = sentence
    for quantity in sorted(
        extract_quantities(sentence, language=detect_language(sentence)), key=lambda q: -q.start
    ):
        stripped = stripped[: quantity.start] + stripped[quantity.end :]
    words = tokens(stripped)
    entity = _entity(sentence)
    drop = set(tokens(entity)) if entity else set()
    return " ".join(word for word in words if word not in drop)[:80]


def respond(schema: str, messages: list[Message]) -> dict[str, Any]:
    prompt = messages[-1].content
    handler = _HANDLERS[schema]
    return handler(prompt)


def _analyze(prompt: str) -> dict[str, Any]:
    question = _line_after(prompt, "Question:")
    lowered = question.lower()
    skills = []
    if any(word in lowered for word in ("kvkk", "regülasyon", "mevzuat", "kanun", "yönetmelik")):
        skills.append("regulatory-research-tr")
    if any(word in lowered for word in ("market size", "pazar büyüklüğü", "market share")):
        skills.append("market-sizing")
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", question)]
    scope = (
        {
            "start": f"{min(years)}-01-01",
            "end": f"{max(years)}-12-31",
            "description": str(min(years)),
        }
        if years
        else {"start": None, "end": None, "description": ""}
    )
    action = any(word in lowered for word in ("plan", "aksiyon", "öneri", "should", "recommend"))
    return {
        "rationale": "Simulated analysis.",
        "language": detect_language(question),
        "intent": question,
        "entities": [m.group(0) for m in _CAPS.finditer(question)][:5],
        "time_scope": scope,
        "answer_type": "action_plan" if action else "overview",
        "domain": "general",
        "source_hints": ["official sources"],
        "skills": skills,
    }


def _plan(prompt: str) -> dict[str, Any]:
    question = _line_after(prompt, "Question:")
    return {
        "rationale": "Simulated plan: the question and its official basis.",
        "subquestions": [
            {
                "text": question,
                "priority": "must",
                "facets": [{"name": "direct answer", "description": question}],
                "expected_sources": ["official"],
            },
        ],
    }


def _queries(prompt: str) -> dict[str, Any]:
    targets = _after(prompt, "Targets (with how many queries each may get):") or []
    queries = []
    for target in targets:
        base = (target["facet"] and f"{target['sub_question']} {target['facet']}") or target[
            "sub_question"
        ]
        variants = [base, f"{base} official", f"{base} latest"]
        if target.get("purpose") == "conflict":
            variants = [f"{target['detail']} official source"]
        for text in variants[: target["max_queries"]]:
            queries.append(
                {
                    "subq_id": target["subq_id"],
                    "facet_id": target["facet_id"],
                    "text": text,
                    "purpose": target["purpose"],
                    "rationale": "simulated",
                }
            )
    return {"rationale": "Simulated queries.", "queries": queries}


def _evaluate(prompt: str) -> dict[str, Any]:
    subquestions = _after(prompt, "Sub-questions:") or []
    focus = " ".join(item["question"] for item in subquestions)
    items = []
    for match in _DOC.finditer(prompt):
        relevance = min(1.0, 0.3 + overlap_ratio(focus, match["body"]))
        items.append(
            {
                "doc_id": match["id"],
                "relevance": round(relevance, 2),
                "is_primary": ".gov" in prompt[match.start() : match.start() + 200]
                or "europa.eu" in prompt[match.start() : match.start() + 200],
                "authority_adjustment": 0.0,
                "reason": "simulated",
            }
        )
    return {"rationale": "Simulated evaluation.", "items": items}


def _extract(prompt: str) -> dict[str, Any]:
    payload = _after(prompt, "Sub-questions and facets:") or {}
    subquestions = payload.get("subquestions", [])
    match = _DOC.search(prompt)
    if match is None or not subquestions:
        return {"rationale": "nothing to extract", "claims": []}
    body = match["body"].split("\n\n", 1)[-1]
    claims = []
    for sentence in _SENTENCE.split(" ".join(body.split())):
        sentence = sentence.strip()
        if len(sentence) < 25:
            continue
        best = max(subquestions, key=lambda s: overlap_ratio(s["question"], sentence))
        if overlap_ratio(best["question"], sentence) < 0.25:
            continue
        quantities = extract_quantities(sentence, language=detect_language(sentence))
        claims.append(
            {
                "subq_id": best["id"],
                "facet_id": best["facets"][0]["id"] if best["facets"] else None,
                "text": sentence,
                "quote": sentence,
                "kind": "number" if quantities else "fact",
                "entity": _entity(sentence),
                "attribute": _attribute(sentence) if quantities else None,
                "value": quantities[0].raw if quantities else None,
                "unit": None,
                "as_of": None,
                "attributed_to": None,
            }
        )
    return {"rationale": "Simulated extraction.", "claims": claims[:8]}


def _judge(prompt: str) -> dict[str, Any]:
    pairs = _after(prompt, "Pairs:") or []
    return {
        "rationale": "Simulated judge.",
        "items": [
            {
                "pair_id": pair["pair_id"],
                "kind": "true_conflict",
                "summary": f"{pair['left']['statement']} / {pair['right']['statement']}",
                "preferred": "none",
                "rationale": "simulated",
            }
            for pair in pairs
        ],
    }


def _coverage(prompt: str) -> dict[str, Any]:
    status = _after(prompt, "Progress:") or []
    return {
        "rationale": "Simulated coverage.",
        "items": [{"subq_id": item["subq_id"], "missing": [], "note": ""} for item in status],
    }


def _synthesize(prompt: str) -> dict[str, Any]:
    ledger = _after(prompt, "Findings (the ledger):") or []
    language = _line_after(prompt, "Report language:").split(".")[0].strip() or "en"
    settled = [f for f in ledger if f["status"] != "contested"]
    contested = [f for f in ledger if f["status"] == "contested"]
    action = "action_plan" in prompt
    framing = {
        "en": "Overall, this report lists what the sources state.",
        "tr": "Genel olarak, bu rapor kaynakların belirttiklerini listeler.",
    }
    return {
        "rationale": "Simulated synthesis.",
        "title": "Research report",
        "summary": [{"text": f["statement"], "cluster_ids": [f["id"]]} for f in settled[:2]],
        "key_findings": [{"text": f["statement"], "cluster_ids": [f["id"]]} for f in settled],
        "conflicting": [{"text": f["statement"], "cluster_ids": [f["id"]]} for f in contested],
        "recommendations": (
            [{"text": f"Review: {settled[0]['statement']}", "finding_refs": [settled[0]["id"]]}]
            if action and settled
            else []
        ),
        "conclusion": [{"text": framing.get(language, framing["en"]), "cluster_ids": []}],
    }


def _verify(prompt: str) -> dict[str, Any]:
    sentences = _after(prompt, "Sentences with their cited findings:") or []
    items = []
    for item in sentences:
        cited = " ".join(item["cited_findings"])
        supported = item["sentence"] in cited or overlap_ratio(item["sentence"], cited) >= 0.8
        items.append(
            {
                "sentence_id": item["sentence_id"],
                "supported": supported,
                "reason": "" if supported else "not contained in the cited findings",
            }
        )
    return {"items": items}


def _validate(prompt: str) -> dict[str, Any]:
    claims = _after(prompt, "Candidate claims:") or []
    # Offline orchestration stand-in only; semantic quality is measured separately on labels.
    return {
        "items": [
            {
                "claim_id": c["claim_id"],
                "supported": True,
                "conditions_complete": True,
                "time_sensitive": c.get("time_sensitive", False),
                "reason": "simulated source-copy validation",
            }
            for c in claims
        ]
    }


_HANDLERS = {
    "AnalyzeOutput": _analyze,
    "PlanOutput": _plan,
    "QueriesOutput": _queries,
    "SourceJudgements": _evaluate,
    "ExtractionOutput": _extract,
    "ClaimValidationOutput": _validate,
    "ContradictionJudgements": _judge,
    "CoverageOutput": _coverage,
    "SynthesisOutput": _synthesize,
    "VerificationOutput": _verify,
}


def simulated_llm(name: str = "simulated") -> FakeLLMProvider:
    return FakeLLMProvider(name, default=respond)


def simulated_catalog() -> ModelCatalog:
    """A one-provider chain with zero prices, so an offline run never shows a fake cost."""
    return ModelCatalog(
        version=1,
        chain=("simulated",),
        tiers={tier: {"simulated": f"simulated-{tier.value}"} for tier in Tier},
        prices_usd_per_million_tokens={
            f"simulated-{tier.value}": {"input": 0.0, "output": 0.0} for tier in Tier
        },
    )


DEMO_CORPUS = [
    CorpusPage(
        url="https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai",
        title="AI Act | Shaping Europe's digital future",
        published_at=date(2026, 5, 20),
        text=(
            "The AI Act entered into force on 1 August 2024. "
            "The EU AI Act obligations for general-purpose AI models apply from 2 August 2025. "
            "The EU AI Act rules for high-risk AI systems apply from 2 August 2026. "
            "The Commission publishes guidelines to support the application of the AI Act."
        ),
    ),
    CorpusPage(
        url="https://www.reuters.com/technology/eu-ai-act-timeline",
        title="EU sticks to AI Act timeline",
        published_at=date(2026, 3, 2),
        text=(
            "The EU AI Act obligations for general-purpose AI models apply from 2 August 2025, "
            "officials confirmed. The EU AI Act rules for high-risk AI systems apply from "
            "2 August 2027 under a proposed delay, according to industry groups."
        ),
    ),
    CorpusPage(
        url="https://techblog.example.com/ai-act-explained",
        title="The AI Act explained",
        published_at=date(2025, 11, 5),
        text=(
            "The EU AI Act obligations for general-purpose AI models apply from 2 August 2025. "
            "Many startups say the EU AI Act timeline is confusing."
        ),
    ),
]


def simulated_search(
    name: str = "tavily", corpus: list[CorpusPage] | None = None
) -> FakeSearchProvider:
    return FakeSearchProvider(name, corpus=corpus if corpus is not None else DEMO_CORPUS)


def entity_key(value: str | None) -> str | None:
    return normalise_entity(value)


def simulated_toolkit() -> Any:
    """Everything `research run --simulate` needs: model, search and a zero-price catalog."""
    from research_agent.agent.research import Toolkit

    return Toolkit(
        llm={"simulated": simulated_llm()},
        search={"tavily": simulated_search()},
        catalog=simulated_catalog(),
    )
