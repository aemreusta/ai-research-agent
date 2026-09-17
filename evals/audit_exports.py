"""Replay structural evidence checks over exported states; not a factual-accuracy benchmark."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path

from research_agent.agent.quotes import verify_quote
from research_agent.agent.state import ResearchState
from research_agent.gate.config import GateConfig
from research_agent.gate.rules import g4_numbers, g12_evidence_eligibility


def audit(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted([*root.glob("*/state.json"), *root.glob("*/state.json.gz")]):
        payload = (
            gzip.decompress(path.read_bytes()).decode()
            if path.suffix == ".gz"
            else path.read_text()
        )
        state = ResearchState.model_validate_json(payload)
        report = state.report
        if report is None:
            raise ValueError(f"Missing report in {path}")
        citations = {
            cid
            for _, sentence in report.sentences()
            for cid in [*sentence.cluster_ids, *sentence.finding_refs]
        }
        docs = {
            did for cid in citations if cid in state.clusters for did in state.clusters[cid].doc_ids
        }
        quotes = [
            verify_quote(
                c.quote, state.documents[c.doc_id].content or state.documents[c.doc_id].snippet
            )
            for c in state.claims.values()
        ]
        numeric = g4_numbers(report, state, GateConfig.load())
        rows.append(
            {
                "case": path.parent.name,
                "claims": len(state.claims),
                "cited_documents": len(docs),
                "quote_methods": dict(Counter(q.method for q in quotes)),
                "invalid_quotes": sum(not q.ok for q in quotes),
                "dangling_references": len(citations - state.clusters.keys()),
                "source_list_mismatch": len(docs ^ {s.doc_id for s in report.sources}),
                "undated_cited_documents": sum(
                    state.documents[d].published_at is None for d in docs
                ),
                "numeric_violations_replay": sum(v.severity == "error" for v in numeric),
                "numeric_warnings_replay": sum(v.severity != "error" for v in numeric),
                "ineligible_citations_replay": len(
                    g12_evidence_eligibility(report, state, GateConfig.load())
                ),
                "stop_reason": state.stop_reason,
                "gate_verdict": (state.gate_result or {}).get("verdict"),
            }
        )
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    rows = audit(args.directory)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(rows, indent=2))
    failures = (
        "invalid_quotes",
        "dangling_references",
        "source_list_mismatch",
        "numeric_violations_replay",
        "ineligible_citations_replay",
    )
    raise SystemExit(0 if rows and not any(row[key] for row in rows for key in failures) else 1)
