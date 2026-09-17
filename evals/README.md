# Evidence evaluation and acceptance checks

Run `make verify` for the ordered, key-free acceptance path: lint/format/types → all Python tests
against disposable PostgreSQL → Go tests including race detection → offline fixture checks.
The disposable database uses port 55432. Never point integration tests at the application DB:
those tests deliberately truncate tables. CI uses an isolated PostgreSQL service and the same
checks; the workflow has not been executed remotely as part of this work.

## Labeled evidence regressions

`cases.json` contains 20 labeled cases: two real audit failures and 18 synthetic traps / positive
controls. Synthetic rules are fixtures, not descriptions of real law. Expected labels are never
included in prompts. Cases cover dropped conditions, AND/OR exemptions, proposals versus enacted
law, jurisdiction, negation, forecast versus actual, stale/undated values and historical scope.

```bash
# Validate quotes and measure the former quote-only baseline; no keys, not a semantic eval.
uv run python evals/run.py

# Real source-entailment model plus production temporal policy; incurs provider cost.
uv run --env-file .env python evals/run.py --live --output evals/results/live.json

# Full research through the running API. The API resolves its own keys. Change port as needed.
uv run python evals/research_runs.py --api http://localhost:8000 \
  --cases evals/live_research_cases.json --out examples/new-run

# Strict selected-model smoke probes; requested IDs are used without provider fallback.
uv run --env-file .env python evals/model_smoke.py gemini-3.1-pro-preview gemini-2.5-pro

# Replay exported structural checks (also reads losslessly compressed state.json.gz).
uv run python evals/audit_exports.py examples/2026-09-17-final --out /tmp/export-audit.json
```

The live evidence evaluator fails unless eligible-claim precision ≥95%, recall ≥80%, verdict
availability =100%, and source-entailment accuracy ≥90%. Recall prevents rejecting everything
from looking successful. This is a **small, development-visible regression set**, not held-out
accuracy. It focuses on entailment and freshness, not end-to-end retrieval or legal-authority
classification; the latter has separate deterministic tests and full live research cases.

Preserved results:

| File | Result | Why retained |
|---|---|---|
| `results/offline.json` | Quote-only precision 0.45, recall 1.00 | Literal grounding alone admits false claims |
| `results/live-v1.json` | Fails thresholds | Fast verifier misses real EU condition loss |
| `results/live-v2.json` | Fails thresholds | Reasoning tier alone does not solve semantics |
| `results/live-v3.json` | Fails thresholds | Prompt changes still miss the real scope trap |
| `results/live-v4.json` | 20/20 labels correct, all four metrics 1.00 | Scoped-source guard plus prompt v4; $0.054564, 67.82 s |

Dataset v2 corrected the historical positive control's question to ask about 2021, consistent
with its label. Earlier results using dataset v1 are not a controlled before/after comparison.
Prompt/model hashes and usage are recorded in results. Passing these 20 cases did **not** prevent
the later legal overview failures in full research; see the results index and evaluation v3.

`research_runs.py` includes eight distinct questions and supports per-case overrides. Its output
manifest records status, timings, usage and exports; gate pass is not a quality label. API GETs
are retried on transport failures; POST is not retried to avoid duplicate paid runs. An existing
manifest resumes polling rather than submitting the same run again.

`audit_exports.py` returns nonzero on missing reports or structural violations. It recalculates
quote matching, citation existence and source-list agreement, then replays production G4/G12.
That reuse is deliberate and **not independent semantic validation**. Recorded fuzzy quote matches
are not described as exact quotations.

`results/crash-resume.json` records an actual agent SIGKILL, recovery to attempt 2, one plan/intake,
346 gap-free events and exactly one terminal/report-ready event. Fake-provider tests additionally
cover auth, rate limiting, server errors, repair failure and optional-service degradation. A real
local TCP server that accepts a connection but never responds exercises HTTP timeout/fallback.

`results/model-smoke.json` preserves successful Gemini 3.1 Pro and OpenAI credit failures.
Gemini 2.5 Pro initially rejected `thinkingLevel`; the adapter fix and successful retry are in
`model-smoke-gemini25-fixed.json`. OpenAI returned 429/no credits remaining, so output quality is
not verified for those models. `final-verifier-pro.json` preserves a live negative/positive pair
for PostgreSQL UUID monotonicity scope.

## Manual heuristic review

1. Open New research → Models. Select a reasoning model, an extraction model and fallback policy.
   Save/load a preset; confirm the controls and submitted overrides agree. Run with fallback off
   when testing a model; inspect both requested and actual models in the completed run.
2. Read the plan and H1: all compared companies, versions or jurisdictions must remain present.
   A well-formed plan is still incomplete if a critical facet was marked optional.
3. Read H2–H4 and rejected ledger candidates. Compare observation date, publication date, effective
   date, jurisdiction, exceptions and AND/OR thresholds. Check operative legal text or regulator
   guidance; do not treat an official press summary as the full rule.
4. Read H5 and the source URLs. Count publishers and copied origins, not links. Verify that a
   domain resembling a law/company name really belongs to that authority.
5. Read H6 and the final Gate tab, then independently sample numerical facts and recommendations
   against original sources. Verify qualifiers beyond the quoted sentence; absence of retrieved
   evidence does not prove that a product, result or legal exception does not exist.
6. Open report JSON, Markdown, state, gate and trace exports. Compare Known Gaps with the question;
   record missing coverage, actual model, cost and elapsed time. A warning/no-evidence result can
   be the correct outcome for a false premise. Preserve unsuccessful reports before changing code.

See [September 17 results](../examples/2026-09-17-RESULTS.md) and
[evaluation v3](../docs/review/evaluation_v3.md) for the measured outcome and remaining limits.
