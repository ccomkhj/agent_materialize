---
name: setup-database
description: Use when the user wants to bootstrap a new agent-materialize project. Walks through schema discovery, view proposal, and config finalization.
---

# Setting up the materialized-view foundation layer

Use this skill **only** when the setup-mcp server is wired up. The runtime MCP cannot perform discovery.

## Workflow

You run the CLI commands yourself via shell (`agent-mv init`, `agent-mv apply`, `agent-mv doctor`). The user stays in the loop on view approvals and any prompts the CLI raises (e.g. drop confirmations).

1. **Bootstrap the project if it isn't already.** Check whether `materialize.yaml` exists. If not, run `agent-mv init` in the project root — that scaffolds `materialize.yaml`, `materialize/`, `.env.example`, `.mcp.json` (wires up the setup MCP), and symlinks these skills into `.claude/skills/agent-materialize/`. Also confirm `.env` exists with `DATABASE_URL` filled in; if it doesn't, ask the user to populate it from `.env.example` before continuing (you can't fill in their database credentials). Runtime credentials (`AGENT_MV_RUNTIME_URL`, `AGENT_MV_RUNTIME_PASSWORD`) are decided in step 7 — leave them as placeholders for now.

   **If you just wrote `.mcp.json`** (it didn't exist before this run), the setup MCP isn't loaded in the user's current session. Tell the user to reconnect MCP servers (`/mcp` in Claude Code, or restart) and re-trigger this skill. Without the reload, `list_schemas` / `sample_query` / etc. won't work.
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
7. **Finalize, decide on the runtime role, and apply.**
   - Call `finalize_config(approved_names=[...])` — writes the YAML and the per-view SQL files.
   - **Ask the user about the access boundary.** `agent-mv apply` always creates a constrained `agent_mv_runtime` role in Postgres. The question is what `AGENT_MV_RUNTIME_URL` in `.env` should point at:
     - **(a) Recommended — separate runtime role.** Set `AGENT_MV_RUNTIME_PASSWORD` to a fresh password and write `AGENT_MV_RUNTIME_URL=postgresql://agent_mv_runtime:<that-password>@<host>:<port>/<db>`. The runtime MCP literally cannot read base tables. `doctor` will pass.
     - **(b) Temporary — reuse `DATABASE_URL`.** Point `AGENT_MV_RUNTIME_URL` at the same superuser DSN as `DATABASE_URL`. Faster to get going on a sandbox, but the boundary is degraded — the agent technically has read access to everything. `doctor` will FAIL by design (that's the correct signal).
     Default to (a). Only accept (b) if the user explicitly asks for it; flag the degraded boundary clearly.
   - Run `agent-mv apply` — creates the `agent_mv` schema, the runtime role, the views, the lineage table, and the `SECURITY DEFINER` refresh function. The CLI prompts the user before any drops.
   - Run `agent-mv doctor`. If the user chose (a), it must pass — if it fails, stop and surface the error; do not claim setup is complete. If the user chose (b), expect the failure and tell them how to upgrade to (a) later.
   - Tell the user to edit `.mcp.json` and replace `agent-materialize-setup-mcp` with `agent-materialize-runtime-mcp` for day-to-day use, then reconnect MCP.

## Rules

- **Never propose a view without showing a sample.** Sample data catches "this column is always NULL" and "this join produces 0 rows" before they become a stale MV.
- **Always include a unique-key column when the underlying data has one.** It enables `REFRESH ... CONCURRENTLY`.
- **Don't propose more than 5 views in v1.** Iterate. Smaller is reviewable.
- **Don't query `pg_*` or `information_schema` via `sample_query` — use `list_tables` / `describe_table` instead.** sample_query rejects them.
