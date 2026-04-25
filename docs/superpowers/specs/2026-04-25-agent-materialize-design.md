# agent-materialize — design

**Date:** 2026-04-25
**Status:** approved (brainstorming complete)
**Audience:** implementers and reviewers building this from scratch

## Problem

Agents that talk to a Postgres database are usually given direct access to base tables. That couples three things that should be separated:

- the surface area the agent can read (often "everything")
- the shape of the data the agent reasons about (raw tables, awkward joins)
- the cost profile of agent queries (full scans on production tables)

This project is a **shareable framework** that bootstraps a thin "foundation layer" between the agent and the database: a small set of materialized views in a dedicated schema, owned by a setup-time role, queried at runtime through a view-only role. The agent only ever sees the views.

The framework ships three things: skills (workflows the agent follows), two MCP servers (setup-time and runtime), and a Python CLI + static HTML dashboard for humans to operate the system.

## Goals

- A new user can install the package, point it at a Postgres DB, and end up with: a `agent_mv` schema, a small set of views chosen with the agent's help, two DB roles, and a working runtime MCP — in one session.
- The runtime DB role *cannot* read base tables. The access boundary is enforced by Postgres roles, not by the MCP process.
- View definitions live in a version-controlled config file (`materialize.yaml` + per-view `.sql` files). The config is the source of truth.
- The agent can refresh views itself when it wants fresh data, without any privilege escalation.
- Lineage (which base tables feed which view, and which views depend on which) is visible in the config, in a metadata table, and in a static HTML dashboard.

## Non-goals (v1)

- Column-level lineage. Table-level only for v1; column-level is a known follow-up.
- Auto-refresh policies (cron, freshness contracts, refresh-on-query). Agent-pull only.
- A live dashboard. Static HTML rebuilt on demand.
- Cascading refresh from the agent's `refresh_view` tool. Cascade is a deliberate human action via `agent-mv refresh-all`.
- Discovery automation that runs without an agent. `discover` is an agent-driven workflow, not a script.
- Multi-tenant or multi-DB support. One config, one target DB per repo.

## Architecture

One Python package, four entrypoints (two MCP servers + one CLI + one dashboard builder), no long-running services.

```
agent-materialize/
├── pyproject.toml
├── src/agent_materialize/
│   ├── config.py            # load/validate materialize.yaml
│   ├── db.py                # psycopg connection pool, role-aware
│   ├── lineage.py           # sqlglot parse → source-table DAG
│   ├── mcp_setup.py         # MCP server: setup-time tools (full role)
│   ├── mcp_runtime.py       # MCP server: runtime tools (view-only role)
│   ├── cli.py               # `agent-mv` Typer/Click CLI
│   ├── dashboard.py         # builds standalone dashboard.html
│   └── skills/              # .md skill files shipped in the package
├── tests/
└── examples/
    └── starter-config/      # example materialize.yaml + SQL
```

### Database roles and schema

Created during `agent-mv apply`:

- **`agent_mv_setup`** — `CREATEROLE`, `SELECT` on schemas of interest, `CREATE` on the `agent_mv` schema. Used by `mcp_setup` and by `apply`.
- **`agent_mv_runtime`** — `SELECT` only on the `agent_mv` schema, `EXECUTE` on a single `agent_mv.refresh_view(name text)` `SECURITY DEFINER` function. Used by `mcp_runtime`.

The runtime role literally cannot see base tables; that is the access boundary.

The `agent_mv` schema holds:

- One materialized view per entry in `materialize.yaml`
- `agent_mv.lineage(view_name text, source_kind text, source_name text)` — populated during `apply`, queried by `get_lineage`
- `agent_mv.refresh_history(view_name text, started_at timestamptz, finished_at timestamptz, status text, error text, rows_after bigint, mode text)` — append-only, populated by `refresh_view()`
- `agent_mv.refresh_view(name text)` — `SECURITY DEFINER` function. Validates `name` is in `materialize.yaml`'s view set, runs `REFRESH MATERIALIZED VIEW CONCURRENTLY <name>` if a unique index exists else falls back to plain `REFRESH`, writes a row to `refresh_history`.

### Why `SECURITY DEFINER` for refresh

The runtime role does not own the views, so it cannot `REFRESH` them directly. We could grant ownership, but then the runtime role would also be able to `DROP`. The function lets us keep the runtime role minimal *and* log every refresh attempt with timing through one chokepoint.

## Configuration

Single source of truth: `materialize.yaml` at the repo root.

```yaml
version: 1
target_schema: agent_mv
views:
  - name: customer_rollup
    sql_file: materialize/customer_rollup.sql
    indexes:
      - columns: [customer_id]
        unique: true                          # required for CONCURRENTLY
    description: "One row per customer with lifetime value + activity."
    sources: [public.users, public.orders, public.payments]   # written by lineage parser
```

