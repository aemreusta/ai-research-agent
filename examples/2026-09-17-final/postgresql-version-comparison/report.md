# PostgreSQL 17 vs. PostgreSQL 18: Feature Comparison and SaaS Upgrade Considerations

## Summary

PostgreSQL 17 introduced incremental backups, JSON to relational table transformations, and query optimizations for CTEs and UNION queries. [1][2] PostgreSQL 18 introduces asynchronous input/output capabilities, native UUIDv7 support via the uuidv7() function, and requires matching checksum configurations during pg_upgrade. [3][4][5][6][7] According to single sources, SaaS teams preparing for PostgreSQL 18 must verify extension compatibility, adjust to deprecated MD5 authentication, and account for default data checksums. _(single source)_ [4][5][8][9]

> _1 sentence(s) removed for insufficient evidence - see gate_result.json_

## Key Findings

- PostgreSQL 17 added support for incremental backups to capture only changes made since the prior backup, as well as query optimizations leveraging statistics for CTEs and UNION queries. [1][2]
- PostgreSQL 17 introduced functionality to transform JSON data into relational tables within SQL environments. [1][2]
- According to single sources, PostgreSQL 17 reduced VACUUM memory consumption by up to 20x and achieved up to 2x better write throughput in high concurrency workloads. _(single source)_ [10]
- PostgreSQL 18 introduces asynchronous input/output capabilities across operations such as sequential scans, bitmap heap scans, and vacuum. [6][7][8]
- PostgreSQL 18 introduces native support for UUIDv7 via the uuidv7() function. [3][4]
- According to single sources, PostgreSQL 18 enables data checksums by default in initdb and deprecates MD5 password authentication in favor of SCRAM-SHA-256. _(single source)_ [4][5][7][8]
- When upgrading with pg_upgrade in PostgreSQL 18, data checksum settings must match between the source and target clusters. [5][6][9]

## Conflicting / Uncertain Information

_None found._

## Conclusion

While PostgreSQL 17 focused on incremental backups, JSON transformations, and query execution optimizations, PostgreSQL 18 expands foundational performance through asynchronous I/O, UUIDv7, and stricter security and integrity defaults. [1][2][3][4][6][7]

## Known Gaps

- What breaking changes, removed features, and deprecated behaviors exist between PostgreSQL 17 and PostgreSQL 18? - not enough independent or primary sources were found (removed configuration parameters)
- 25 candidate claim(s) withheld: source support, conditions or current applicability could not be confirmed; see the evidence ledger.

## Sources

1. [Exploring PostgreSQL 17: Overview of New Features & Enhancements](https://enterprisedb.com/blog/exploring-postgresql-17-new-features-enhancements) - enterprisedb.com (score 0.31, as of 2024-08-27)
2. [EDB Contributions to PostgreSQL® 17 Help Enterprises Unlock Greater Performance for Complex Workloads](https://finance.yahoo.com/news/edb-contributions-postgresql-17-help-130000691.html) - finance.yahoo.com (score 0.26, as of 2024-09-26)
3. [PostgreSQL 18: A Comprehensive Guide to New Features ...](https://medium.com/towardsdev/postgresql-18-a-comprehensive-guide-to-new-features-for-dbas-and-developers-b355e48023bf) - medium.com (score 0.36)
4. [Documentation: 18: E.6. Release 18 - PostgreSQL](https://postgresql.org/docs/current/release-18.html) - postgresql.org (score 0.76, primary, as of 2026-08-13)
5. [PostgreSQL 18.0 Release Notes](https://postgresql.org/docs/release/18.0) - postgresql.org (score 0.71, primary, as of 2025-09-25)
6. [PostgreSQL 18: part 5 or CommitFest 2025-03](https://postgrespro.com/blog/pgsql/5972351) - postgrespro.com (score 0.38, as of 2025-09-25)
7. [PostgreSQL 18 New Features: What's New and Why It Matters - Neon](https://neon.com/postgresql/18-new-features) - neon.com (score 0.37, as of 2025-06-21)
8. [PostgreSQL 18 Released!](https://postgresql.org/about/news/postgresql-18-released-3142) - postgresql.org (score 0.71, primary, as of 2025-09-25)
9. [PostgreSQL 18 Upgrades for AI-Era Workloads and Operations](https://severalnines.com/blog/postgresql-18-upgrades-for-ai-era-workloads-and-operations) - severalnines.com (score 0.40, as of 2026-03-20)
10. [PostgreSQL: PostgreSQL 17 Released!](https://postgresql.org/about/news/postgresql-17-released-2936) - postgresql.org (score 0.70, primary, as of 2024-09-26)

## Run metadata

- **Stop reason:** `max_iterations` - max_iterations=4 reached
- **Research rounds:** 4
- **Searches:** 20
- **Sub-questions:** s1 answered, s2 answered, s3 open, s4 answered
- **Sources read:** 32 / 149
- **Findings:** 95 (124 claims)
- **Tokens (in/out):** 588349 / 104465
- **LLM cost:** $0.4900
- **Output gate:** `pass_with_warnings` (1 removed)
- **Skills:** none
- **Config hash:** `489c5300e6b5b6eb`
- **Models:** embedding=gemini-embedding-001, fast=gemini:gemini-3.1-flash-lite, reasoning=gemini:gemini-3.8-flash
- **Prompt versions:** analyze_query@langfuse:1, assess_coverage@langfuse:1, evaluate_sources@langfuse:1, extract_claims@langfuse:4, generate_queries@langfuse:1, judge_contradictions@langfuse:5, plan@langfuse:2, synthesize@langfuse:3, validate_claims@langfuse:2, verify_citations@langfuse:2
