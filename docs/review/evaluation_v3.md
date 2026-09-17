# Project evaluation v3 — implementation and verification

Date: 2026-09-17. Baseline: `f8fc007`, independently reviewed in evaluation v2. This report
records the implemented changes and fresh evidence. It supersedes the v1 implementation brief.

The project is materially stronger in ownership, failure recovery, evidence admission and user
control. It now supports explicit model selection and visible heuristic review steps. It remains
an assisted research system: structurally valid, well-cited reports can contain incomplete or
misleading interpretations. A gate pass or 20-case regression pass is not a factual certificate.
I am not replacing the earlier estimated reviewer score with an unmeasured “near-perfect” score.

## Changes that close verified defects

| Area | Implemented behavior | Verification |
|---|---|---|
| Attempt ownership | Fresh lease for every dispatch; old workers cannot heartbeat, append events, checkpoint or complete after losing ownership | Concurrent PostgreSQL tests and fenced saver tests |
| Deadline | Deadline termination ignores heartbeat movement while checking the active lease | Watchdog/store regression with a changing heartbeat |
| Completion | Status, counters, artifacts, secret deletion and terminal/report-ready events commit together | Integration tests for stale owners and transaction behavior |
| Recovery | Original started time/deadline retained; completed checkpoints settle without rerunning paid graph nodes; skills/domain config restored | Checkpoint integration tests and actual container SIGKILL |
| Search | Empty-result retry/fallback occurs outside the acquired semaphore | Saturation regression at one and multiple slots |
| SSE | Terminal transition drains every pending page, including a backlog over 200 | Live-connected 251-event regression |
| Gate | Exactly the configured remediation rounds; zero is report-only | Boundary tests |
| Verifiers | Complete unique verdicts required; missing answers retry then fail closed; recommendations checked; original context included | Negative/missing/duplicate verdict and fallback regressions |
| Exports | Report JSON is an actual API artifact, with model selection metadata | API tests and all new exported examples |

Other fixes cover cancellation/accept races, lease-aware retry, reconnect wake-ups, negative
configuration bounds, reserved logging fields, CLI/watchdog separation and restored runtime
configuration on resume. The external dispatcher bounds an attempt's valid lifetime; this does
not promise to undo an HTTP request already sent to a provider or recover unreported token usage.

## Models and heuristics

Reasoning and fast models can be selected independently in New research, API overrides, the CLI
and saved presets. The selected model is tried first; provider fallback is explicit and can be
disabled. Unknown model IDs and missing credentials fail before queuing. Requested and used
models are visible. Gemini Pro long-context pricing is recorded; Gemini 2.5 Pro omits the
unsupported `thinkingLevel` parameter after a real API failure exposed it.

A browser-started run used Gemini 3.1 Pro for reasoning and Flash-Lite for extraction with fallback
disabled. A separate Gemini 2.5 Pro live probe passed. OpenAI Sol/Terra/Luna requests reached the
selected model but returned HTTP 429/no credits remaining. Their routing, typed requests and
failure behavior are tested; their live report quality is **unverified**. The exposed Astra option
was checked against provider documentation and schema tests, not a successful paid live run.

H1 checks plan entity coverage; H2 current applicability; H3 support verdicts; H4 detected scope
restrictions; H5 independent origins; H6 citation/numeric integrity before remediation. These are
visible in the timeline and Heuristic checks tab and retained in state exports. A warning points
to evidence to inspect; a pass does not prove the report correct.

## Evidence quality and lessons from the live runs

Claims retain conditions, effect dates, time sensitivity, quote-match method, validation reason
and freshness. Mutable historical/undated values cannot establish a current answer. Claims with
different conditions do not merge. Publisher/copy/attribution independence is counted across a
facet. Domain resemblance no longer makes `kvkkuyum.com` or a site named for the EU AI Act primary.

The original old VERBİS value and original EU condition-loss fixture are rejected. Twenty labeled
claim regressions passed the final v4 experiment (precision/recall/availability/entailment all
1.00; $0.054564, 67.82 seconds). This is a small set used during development, not held-out accuracy.
Dataset v2 also corrected the historical positive control's question; earlier runs are preserved
but are not a controlled before/after comparison. See [eval instructions](../../evals/README.md).

The eight repeat research cases were **not** uniformly correct despite clean structural checks:

- Apilex retrieval improved substantially; actual company pages were found and missing public
  partnership evidence stayed a gap. Company-reported metrics are not independently verified.
- The comparison plan now preserves both Shopify and Wix. Actual Wix revenue still was not
  retrieved in the saved run, and its conclusion overstates what can be inferred from a scheduled
  earnings announcement. A missing result is a retrieval gap, not evidence it does not exist.
- The fictional-company case correctly declines to invent a business/product; gate `fail` with
  `no_evidence` is expected here, not an infrastructure failure.
