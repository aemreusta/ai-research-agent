# Project evaluation v2

Date: 2026-09-16. Reviewed commit: `f8fc007`. This is a fresh assessment of the checkout and of the claims in `evaluation_v1.md`; it does not implement the work packages. Existing review documents were left unchanged.

**Assessment: a strong engineering case with incomplete reliability and factual-validation guarantees. My rubric estimate is 83/100, with a reasonable reviewer range of roughly 80–87.** The earlier ~85 estimate is plausible, but neither estimate is a measured evaluation score. The next work should close concrete failure modes and demonstrate better answers, rather than add infrastructure.

The original case's eight functional capabilities are represented in the implementation. The graph, claim ledger, provider abstractions, deterministic routing, source provenance, and traceable decisions are substantive. The separate Go dispatcher is defensible as an external watchdog, but its added ownership and transaction complexity must be justified by tested guarantees.

**Evidence and limits**

- Read the original case PDF, including its rubric and architecture illustration; reviewed all four committed live reports, their saved states, the prior audit/brief, and relevant implementation paths.
- `make test-integration`: **476 Python tests passed**, including PostgreSQL integration tests. The test database is separate from the running application database.
- Repeated Go tests without cache: `TEST_DATABASE_URL=postgresql://research:research@localhost:55432/research go test -count=1 -json ./...`. **70 passing test/subtest result entries**, comprising 50 top-level tests; eight tested packages passed.
- `make lint`: Ruff lint/format, strict mypy, Go formatting and vet passed.
- Existing application's `/readyz` on port 8010 returned 200; database, Presidio and Langfuse checks were healthy. This is not a clean-clone installation test.
- Replayed quote verification and the deterministic gate on saved states; independently reconstructed citation references and source-list membership. Numeric findings below are gate replays, not an independent proof of semantic correctness.
- Ran isolated reproductions for semaphore deadlock, incomplete verification, zero-round remediation, SSE closure, heartbeat/deadline SQL and stale completion writes. Database probes created and deleted their own test run.
- Checked selected legal claims against official KVKK, Commission and Council sources. No new paid research runs, production crash tests, complete browser assessment, or exhaustive legal review were performed.

Machine-readable fixture measurements: [evaluation_v2_metrics.json](evaluation_v2_metrics.json).

**What the saved outputs demonstrate**

| Example | Claims | Normalized exact / fuzzy quotes | Cited documents / primary | Searches | Recorded LLM cost | Recorded elapsed time |
|---|---:|---:|---:|---:|---:|---:|
| ApilexAI | 33 | 30 / 3 | 7 / 4 | 14 | $0.0756 | 109.61 s |
| EU AI Act | 74 | 73 / 1 | 9 / 4 | 10 | $0.1584 | 123.37 s |
| KVKK | 84 | 81 / 3 | 17 / 8 | 13 | $0.1970 | 140.07 s |
| Legal tech | 54 | 54 / 0 | 7 / 0 | 20 | $0.1224 | 101.03 s |

All **245/245** quotes pass the implemented matcher: 238 normalized exact and seven fuzzy. Normalization removes differences in punctuation/case/spacing; this does not mean 238 literal Ctrl-F matches. Across all four reports there are zero dangling finding references, zero source-list membership mismatches, and zero remaining gate violations on replay, including G4. Recommendations are included in reference/source counts.

These are useful evidence-integrity checks. They do not establish that a number is current, that a legal condition survived extraction, or that a cited source is correct. The KVKK report passes the gate while giving an obsolete threshold. ApilexAI and legal-tech reports expose gaps and stop on `no_progress`, although listing entire partially answered questions under Known Gaps is less useful than naming the missing facets. The legal-tech conclusion also repeats qualitative drivers without directly summarizing the numerical range requested by the user.

**Findings requiring action**

1. **[P1, new, reproduced] Empty-result broadening can deadlock the search node.** In `agent/src/research_agent/agent/nodes/search.py:281`, `run()` acquires a semaphore; at line 325 it recursively awaits `run(index, retry)` before releasing it. With one configured slot, a single empty query requiring broadening waits for its own slot. At the default five slots, five such requests can all wait for additional slots held by one another. Using the real `SearchGateway`, a slow in-memory empty provider, and a 0.3-second test timeout reproduced both cases: one/five provider calls completed, while one/five broadened queries remained planned. The node cannot reach the router or the next cancellation boundary; its executor slot remains occupied. Release the permit before scheduling the broadened request, or use an explicit queue/loop without recursive acquisition. Add both one-slot and saturated-default regression cases.

