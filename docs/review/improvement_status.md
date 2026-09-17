# Improvement status — 2026-09-17

The verified improvement pass is implemented. See [evaluation v3](evaluation_v3.md) for the evidence,
the failed live iterations and the remaining quality limits. "Complete" here does not mean the
reports are free of factual errors.

- [x] **Runtime:**
  - empty-search deadlock and the heartbeat/deadline race fixed;
  - attempt ownership and cancellation;
  - atomic completion and delivery of the last SSE events;
  - exact limits on gate remediation rounds.
- [x] **Evidence:**
  - every source verdict must be returned;
  - claims are checked against the original source, with conditions and effective dates;
  - old or undated values that can change are held back;
  - independence judged by publisher and provenance;
  - gate rule G12.
- [x] **Robustness:**
  - skills and configuration restored on resume; finished checkpoints are not rerun;
  - elapsed time preserved across attempts;
  - finding IDs kept in fallback synthesis;
  - language detection and logging fixes; CLI separated from the watchdog;
  - negative configuration values rejected; reconnect wake-ups.
- [x] **Models:**
  - Gemini, OpenAI or Ollama selectable per stage;
  - strict or fallback mode;
  - presets keep the selection; pricing recorded;
  - requested and actually used models recorded;
  - a live Gemini Pro run.
- [x] **Heuristic checks:** H1–H6 are visible in the UI, the timeline and the exports; a
  developer checklist covers manual review.
- [x] **Evaluation:** 20 labelled regression cases, failed iterations kept, runnable scripts, CI.
- [x] **Live evidence:**
  - eight distinct research questions;
  - one Gemini Pro run started from the UI;
  - legal reruns;
  - a correct refusal on a made-up company.

  Costs, durations and the unresolved retrieval and interpretation problems are recorded.
- [x] **Resilience:**
  - tests for provider errors, timeouts, output repair, cancellation and budgets;
  - a real SIGKILL recovery.

  A full-stack test of an agent that hangs while still sending heartbeats was not run; the
  deadline race is covered by PostgreSQL regression tests.
- [x] **Delivery docs:**
  - the required `.env` setup;
  - a real report JSON export;
  - costs and durations;
  - current diagrams;
  - the old brief marked as superseded;
  - a reproducible `make verify`.
- [x] **Case review (post-review):**
  - README design answers match the code (all dedup layers, the `consistent` verdict, G12,
    measured cost trade-off);
  - `examples/README.md` points to the September 17 batches;
  - ASCII-only Turkish reports are rewritten and flagged;
  - the single-source label says "independent".

**Verification:**

- `make verify` passed: 543 Python tests, Go tests with the race detector, lint, type checks and
  fixtures.
- The GitHub Actions `Verification` workflow passed on the pushed `main`.
- The results of the legal reruns after the primary-source rule are in the results index.

**Remaining work:**

- A live comparison of OpenAI report quality is waiting for API credits.
- Wider evaluation on unseen questions, retrieval coverage, legal-source classification and
  threshold calibration remain open.
- The DSPy/GEPA optimisation job was not implemented.
- Findings from the v1 audit that turned out to be wrong were not implemented.
- Delivery (sending the zip) is done by the user.
