# Live runs: what they showed and what changed

> **Historical record (2026-09-16).** These are the first live runs. The code has changed since;
> newer runs and their remaining problems are in [`2026-09-17-RESULTS.md`](2026-09-17-RESULTS.md)
> and [evaluation v3](../docs/review/evaluation_v3.md). "Final" below refers only to the September 16 snapshot.
>
> Addressed later:
>
> - **Stale VERBİS threshold:** handled by the rule that holds back old or undated values.
> - **Label wording:** the label now reads "single independent source", and ASCII-only Turkish
>   reports are rewritten and flagged.

All runs on 2026-09-16 used Gemini (`gemini-3.8-flash` reasoning, `gemini-3.1-flash-lite` fast,
`gemini-embedding-001`) and Tavily. Each was started through the API (the same path as the UI),
with default settings and two agent replicas. The four questions were run three times:

- **v1:** the first live run.
- **v2:** after the first round of fixes.
- **final:** the committed code; the folders in `examples/` are from this run.

Runs are not deterministic: search results and model output differ between runs, so round counts
and costs move even without code changes.

## Final runs

| Example | Outcome | Rounds / searches | Tokens in / out | Cost | Time | Claims → findings (2+ independent) | Judged pairs |
|---|---|---|---|---|---|---|---|
| `eu-ai-act-timeline` | sufficient, gate `pass_with_warnings` (1 sentence removed) | 3 / 10 | 140k / 48k | $0.158 | 124 s | 74 → 57 (9) | different_time 1, different_scope 2 |
| `kvkk-2026-saas-action-plan` | sufficient, gate `pass` | 2 / 13 | 161k / 66k | $0.197 | 140 s | 84 → 59 (8) | consistent 1, different_scope 1 |
| `apilexai-products-partnerships` | no_progress, gate `pass_with_warnings` | 3 / 14 | 59k / 27k | $0.076 | 110 s | 33 → 15 (4) | - |
| `legal-tech-market-size` | no_progress, gate `pass_with_warnings` | 3 / 20 | 142k / 36k | $0.122 | 101 s | 54 → 38 (7) | true_conflict 1 |

For comparison, v1: $0.132 / $0.256 / $0.091 / $0.160, with 2 / 4 / 4 / 4 rounds. Both v1
and the final runs stayed well inside the 45-search budget. Across the three batches, most of
the cost came from `extract_claims` and `verify_citations`.

## Problems found in the live data, and the fixes

| # | Seen in | Problem | Fix |
|---|---|---|---|
| 1 | ApilexAI v1 | One press release on halktv, dha and paradergi counted as three confirmations. MinHash missed it because each page wraps the text in different menus and footers; the founders sentence cited 9 sources. | **L2b:** pages that share ≥ 2 verbatim quotes of ≥ 12 words get one origin. Replaying v1 with the new rule, `k10` drops from 4 to 2 origins and `k11` from 3 to 1. |
| 2 | ApilexAI v1 | A finding with 4 documents had 5 origins: a text that named the speaker in one sentence and not in the next was counted twice. | A text (origin) counts once. |
| 3 | ApilexAI (intermediate run) | Four apilex.ai pages and two dunya.com articles were counted as six independent sources. | Independence now also requires a different publisher (`site_of`: `tr.linkedin.com` = `linkedin.com`). |
| 4 | Legal tech v1 | 8 of the 10 pairs sent to the judge were "2025 vs 2030" (wasted calls). Meanwhile the 2025 values 6.15 bn, 6.17 bn, 8.43 bn and 8,624 mn were never compared: the entity names were spelled differently and the units were ignored. | Contradiction candidates now require the same period when both sides state one. They also normalise entity spellings (Europe/European, tech/technology) and attribute wording (valuation = size, "projected" is ignored), and compare values together with their unit and scale. Replayed on v1, the same state gives the four 2025 pairs plus the real 2030 pair, with no cross-year pairs. |
| 5 | KVKK v1 | The "Conflicting" section listed two statements that agree ("mandatory" vs "must be notified to the Board"). The judge had no way to say so. | New judge kind `consistent`. Such pairs are not contested and not passed to synthesis. In the final KVKK run the judge used it for the 25.49 % revaluation rate. |
| 6 | EU AI Act v2 | The report stated "high-risk obligations apply from 2 August 2026" as settled next to a Commission source giving 2 December 2027; the judge called the pair a scope difference. | The judge and the synthesiser now see each finding's newest source date. A `different_time` verdict can keep a ledger-backed current side, and synthesis must present the earlier value as earlier. The final report leads with the Digital Omnibus postponement (2 Aug 2026 → 2 Dec 2027; Annex I → 2 Aug 2028). |
| 7 | v2 and intermediate runs | The new prompt versions never reached Langfuse. The copy there was still schema-compatible, so it kept winning over the newer repo seed. | Langfuse versions carry `seed_version`; a newer repo seed is published and used. The final runs used `judge_contradictions@langfuse:5` and `synthesize@langfuse:2`. |
| 8 | ApilexAI v2 | The Turkish report gave the known-gap reason in English ("no progress for 2 rounds"). | Reasons are localised ("2 turdur ilerleme yok ..."). |

