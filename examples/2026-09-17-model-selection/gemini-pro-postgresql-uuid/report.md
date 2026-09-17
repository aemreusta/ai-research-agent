# Comparison of uuidv7() and uuidv4() Functions in PostgreSQL 18

## Summary

PostgreSQL provides native support for generating UUIDs using the UUIDv4 and UUIDv7 algorithms. _(single source)_ [1] While uuidv4() generates random identifiers, the uuidv7() function introduced in PostgreSQL 18 creates time-ordered, sortable UUIDs. _(single source)_ [2][3][4]

## Key Findings

- The uuidv7() function generates a version 7 time-ordered UUID using a UNIX timestamp with millisecond precision, sub-millisecond timestamp data, and random bits. [2][3][5]
- According to a single source, uuidv7() embeds the current time at the beginning to ensure sortability, and monotonicity is ensured within the same backend. _(single source)_ [4]
- The native UUID type supported UUIDv4 generation prior to PostgreSQL 18, and the uuidv4() function generates a version 4 random UUID. _(single source)_ [2][4]
- A single source notes that UUIDv4 values are not monotonic, whereas UUIDv7 values are sortable. _(single source)_ [4]
- The uuid_extract_timestamp() function solely supports extracting timestamps from version 1 or 7 UUIDs, returning null for other versions. _(single source)_ [2]
- A single source states that a limitation of uuidv7() is that it reveals the approximate creation time, which is a potential privacy concern. _(single source)_ [5]
- The official documentation for PostgreSQL UUID generation functions is located in Section 9.14 at https://postgresql.org/docs/current/functions-uuid.html. _(single source)_ [1][2]

## Conflicting / Uncertain Information

_None found._

## Conclusion

The uuidv7() function differs from uuidv4() by embedding a timestamp to produce sortable, monotonic identifiers instead of purely random values. [2][4][5] Although uuidv7() enables timestamp extraction, a single source highlights that exposing the creation time poses a privacy limitation. _(single source)_ [2][5]

## Known Gaps

- How does the uuidv4() function generate UUIDs in PostgreSQL according to official documentation? - not enough independent or primary sources were found (PostgreSQL module)
- According to PostgreSQL official documentation, how does the uuidv7() function differ from the uuidv4() function? - not enough independent or primary sources were found (Sorting and ordering, Structural difference)
- 1 candidate claim(s) withheld: source support, conditions or current applicability could not be confirmed; see the evidence ledger.

## Sources

1. [PostgreSQL: Documentation: 18: 8.12. UUID Type](https://postgresql.org/docs/current/datatype-uuid.html) - postgresql.org (score 0.68, primary, as of 2026-08-13)
2. [Documentation: 18: 9.14. UUID Functions - PostgreSQL](https://postgresql.org/docs/current/functions-uuid.html) - postgresql.org (score 0.76, primary, as of 2026-08-13)
3. [Documentation: 18: E.6. Release 18 - PostgreSQL](https://postgresql.org/docs/current/release-18.html) - postgresql.org (score 0.76, primary, as of 2026-08-13)
4. [Postgres 18 Features: Async I/O, UUIDv7, OAuth and More](https://xata.io/blog/going-down-the-rabbit-hole-of-postgres-18-features) - xata.io (score 0.24, as of 2025-09-29)
5. [UUID v4 vs UUID v7 for Database Primary Keys, A Practical Comparison](https://guidgenerator.com/uuids-in-databases/uuidv4-vs-uuidv7-for-database-primary-keys-a-practical-comparison) - guidgenerator.com (score 0.27, as of 2026-07-23)

## Run metadata

- **Stop reason:** `max_iterations` - max_iterations=1 reached
- **Research rounds:** 1
- **Searches:** 6
- **Sub-questions:** s1 answered, s2 open, s3 open
- **Sources read:** 5 / 41
- **Findings:** 12 (15 claims)
- **Tokens (in/out):** 119380 / 20186
- **LLM cost:** $0.2076
- **Output gate:** `pass` (0 removed)
- **Skills:** none
- **Config hash:** `5ec053f8dc73a89d`
- **Models:** embedding=gemini-embedding-001, fast=gemini:gemini-3.1-flash-lite, reasoning=gemini:gemini-3.1-pro-preview
- **Prompt versions:** analyze_query@langfuse:1, assess_coverage@langfuse:1, evaluate_sources@langfuse:1, extract_claims@langfuse:4, generate_queries@langfuse:1, judge_contradictions@langfuse:5, plan@langfuse:2, synthesize@langfuse:3, validate_claims@langfuse:2, verify_citations@langfuse:2
