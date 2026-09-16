# Research skills

Domain expertise for the research agent, in the [Agent Skills](https://agentskills.io) folder
format: `SKILL.md` (frontmatter + guidance) and optional `references/`.

- `analyze_query` sees only each skill's `name` and `description` and picks up to two.
- The chosen skills' guidance is added to the `plan`, `generate_queries`, `evaluate_sources` and
  `synthesize` prompts.
- `references/domains.yaml` may **add** tier 1 or tier 2 domains for the run; it can never remove
  or demote one. `references/queries.yaml` lists query patterns.
- No scripts, ever: a skill is data. Skills cannot change budgets, termination or gate rules.