Each fix has unit tests (`test_syndication.py`, `test_contradiction_node.py`, and additions in
`test_ledger_logic.py` and `test_prompting.py`). At the time the Python suite had 476 tests.

## What the final reports get right

- **EU AI Act:** the report answers "what changed":
  - the Digital Omnibus (Regulation (EU) 2026/1744);
  - the Annex III postponement to 2 Dec 2027 and the Annex I postponement to 2 Aug 2028;
  - the sandbox deadline moved to 2 Aug 2027;
  - the new prohibition applying from 2 Dec 2026.

  It rests on the Commission's AI Act Service Desk (score 0.89) and EUR-Lex (0.83).
- **KVKK:**
  - Most sources are primary (kvkk.gov.tr, btk.gov.tr).
  - Every recommendation in the action plan cites the findings it rests on.
  - The 2026 fine amounts and the 25.49 % revaluation rate are cited.
  - The gate passed without changes.
- **ApilexAI:** the company is thinly covered, and the report says so.
  - Partnerships and the roadmap are listed as known gaps instead of being made up.
  - Findings are labelled "tek kaynak" (single source) where that applies.
- **Legal tech:** the one real disagreement (2030 forecast: USD 10.31 bn vs 11.58 bn) is in the
  Conflicting section. 6.15 bn and 6.17 bn for 2025 fall within tolerance and are not
  reported as a conflict.

## Remaining weaknesses (not fixed)

- **Rewritten press releases:**
  - Outlets that rewrite a press release in their own words share no verbatim quotes. They still
    count as separate sources ("France is the focus" had 7 independent sources in an intermediate ApilexAI run).
  - Detecting this needs a semantic comparison of whole articles.
- **Stale primary pages:**
  - The final KVKK report states the VERBİS thresholds as "50 employees or TL 25 million"
    (source: kvkk.gov.tr announcement 6903) and builds recommendation 5 on them.
  - The v2 run had found the newer kvkk.gov.tr announcement (a TL 100 million balance-sheet
    threshold, deadline 05.06.2026).
  - An undated official page scores as recent as a new one, and the claim is single-source, so no
    conflict was detected in this run. Undated primary sources should lose recency weight, or be
    checked against newer ones on the same site.
- **Extraction errors on a single source:**
  - The EU report says prohibited practices are enforced "from 2 August 2026". The
    prohibitions apply from 2 Feb 2025, which v2 stated correctly; the sentence is
    labelled single-source.
  - The gate checks that numbers and dates match their sources. It cannot tell whether the
    source's sentence was read correctly.
- **Label wording:**
  - Some sentences carry "(single source)" while citing several sources. The label comes from the
    weakest finding the sentence cites, so a sentence that merges findings reads oddly.
  - The model wrote "Tekil" (for "tek") once in the KVKK summary; nothing checks spelling.
- **Stop reason for company research:** it is usually `no_progress`. With few independent
  sources, facets rarely become sufficient, so the run stops on stagnation rather than success.
- **Concurrent prompt publishing:** runs that start at the same time may each publish the same
  seed. This is harmless (Langfuse only gains duplicate versions), but the version number jumps
  (`judge_contradictions` went to 5).
