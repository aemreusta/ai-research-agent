# PostgreSQL 17 vs PostgreSQL 18: Feature Comparison and Upgrade Considerations

## Summary

PostgreSQL 18 brings major performance improvements, including an asynchronous I/O subsystem and skip-scan lookup capabilities for multicolumn B-tree indexes. [1][2][3][4][5][6] Developer-facing additions include native support for virtual generated columns, the uuidv7() function for timestamp-ordered UUID generation, and OLD and NEW aliases in RETURNING clauses. [1][2][7] Operationally, the release introduces notable backward incompatibilities, including changes to VACUUM and ANALYZE inheritance handling, the deprecation of MD5 password authentication, and adjustments to time zone abbreviation resolution. [3][7]

## Key Findings

- PostgreSQL 18 introduces an asynchronous I/O subsystem to improve the performance of sequential scans, bitmap heap scans, and vacuums. [2][3]
- PostgreSQL 18 adds skip-scan lookups for multicolumn B-tree indexes, allowing queries to use indexes even when leading columns lack restrictions. [1][2][3][4][5][6]
- PostgreSQL 18 includes the uuidv7() function to generate timestamp-ordered UUIDs. [1][2][7]
- PostgreSQL 18 supports OLD and NEW aliases in RETURNING clauses for INSERT, UPDATE, DELETE, and MERGE operations. [1][7]
- PostgreSQL 18 adds support for virtual generated columns that compute values dynamically during read operations. [2][7]
- PostgreSQL 18 introduces the UNIQUE NULLS DISTINCT constraint, which treats NULL values as unique and prevents duplicate NULL entries. [4][8][9]
- PostgreSQL 18 introduces support for OAuth authentication. [4][5]
- According to a single source, initdb enables data checksums by default in PostgreSQL 18, and pg_upgrade requires matching cluster checksum settings. _(single source)_ [2]
- According to a single source, VACUUM and ANALYZE process inheritance children by default unless the ONLY option is used. _(single source)_ [2]
- According to single-source findings, MD5 password authentication is deprecated in PostgreSQL 18, with CREATE ROLE and ALTER ROLE emitting deprecation warnings. _(single source)_ [1][2][10]
- According to a single source, users with indexes on ltree columns who do not use the libc collation provider must reindex their ltree columns after upgrading to PostgreSQL 18.2. _(single source)_ [11]

## Conflicting / Uncertain Information

_None found._

## Conclusion

PostgreSQL 18 provides significant performance and developer enhancements through asynchronous I/O, skip scan, uuidv7(), and virtual generated columns. [1][2][3][7] Small SaaS teams upgrading from PostgreSQL 17 must account for operational breaking changes, particularly the deprecation of MD5 authentication, default checksum activation, and inheritance handling in maintenance tasks. [2][3][7]

## Known Gaps

- 10 candidate claim(s) withheld: source support, conditions or current applicability could not be confirmed; see the evidence ledger.

## Sources

1. [Documentation: 18: E.6. Release 18 - PostgreSQL](https://postgresql.org/docs/current/release-18.html) - postgresql.org (score 0.73, primary, as of 2026-08-13)
2. [PostgreSQL: Release Notes](https://postgresql.org/docs/release/18.0) - postgresql.org (score 0.71, primary, as of 2025-09-25)
3. [PostgreSQL 18 released: Key features & upgrade tips](https://baremon.eu/postgresql-18-released-key-features-upgrade-tips) - baremon.eu (score 0.33, as of 2025-09-25)
4. [PostgreSQL 18 Upgrades for AI-Era Workloads and Operations](https://severalnines.com/blog/postgresql-18-upgrades-for-ai-era-workloads-and-operations) - severalnines.com (score 0.42, as of 2026-03-20)
5. [Postgres 18 Features: Async I/O, UUIDv7, OAuth and More | xata](https://xata.io/blog/going-down-the-rabbit-hole-of-postgres-18-features) - xata.io (score 0.40, as of 2025-09-29)
6. [Postgres 18: Generated Column Replication and Enhanced Monitoring](https://pgedge.com/blog/postgres-18-generated-column-replication-and-enhanced-monitoring) - pgedge.com (score 0.47, as of 2026-01-14)
7. [What's New in PostgreSQL 18: A DBA's Perspective | Bytebase](https://bytebase.com/blog/what-is-new-in-postgres-18) - bytebase.com (score 0.44, as of 2025-05-23)
8. [PostgreSQL 18 just dropped: 10 powerful new features devs need to know - DEV Community](https://dev.to/dev_tips/postgresql-18-just-dropped-10-powerful-new-features-devs-need-to-know-3jf) - dev.to (score 0.31, as of 2025-06-19)
9. [Medium](https://medium.com/devlink-tips/postgresql-18-just-dropped-10-powerful-new-features-devs-need-to-know-413aa601f20b) - medium.com (score 0.25)
10. [PostgreSQL 18 Released!](https://postgresql.org/about/news/postgresql-18-released-3142) - postgresql.org (score 0.73, primary, as of 2025-09-25)
11. [PostgreSQL: PostgreSQL 18.2, 17.8, 16.12, 15.16, and 14.21 Released!](https://postgresql.org/about/news/postgresql-182-178-1612-1516-and-1421-released-3235) - postgresql.org (score 0.62, primary, as of 2026-02-12)

## Run metadata

- **Stop reason:** `sufficient` - all 4 required sub-questions are answered
- **Research rounds:** 1
- **Searches:** 8
- **Sub-questions:** s1 answered, s2 answered, s3 answered, s4 answered
- **Sources read:** 20 / 60
- **Findings:** 74 (99 claims)
- **Tokens (in/out):** 425853 / 59051
- **LLM cost:** $0.2200
- **Output gate:** `pass` (0 removed)
- **Skills:** none
- **Config hash:** `489c5300e6b5b6eb`
- **Models:** embedding=gemini-embedding-001, fast=gemini:gemini-3.1-flash-lite, reasoning=gemini:gemini-3.8-flash
- **Prompt versions:** analyze_query@langfuse:1, assess_coverage@langfuse:1, evaluate_sources@langfuse:1, extract_claims@langfuse:3, generate_queries@langfuse:1, judge_contradictions@langfuse:5, plan@langfuse:1, synthesize@langfuse:3, validate_claims@langfuse:1, verify_citations@langfuse:2