2. **[P1, confirmed] Temporal and applicability errors can pass every quality check.** KVKK claim `c82` has `as_of="11/03/2021"`; document `d82` has no publication date, a score of 0.735, and an evaluator rationale explicitly warning that the content is likely legacy and not applicable to 2026. It nevertheless supports an unqualified 25-million-TL threshold and an action item. The official [2023/1154 decision](https://www.kvkk.gov.tr/Icerik/7647/2023-1154) replaced the ordinary exemption's 25-million threshold with 100 million; a [2026 KVKK announcement](https://www.kvkk.gov.tr/Icerik/8752/2025-yili-mali-bilanco-toplami-bakimindan-sicile-kayit-yukumlulugu-dogan-kurumlar-vergisi-mukellefi-tuzel-kisi-veri-sorumlularinin-verbis-kayit-suresi-hakkinda-kamuoyu-duyurusu) confirms the relevant current categories. Employee counts, activity type and other exemptions still matter; this is not a universal single-number rule.

   The EU report likewise describes a general transparency postponement. The [Commission FAQ](https://digital-strategy.ec.europa.eu/en/faqs/code-practice-transparency-ai-generated-content) distinguishes the August application date from a December transitional period for existing systems. This is not solely a missing schema field: claim `c36` closely copies an overbroad source sentence while the saved document elsewhere mentions certain existing systems. Claim `c70` copies a short timeline heading. Quote matching validates those strings, but extraction does not establish that the claim preserves the surrounding qualification (`agent/nodes/evidence.py:193`). Later citation verification sees only `cluster.statement`, not the underlying quotes and surrounding evidence (`agent/nodes/report.py:318`). Add claim-to-evidence validation, typed applicability/effective-date information, and targeted checks of authoritative current provisions. Preserve conditions through clustering, contradiction comparisons, synthesis and verification.

3. **[P1, confirmed with database probes] Run ownership is not fenced; a losing completion can persist data.** `db/repository.py:213` updates heartbeats by run ID alone. `finish()` and `_transition()` do not require the agent/attempt that owns the run. A simulated old worker successfully finished a `running` row whose owner was `agent-B`, attempt 2. Separately, `_settle()` adds artifacts before `update_counters()` commits (`agent_server/executor.py:292`, `db/repository.py:245`); only afterward does `finish()` validate the terminal transition. With a run already failed for `DEADLINE_EXCEEDED`, an attempted successful settlement left the row failed but persisted its report artifact and changed `tokens_in` to 777. Fence ownership-sensitive writes and publish status, counters and artifacts in one transaction. Include progress/checkpoint behavior in the stale-worker design, not only heartbeat.

4. **[P1, new, reproduced] Missing verifier decisions are silently counted as checked.** `agent/nodes/report.py:332–345` accepts `VerificationOutput(items=[])` and only records explicit negative verdicts. On the EU fixture this produced `{"checked":12,"unsupported":0}` with zero actual verdicts. A caught `LLMFailure` also returns without marking the batch unchecked. Require exactly one decision per requested sentence ID, reject duplicate/missing IDs, and expose unavailable verification as a distinct state. Choose an explicit retry/fallback policy instead of reporting successful coverage. This matters because the deterministic gate cannot detect a semantically false nonnumeric sentence that cites a real cluster.

5. **[P1, narrower than v1] Deadline failure incorrectly depends on an unchanged heartbeat.** The predicate at `dispatcher/internal/store/store.go:207` applies to deadline failure as well as stale-heartbeat recovery. On the test database, updating the heartbeat after observation made the deadline update affect zero rows; repeating with a fresh observation and no intervening heartbeat affected one row. The race is real and weakens the guarantee. The stronger assertion that an ordinary agent heartbeating every ten seconds necessarily evades every five-second watchdog pass indefinitely is not demonstrated. Deadline enforcement should depend on the authoritative expired deadline and appropriate run/attempt identity, not on heartbeat equality. Preserve heartbeat protection for decisions based on inactivity.

6. **[P2, reproduced] SSE closes before draining the live backlog.** `api/sse.py:118–127` reads at most 200 events and then closes if the status is terminal. An isolated running-to-terminal stream returned only 200 frames before `run_closed`, without another fetch for event 201. The initial backfill already loops correctly. The regression must connect while the run is running and then append more than 200 events before terminal observation; connecting to an already completed 250-event run, as the brief proposes, exercises the working path and can pass before the fix. Also define closing-event ordering: `_settle()` currently publishes terminal status before appending `RUN_FINISHED`, so draining a page alone does not guarantee that final event is present.

7. **[P2, reproduced] Zero remediation rounds still removes content.** `gate/runner.py:278` permits `max_rounds + 1` passes. With a fixture report plus an uncited factual sentence, `max_rounds=0` performed one round and removed one sentence. Enforce the configured count. Clarify whether report-only mode also forbids normalization performed before the loop, such as adding Known Gaps.

8. **[P2, code-confirmed] Facet independence and reviewer setup still need correction.** `agent/coverage.py:44` unions cluster origin IDs without applying the publisher constraint across clusters. Preserve both provenance deduplication and publisher independence at this level. In README, `.env` is optional at line 24 although that file enables the observability profile; line 375 promises `report.json` for live examples that do not contain it. Put the measured cost/time range and a first-run path near Quickstart. Label cost as recorded LLM cost, not a guaranteed all-in price.

**Corrections to the previous audit**

| Previous claim | Rechecked result |
|---|---|
| Five accepted quotes fail the implementation's 0.88 threshold | Not reproduced: all 245 pass `verify_quote`; seven use its fuzzy path. |
| Parallel searches exceed the budget because accounting happens after the request | Incorrect for the real gateway: `providers/search/gateway.py:85` increments before its first await. Five concurrent planned queries with a budget of two made two calls and skipped three. Replace B10 with the reproduced semaphore deadlock. |
| Duplicate artifact rows cause `MultipleResultsFound` | Incorrect mechanism: `(run_id, kind)` is already the composite primary key (`db/models.py:269–272`). The confirmed defect is pre-CAS persistence and unfenced ownership; do not add a redundant uniqueness migration. |
| Council adoption on 29 June is a disputed/wrong date | The Council's own [29 June release](https://www.consilium.europa.eu/en/press/press-releases/2026/06/29/artificial-intelligence-council-gives-final-green-light-to-simplify-and-streamline-rules/) confirms that event. The later date on the final regulation does not invalidate the Council-adoption claim. Distinguish legislative milestones. |
| The new prohibited practice's December start is another lost-condition error | The same Council release says the new ban applies in December. The EUR-Lex [regulation text](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=OJ%3AL_202601744), available in indexed text during this review, specifies the new provisions' December application date. Do not turn this into a false regression expectation. |
| Undated official sources score as recent as new ones | The code assigns 0.4 to undated sources and 1.0 to fresh ones. The problem is that authority/primary weights can still make an undated stale source sufficient, and applicability is not enforced. |
| GEPA supports the current prompt-engineering score | README mentions DSPy/GEPA as future production work. Do not award implementation credit for it. |

The EU report still has another real problem: its broad statement that enforcement, including prohibited practices, began in August 2026 conflates enforcement arrangements with the original prohibitions' application from February 2025. The [Commission AI Act overview](https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai) distinguishes those stages. Keep that case separate from the correctly dated new prohibition.

**Changes needed in the implementation brief**

- WP1: add the deadlock and incomplete-verification regressions; correct the SSE test's timing; remove the duplicate uniqueness migration. A transaction redesign is necessary because calling the current `finish()` first would itself commit before artifacts are added.
- WP2: a publication-date-only filter misses the existing trap. **39 of 40 cited-document entries across the four runs have `published_at=null`**, including the stale KVKK source. `Claim.source_published_at` does not currently exist, contrary to the brief. Use claim/source temporal evidence and distinguish publication date from legal validity. A still-valid 2023 provision can correctly answer a 2026 question.
- WP2: replacing origin counting with publisher counting alone would reintroduce syndicated-source inflation. Enforce both. Treat hosted first-party content with provenance, rather than assuming every LinkedIn or similar page speaks for the researched company.
- WP2: a keyword-based conditions warning is a heuristic. It cannot prove preservation of negation, exceptions or scope, and a null condition field on old fixtures cannot measure improved extraction. Replay extraction from frozen source text or add explicitly labeled expected conditions.
- WP5: separate artifact integrity, evidence entailment, temporal/applicability accuracy, completeness, and operational reliability. Existing final states can test gate replay; they cannot reconstruct every removed pre-gate sentence or prove that changed extraction prompts repair the old outputs. Define denominators for pre-remediation numeric failures and persist the required intermediate data.
- WP5: preserve the current failing answers as negative controls. Do not require every unchanged fixture to pass every quality threshold. Use per-task source-diversity expectations: one authoritative legal instrument may be sufficient, and sparse company coverage should remain an honest gap.
- WP5: there is no checked-in CI workflow in this checkout. An offline eval command is useful; claiming CI enforcement additionally requires a workflow that actually runs it.
- WP6: port 9 normally produces connection refusal, not a read timeout. Use a controlled endpoint that accepts a connection and withholds a response to demonstrate the timeout path.

**Rubric estimate**

| Case criterion | Maximum | Estimate | Main reason |
|---|---:|---:|---|
| Agent Orchestration & Reasoning Flow | 25 | 22 | Explicit graph, gap-driven routing and ledger; coverage semantics still imperfect. |
| Search & Retrieval Strategy | 20 | 17 | Strong abstractions, deduplication and follow-up; temporal evidence needs enforcement. |
| Reliability & Edge Case Handling | 15 | 10 | Passing integration suite, but reproduced deadlock, ownership and stream failures. |
| LLM / Prompt Engineering | 15 | 12 | Structured outputs and fallback; evidence entailment and verification completeness remain weak. |
| Software Architecture & Code Quality | 15 | 13.5 | Clear boundaries, shared contracts and clean static checks; transaction/ownership complexity leaks through. |
| Testing & Observability | 5 | 4.5 | Extensive passing tests and usable traces; adversarial regressions and independent eval absent. |
| Documentation & Design Decisions | 5 | 4 | Requirements and rationale covered; Quickstart and audit claims need correction. |
| **Total** | **100** | **83** | Subjective reviewer estimate, not a benchmark result. |

**Recommended order:** fix the search deadlock and ownership/atomic-settlement path; repair verifier completeness and claim-to-evidence/applicability checks; close deadline/SSE/remediation defects; correct Quickstart and the implementation brief; then add the focused eval/resilience evidence and refresh the four live examples once. The strongest submission story will be a small set of reproducible failures turned into passing regressions and visibly better reports.
