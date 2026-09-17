# Improvement status — 2026-09-17

The verified improvement pass is implemented. See [evaluation v3](evaluation_v3.md) for evidence,
failed live iterations and remaining quality limits; “complete” here does not mean perfect facts.

- [x] Runtime: empty-search deadlock, heartbeat/deadline race, attempt ownership and cancellation,
  atomic completion, SSE tail delivery, exact remediation limits.
- [x] Evidence: complete source-verdict responses, original-source entailment, conditions/effect
  dates, old/undated mutable value holds, publisher/provenance independence and G12.
- [x] Robustness: restored skills/config on resume, completed-checkpoint settlement, preserved
  elapsed time, fallback synthesis IDs, language detection, logging, CLI/watchdog separation,
  negative configuration bounds and reconnect wake-ups.
- [x] Models: stage-specific Gemini/OpenAI/Ollama selection, strict/fallback modes, preset round-trip,
  pricing, requested/actual metadata and live Gemini Pro evidence.
- [x] Heuristics: H1–H6 visible in UI/timeline/exports, plus the developer/manual review checklist.
- [x] Evaluation: 20 labeled regressions, preserved failed iterations, runnable scripts and CI.
- [x] Live evidence: eight distinct research cases, one Pro UI run, legal reruns and false-premise
  abstention; costs/times and unresolved retrieval/semantic limitations recorded.
- [x] Resilience: provider/timeout/repair/cancel/budget tests and real SIGKILL recovery. Full-stack
  hung-heartbeat fault injection is not claimed; the deadline race has PostgreSQL regressions.
- [x] Delivery docs: required `.env` setup, genuine report JSON export, costs/durations, current
  diagrams, historical brief clearly superseded and reproducible `make verify`.

Verification: `make verify` passed 538 Python tests, Go tests with race detector, lint/types and
fixtures. The final Docker suite also passed all 538 tests (20.72 s). Post-policy legal run results
are listed in the results index. Remote GitHub Actions has not run; changes have not been pushed.

OpenAI live report comparison awaits API credits. Wider unseen semantic evaluation, retrieval
coverage, legal-source classification and threshold calibration remain ongoing product work.
The v1 audit's disproven findings were not implemented. External delivery/email is not authorized
by this session and was not attempted.