View bodies live in `materialize/<name>.sql` so SQL stays diffable and not YAML-escaped. The `sources` field is owned by the lineage parser — humans don't write it; `apply` overwrites it on every run.

## Setup phase: discover → apply

Three commands, run in order:

```
agent-mv init        # writes empty materialize.yaml + .env.example + symlinks skills
agent-mv discover    # prints "run discovery from your agent with setup-mcp configured"
agent-mv apply       # creates roles, schema, views, lineage, refresh function (idempotent)
```

`agent-mv discover` is intentionally a no-op-with-instructions: discovery only happens inside an agent loop with the `setup-mcp` server wired up. The CLI command exists so users have a place to look.

### `setup-mcp` tools (used during discovery)

- `list_schemas()`
- `list_tables(schema)`
- `describe_table(name)`
- `profile_table(name)` — row count, FK graph, top-N values for low-cardinality columns
- `sample_query(sql, limit)` — read-only sample. Server-side caps: `LIMIT` ≤ 1000 enforced regardless of caller's value, no access to `pg_*` system tables (rejected with a clear error).
- `read_repo_files(globs)` — agent inspects the consuming codebase/API for hints about what's actually queried
- `propose_view(name, sql, rationale)` — appends a proposal to a *staging* file (`.materialize-staging.yaml`)
- `finalize_config()` — moves staged proposals into `materialize.yaml` after the user has approved them in conversation

The `setup-database` skill orchestrates: explore → hypothesize 3-5 candidate views → present each with rationale + a sample query result → user approves → `propose_view` → `finalize_config`.

### `apply` semantics

- Idempotent: diffs `materialize.yaml` against live DB state.
- Creates new views, recreates changed views (drop + create when columns change, `CREATE OR REPLACE` when only the body changes in a column-compatible way).
- For views removed from config, **prompts the user** before dropping. The prompt names the view and its row count.
- Runs the lineage parser (sqlglot) for every view, writes results back into the YAML's `sources` field and into `agent_mv.lineage`.
- Cross-checks the sqlglot-derived source list against `pg_depend`. Disagreements log a warning; sqlglot wins for the YAML write (it sees CTE/subquery shape; `pg_depend` is the runtime authority for refresh ordering).
- Creates the `agent_mv_setup` and `agent_mv_runtime` roles if absent. Idempotent role creation: skip if role exists with the expected privileges; warn if exists with mismatched privileges.

## Runtime phase: `runtime-mcp`

Connects as `agent_mv_runtime`. Tools:

- **`list_views()`** → list of `{name, description, sources, last_refreshed_at, row_count}`. No staleness hint — `last_refreshed_at` is sufficient and the agent decides.
- **`describe_view(name)`** → columns + types + the YAML `description`. No SQL body (runtime role doesn't have it; not needed for queries).
- **`query_view(sql, limit=1000)`** → executes against `agent_mv` only. Server-side `LIMIT` cap. Errors surface as `{error_type, message, hint}`.
- **`refresh_view(name)`** → calls `agent_mv.refresh_view(name)`. Returns `{started_at, finished_at, duration_ms, rows_after, mode}`.
- **`get_lineage(name)`** → `{sources, depends_on, depended_on_by}`, all table-level.

### Refresh semantics

- Default: `REFRESH MATERIALIZED VIEW CONCURRENTLY <name>` if a unique index exists, else plain `REFRESH` with a warning logged into `refresh_history.mode`.
- No queueing or in-flight tracking beyond Postgres's own behavior. Concurrent refresh attempts on the same view fail with a clear error; the agent retries.
- `refresh_view()` does **not** cascade. Cascading refresh is a deliberate human action via `agent-mv refresh-all`.

## Lineage

Source of truth: `sqlglot` parse at `apply` time. Stored in two places:

1. **YAML `sources` field** — for humans / PR diffs / repo-side inspection.
2. **`agent_mv.lineage` table** — for the runtime role (which can't read the YAML) and for graph queries (`depended_on_by` is just a flipped lookup).

`get_lineage(name)` returns table-level data: direct source tables, MV dependencies, and reverse dependencies. Topological sort over `agent_mv.lineage` produces the refresh order for `agent-mv refresh-all`.

Column-level lineage is a known follow-up.

## CLI surface (`agent-mv`)

Every command is something a human runs. The agent uses the MCP, not the CLI.

```
agent-mv init                  # write empty materialize.yaml + .env.example + symlink skills
agent-mv discover              # prints instructions to run discovery via setup-mcp
agent-mv apply                 # diffs config vs DB; prompts on drops; runs lineage parser
agent-mv status                # rich-table view: name, last_refreshed_at, row_count, sources
agent-mv refresh <name>        # one view (human escape hatch)
agent-mv refresh-all           # topological order, sequential
agent-mv drop <name>           # explicit drop; also removes from yaml
agent-mv dashboard build       # writes dashboard.html
agent-mv doctor                # asserts roles, schema, MCP envs, and access boundary
```

`agent-mv doctor` is the install-time sanity check. It tries `SELECT 1 FROM <one base table>` *as the runtime role* and asserts the query fails with `permission denied`. If `doctor` passes, the framework's core promise has been verified end-to-end.

## Dashboard (static HTML)

`agent-mv dashboard build` produces a single `dashboard.html` — no server, no JS framework, no build step. Single Jinja template + queries from the runtime role + inlined SVG.

Page layout:

1. **Header** — generated-at timestamp, target schema, total view count.
2. **Status table** — name, description, sources (compact), last_refreshed_at (relative: "6h ago"), row_count, last refresh duration, last refresh status.
3. **Lineage graph** — `graphviz`-rendered SVG, inlined. Base tables are one shape, MVs another. Edges are directed (source → MV). MV-to-MV deps look the same as table-to-MV.
4. **Recent refresh history** — last 50 rows from `agent_mv.refresh_history`, newest first. Failures highlighted.

`graphviz` (system package, `brew install graphviz`) chosen over `pyvis` because the output is clean static SVG with no JS, fitting the "no server, works offline" goal.

No live refresh. Re-run `agent-mv dashboard build` to regenerate.

## Skills

Four skills, packaged in `src/agent_materialize/skills/` and **symlinked** into the user's `.claude/skills/` during `agent-mv init`. Symlinks (not copies) so users get the latest skill version on `pip upgrade` of the framework.

1. **`setup-database`** — discovery workflow. Triggers on phrases like "set up materialized views" or after `agent-mv init`. Walks the agent through: explore schemas → read consuming code via `read_repo_files` → propose 3-5 candidate views → present each with rationale + sample data → user approves → `propose_view` → `finalize_config`. Includes the rule: *never propose a view without showing a sample query result first.*
2. **`querying-views`** — runtime guidance. Triggers when the agent is about to query through the runtime MCP. Rules: check `last_refreshed_at` first; refresh only if stale-for-the-task (not pre-emptively); use `describe_view` before writing complex SQL; failures surface a `hint` field — read it.
3. **`adding-a-view`** — post-setup workflow. Write SQL in `materialize/<name>.sql`, add YAML entry, run `agent-mv apply`. Includes a unique-index reminder for `CONCURRENTLY` refresh.
4. **`troubleshoot-refresh`** — refresh failure decision tree. Check `refresh_history.error`, missing unique index, source table changes, blocking locks. Each branch points at a concrete next command.

Skills are read-only artifacts in the package. Users who want to customize copy them out of the symlink target and edit.

## Testing strategy

Three layers, no DB mocking.

1. **Unit tests** — pure functions only: `lineage.py` (sqlglot → expected source list), `config.py` (YAML validation), refresh-DAG topological sort. `pytest`, fast, no DB.
2. **Integration tests against ephemeral Postgres** — `testcontainers-python` fixture. Each test gets a fresh DB. Coverage:
   - `apply` creates roles, schema, views from a fixture config
   - **runtime role cannot `SELECT` from base tables** (the security promise — explicit assertion, CI blocker)
   - runtime role can `SELECT` from views
   - `refresh_view()` works as runtime role and logs into `refresh_history`
   - `apply` is idempotent (run twice → same state)
   - `apply` prompts before drops (non-interactive mode asserts the prompt was triggered, then aborts safely)
   - `pg_depend` cross-check matches sqlglot output for fixture views
3. **MCP smoke tests** — start each MCP server, send a tool call over stdio, assert response shape.

Out of scope for v1 testing:

- End-to-end discovery workflow (agent loop; covered by manual walkthroughs documented in the `setup-database` skill)
- Dashboard visual output (smoke-test that `dashboard build` produces a non-empty file)

## Risks and open questions

- **`SECURITY DEFINER` function correctness.** A bug here could let the runtime role refresh anything. Mitigation: the function validates `name` against `materialize.yaml`'s view set, queried fresh from `agent_mv.lineage`. Integration test asserts that calling `refresh_view('public.users')` (a base table) is rejected.
- **`CREATE OR REPLACE` for view bodies that change column shape.** Postgres rejects column-incompatible `CREATE OR REPLACE`. `apply` falls back to drop+create, which momentarily breaks queries. Acceptable for v1; document it.
- **Discovery proposing views that are too expensive.** No cost guardrails in v1 — the user reviews proposals before `finalize_config`. Acceptable; revisit if it bites.
- **`graphviz` system dependency.** Users on hosts without `brew`/`apt` will hit a non-Python install step. Documented in the README; `agent-mv doctor` checks for it.

## Out of scope (explicit)

- Column-level lineage
- Auto-refresh policies (cron, freshness contracts, on-query)
- Live dashboard
- Cascading refresh from the agent's per-view tool
- Multi-DB / multi-tenant
- Authentication on the dashboard (it's a local file)
