# Recast Repo Guidance

This is the active Recast product and demo repository.

Use this repo for:

- judge-facing product narrative;
- Demo 1 scripts, acceptance criteria, and polished documentation;
- Recast app prototypes;
- VSS / Acer GN100 integration material needed to reproduce the demo;
- mobile capture planning for `recast-ios`;
- sanitized sample outputs and demo assets.

Do not use this repo as a general hackathon scratchpad. Broad research, abandoned ideas, raw data pulls, private credentials, and exploratory prep notes belong in `city-of-seattle-prep`.

When promoting material from `city-of-seattle-prep`, bring over only the curated version. Remove private paths, credentials, temporary notes, noisy planning history, and claims that are not evidence-backed.

Core thesis:

> Every building has a present and a possible future. Recast understands both, and finds the path between them.

## Documented Solutions

`docs/solutions/` — documented solutions to past problems (bugs, best practices, workflow patterns), organized by
category with YAML frontmatter (`module`, `tags`, `problem_type`). Read the relevant ones before implementing or
debugging in a documented area; add new learnings with `/ce-compound`. Plans live in `docs/plans/`
(`ce-plan` unified-plan contract).