- A PostgreSQL Pro report overgeneralized UUID monotonicity. The final citation verifier was moved
  to the selected reasoning tier; a live Pro negative/positive pair then correctly rejected
  cross-backend monotonicity and accepted the within-backend qualification. This pair is not a
  full repeated end-to-end benchmark of the technical report.
- The `2026-09-17-legal-final` rerun still repeated an overbroad transparency deadline from official
  overview pages. Its KVKK answer also treated a law-firm article's DPA/24-hour advice as a legal
  rule. Those outputs are preserved. Better source entailment alone cannot fix an oversimplified
  source. The final admission policy now withholds rule-bearing regulatory claims from secondary
  commentary and identified official overview/press pages, asking for operative rules or detailed
  regulator guidance. This is conservative and can reduce coverage; it is a heuristic, not a
  universal legal-authority classifier.

The post-policy EU rerun retained the narrow legacy-system qualifier, with more Known Gaps
and no high-risk deadline dates in its findings. The KVKK rerun removed the 24-hour law-firm
assertion but still misinterpreted who chooses to notify under a standard contract, and quoted
old penalty ranges from a recently published enforcement decision. Follow-up regressions now
withhold that old-decision monetary claim and the Turkish “zorundadır” commentary claim, and
apply the legal policy to all time-sensitive claims. A deterministic replay confirms those two
withholds; a further full research run after these last guards was not performed. Contract-role
interpretation remains a documented semantic limitation.

The eight repeat exports contain 638 candidate claims: 590 exact and 48 fuzzy quote matches under
the production matcher, no rejected quote matches, no dangling finding references and matching
source lists. Replayed G4/G12 found no violations. Eight of 80 cited-document occurrences lacked
publication dates. These are structural measurements; the replay deliberately reuses production
checks and therefore is **not independent semantic validation**.

[The results index](../../examples/2026-09-17-RESULTS.md) records every batch, costs, durations and
the additional post-policy legal rerun. Directory names containing “final” identify an iteration,
not a certification of correctness. Full state snapshots are losslessly compressed for Git.

## Reliability evidence and reproducibility

`make verify` passed **543 Python tests** (after the Turkish-letter and label change below), all Go packages including the race detector, Ruff,
strict mypy (155 source/test files), Go formatting/vet and offline fixtures. The Docker suite passed the then **538 tests** in 20.72 seconds against a separate test
database inside the Compose network. A GitHub Actions
workflow runs the same categories with PostgreSQL and no paid keys; its remote run on the pushed
`main` passed.

The real crash test killed an agent container during extraction after a completed search
checkpoint. The dispatcher reclaimed the run as attempt 2. It completed with 346 gap-free events,
exactly one intake/plan execution and one report-ready/terminal event. The test also exposed the
started-time reset, which was fixed afterward. The saved pre-fix manifest's 219-second DB duration
understates its 306-second wall-clock observation. That artifact has not been silently corrected.

Controlled tests cover 401/429/5xx, malformed responses/repair failure, empty search, cancellation,
budgets and optional-service degradation. An actual local TCP server which never responds tests
read timeout and provider fallback. The hard-deadline/heartbeat race is tested against PostgreSQL;
a separate full-stack hung-but-heartbeating fault injection was not performed.

## Report wording (post-review change)

A completeness review against the case found two presentation defects in the retained reports:

- **ASCII-only Turkish.** The `2026-09-17-final` ApilexAI report was written without Turkish
  letters ("Sirket", "yatirim").
  - Synthesis now detects such a draft and rewrites it once with feedback.
  - If the report is still ASCII-only, G10 raises a warning.
  - The `synthesize` seed is now version 4 with an explicit spelling rule.
- **Misleading "single source" label.** The label appeared on sentences with several citations.
  Those citations were pages of one publisher, which count as one independent source, so the label
  now reads "single independent source" / "tek bağımsız kaynak".

Unit tests cover the detection, the single rewrite, the G10 warning and the label. No new live
run was made for this change; the retained reports are not edited.

## Remaining limits

1. Retrieval coverage and source interpretation remain the largest risks. Explicit conditions,
   stronger verification and primary-rule requirements improve evidence without proving truth.
   Coverage itself is heuristic: generic facts can satisfy a poorly defined facet, and the model
   can mark an important sub-question optional. Larger unseen evaluations are still needed.
2. Date extraction distinguishes publication from observation, but missing publication dates or
   ambiguous legal effect dates can withhold valid rules. Historical laws often remain in force;
   this conservative policy trades recall for current applicability evidence.
3. Publisher matching, regulatory-domain classification and overview detection are heuristics.
   They are not domain ownership verification or comprehensive legal source classification.
4. LLM costs exclude search credits/infrastructure and can omit usage interrupted before recording.
   Default budget gates remain configurable; different selected models have different costs.
5. No authentication or multitenancy is provided for this local deployment. DSPy/GEPA optimization,
   broader threshold calibration and production operations remain outside this improvement pass.

No email, reviewer invitation or deployment was performed. Changes are committed in Conventional
Commit groups and pushed to the private repository; local evidence is available without provider keys.
