---
name: setup-database
description: Use when the user wants to bootstrap a new agent-materialize project. Walks through schema discovery, view proposal, and config finalization.
---

# Setting up the materialized-view foundation layer

Use this skill **only** when the setup-mcp server is wired up. The runtime MCP cannot perform discovery.

## Workflow

1. **Confirm the user has run `agent-mv init`.** The directory must contain `materialize.yaml` (with `views: []`) and `.env.example`.
2. **Read the consuming codebase first.** Call `read_repo_files(globs=["**/*.py", "**/*.sql", "**/*.ts"])` (or whatever the repo uses) to understand what queries the agent / app actually runs. Without this you will propose generic views that don't match the user's actual workload.
3. **Explore the schema.**
   - `list_schemas()` — orient yourself
   - For each user schema, `list_tables(schema=...)`
   - For each promising table, `describe_table(name=...)` and `profile_table(name=...)` (row count, FKs)
4. **Hypothesize 3-5 candidate views.** Each candidate must satisfy:
   - Maps cleanly to a question the consuming code actually asks
   - Joins ≤ 4 tables (anything bigger is a smell — split it)
   - Has a natural unique key (so refresh can use `CONCURRENTLY`)
5. **For each candidate, present to the user:**
   - Name + one-sentence rationale
   - The SQL
   - **A sample query result** — call `sample_query(sql=..., limit=10)` and show the rows. **Never propose a view without showing sample data.**
6. **User picks/edits.** Call `propose_view(name, sql, rationale)` for each approved candidate.
7. **Finalize:** `finalize_config(approved_names=[...])` writes the YAML and the per-view SQL files. Tell the user to run `agent-mv apply`.

## Rules

- **Never propose a view without showing a sample.** Sample data catches "this column is always NULL" and "this join produces 0 rows" before they become a stale MV.
- **Always include a unique-key column when the underlying data has one.** It enables `REFRESH ... CONCURRENTLY`.
- **Don't propose more than 5 views in v1.** Iterate. Smaller is reviewable.
- **Don't query `pg_*` or `information_schema` via `sample_query` — use `list_tables` / `describe_table` instead.** sample_query rejects them.
