# agent-materialize

**A foundation layer for agents over Postgres.** It wraps your database in a small set of agent-curated materialized views behind a two-role access boundary, so an AI agent can query and refresh data without ever touching base tables.

![architecture](docs/images/architecture.png)

---

## Why this exists

Agents that talk to a Postgres database are usually given direct credentials to base tables. That couples three things that should be separate:

- **The surface area the agent can read** — usually "everything", which is rarely what you want.
- **The shape of the data the agent reasons about** — raw tables, awkward joins, denormalized columns the agent has to re-derive on every question.
- **The cost profile of agent queries** — full scans on production tables, repeated per turn.

The fix that everyone reaches for first is "just write some views and point the agent at those." That works for a session, but it leaves three gaps:

1. **Who decides what views to create?** A static schema rarely survives contact with a real agent workload.
2. **Who keeps them fresh?** Stale data is silently wrong.
3. **How do you actually keep base-table credentials away from the agent?** Most setups punt on this, granting `SELECT` on `public.*` and trusting the prompt.

`agent-materialize` is opinionated about all three. The agent helps choose the views during a one-time discovery phase, the agent itself can refresh them at runtime through a narrow interface, and the access boundary is enforced **inside Postgres** with two roles — not in the application layer where a misconfigured client could leak through.

## What you get

| Component | What it does |
|---|---|
| **`setup-mcp` server** | Privileged. Used once. Lets the agent introspect the schema, sample data, read your consuming codebase, and propose materialized views. |
| **`runtime-mcp` server** | View-only. Used every day. The agent's daily-driver MCP — list, describe, query, refresh, lineage. Can't see base tables. |
| **`agent-mv` CLI** | What your agent runs under the hood during setup, available to you as an escape hatch: `init`, `apply`, `doctor`, `status`, `refresh`, `refresh-all`, `drop`, `dashboard build`. |
| **Static HTML dashboard** | View-status table, refresh history, and an inline SVG lineage graph. No server. |
| **Four skills** | `setup-database`, `querying-views`, `adding-a-view`, `troubleshoot-refresh`. Symlinked into `.claude/skills/` on `init`. |
| **Slash commands** | `/agent-materialize-onboard`, `/agent-materialize-add-view`, `/agent-materialize-troubleshoot` — user-typed entry points that load the matching skill. Symlinked into `.claude/commands/` on `init`. |
| **`materialize.yaml`** | Single source of truth for view definitions. Lineage is parsed by sqlglot at apply-time and written back into the YAML. |

## Quickstart

> **Run these commands in your own project directory, not inside this repo.** Scaffolding lands in your CWD; you don't want it inside this package's source tree.

The flow is **skill-first**. You install the package, point your MCP client at the setup server, and ask your agent to run the `setup-database` skill. The agent does the typing — scaffolding, schema exploration, view proposals, apply, doctor. You stay in the loop on view approvals and DDL confirmations.

### 1. Install the CLI

The package isn't on PyPI yet. Install it as a **global CLI tool** with `uv tool install` — that puts `agent-mv` on your PATH in its own isolated environment, no venv-activation needed. Your agent calls these commands under the hood; you rarely type them yourself.

```bash
git clone https://github.com/ccomkhj/agent-materialize
cd agent-materialize
uv tool install .

# If `agent-mv` isn't found afterward, ensure uv's bin dir is on PATH:
uv tool update-shell && exec $SHELL

# Plus the system dep for the dashboard
brew install graphviz   # macOS  (apt install graphviz on Debian/Ubuntu)
```

> **Why `uv tool install` and not `uv add`?** `uv add` registers a library dependency inside one specific project's `.venv/`. `uv tool install` is uv's "install this CLI globally, in an isolated env" mode — closer to `pipx install`. For agent-materialize you want the second one: `agent-mv` is a CLI invoked from any project directory.

### 2. Scaffold the project

In your project directory, write `.env` and run `agent-mv init`:

```bash
cd your-project
cat > .env <<'EOF'
DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME             # full setup-time privileges
# Runtime credentials are decided in step 4. Leave as placeholders for now:
AGENT_MV_RUNTIME_URL=postgresql://agent_mv_runtime:CHANGEME@HOST:5432/DBNAME
AGENT_MV_RUNTIME_PASSWORD=CHANGEME
EOF

agent-mv init
```

`init` writes `materialize.yaml`, `.env.example`, `.mcp.json` (wires up the setup MCP), `materialize/`, and symlinks the four skills into `.claude/skills/agent-materialize/`. The MCP servers auto-load `.env` from the working directory.

This is the only step you type by hand. (You can skip it and have your agent run `agent-mv init` mid-session, but you'll then have to reconnect MCP servers so the setup MCP loads — see the `setup-database` skill.)

### 3. Onboard

Start your agent in the project directory (so it picks up `.mcp.json` and the symlinked commands) and type:

```
/agent-materialize-onboard
```

That slash command loads the `setup-database` skill and walks the agent through the rest. (Equivalent to pasting *"Load the setup-database skill from .claude/skills/agent-materialize/ and follow it."*)

Your agent will:

1. Ask **what questions your consuming app or agent actually asks** of this DB — have one or two real examples ready (e.g. *"which POs need to be created?"*, *"which SKUs are running low?"*). Without this the proposals are generic.
2. Read your codebase and explore the Postgres schema via the setup MCP.
3. Propose 3–5 materialized views **with sample rows** and wait for your approval on each.
4. Ask whether you want a strict access boundary (separate `agent_mv_runtime` role — recommended) or to temporarily reuse the superuser DSN.
5. Run `agent-mv apply` — creates the `agent_mv` schema, the runtime role, the views, the lineage table, and the `SECURITY DEFINER` refresh function. You confirm any drops.
6. Run `agent-mv doctor` — proves the runtime role cannot read base tables.

You stay in the loop on the parts that matter: you describe the workload, you approve every view, you read the apply diff, you confirm drops, you decide on the boundary. The agent just does the typing.

### 4. Switch to the runtime MCP

Once `doctor` passes, swap `agent-materialize-setup-mcp` → `agent-materialize-runtime-mcp` in your MCP config:

```json
{
  "mcpServers": {
    "agent-materialize-runtime": {
      "command": "agent-materialize-runtime-mcp"
    }
  }
}
```

Then **reconnect MCP** (`/mcp` in Claude Code, or restart the session) so the swap takes effect.

The runtime MCP can list, describe, query, refresh, and trace lineage — but cannot read base tables, drop views, or refresh anything not on the lineage allowlist. Your agent now has a clean, narrow surface that maps to the questions your code actually asks.

### Working on agent-materialize itself?

If you're contributing to this repo (not using it from another project), see [Development](#development) instead — `uv sync` from the repo root sets up the editable install.

## Troubleshooting

**`agent-mv doctor` fails with `password authentication failed for user "agent_mv_runtime"`.** The role already exists from a prior install with a different password, and `agent-mv apply` does not rotate passwords on existing roles. Sync it by hand:

```bash
psql "$DATABASE_URL" -c "ALTER ROLE agent_mv_runtime WITH PASSWORD '$AGENT_MV_RUNTIME_PASSWORD';"
```

Then re-run `agent-mv doctor`.

**Setup MCP fails every tool call with `DATABASE_URL is required for setup-mcp`.** The MCP server can't see your `.env`. Either it was launched before the file existed, or its working directory isn't under the project. Make sure `.env` lives at the project root, then reconnect the MCP (`/mcp`, or restart the session).

**The agent proposes generic views that don't match your workload.** It's missing context on what your consumer actually asks. Re-run `/agent-materialize-onboard` and lead with concrete example questions ("which POs need to be created?", "what's selling slow on Amazon?") before letting it explore.

## How it works

### The two roles

`agent-mv apply` creates two Postgres roles:

- **`agent_mv_setup`** — `CREATEROLE`, `SELECT` on the schemas of interest, `CREATE` on the `agent_mv` schema. Used by `setup-mcp` and by the `apply` command itself.
- **`agent_mv_runtime`** — `SELECT` only on the `agent_mv` schema, plus `EXECUTE` on a single `SECURITY DEFINER` function: `agent_mv.refresh_view(name text)`. **Cannot read base tables.** Cannot drop, alter, or refresh views directly.

The runtime role's privileges are enforced by Postgres — not by the MCP process, not by the prompt. `agent-mv doctor` proves it on every install: it tries to `SELECT` a base table as the runtime role and asserts the query fails with `permission denied`.

### Refresh through a chokepoint

The runtime role refreshes views by calling `agent_mv.refresh_view('my_view')`. That function:

1. Validates the name against the `agent_mv.lineage` allowlist (so the runtime role can't trick the definer into refreshing arbitrary objects).
2. Bounds the input length (DoS guard on the error path).
3. Detects whether a unique index exists and picks `REFRESH MATERIALIZED VIEW CONCURRENTLY` or plain `REFRESH` accordingly.
4. Logs every attempt — success or failure — into `agent_mv.refresh_history` with timing and rows-after.
5. Runs with `SECURITY DEFINER` and `SET search_path = pg_catalog` to block search-path hijacking.

Every refresh is auditable. Every refresh runs through one chokepoint.

### Lineage is the contract

When `agent-mv apply` runs, sqlglot parses each view's SQL and extracts the source tables and MV-to-MV dependencies. These get written to two places:

- **The YAML's `sources:` field** — for human review in PRs.
- **`agent_mv.lineage`** — for the runtime role to query through `get_lineage()` and for the SECURITY DEFINER refresh function to use as an allowlist.

Apply also cross-checks against `pg_depend` and warns if sqlglot and Postgres disagree.

## Benefits

### Security
- **Two-role boundary enforced in the database.** A misconfigured agent client cannot bypass it.
- **No credential bleed.** The runtime MCP literally doesn't have credentials that reach base tables.
- **Tested as a CI blocker.** `tests/test_security_boundary.py` asserts the runtime role cannot SELECT base tables, cannot DROP, cannot REFRESH directly, and cannot trick the SECURITY DEFINER function into refreshing arbitrary names.

### Speed and cost
- **Agents query a thin schema.** Materialized views match the questions your code asks; agents stop re-deriving the same joins on every turn.
- **Refresh is intentional.** Agents call `refresh_view()` only when they need fresher data — no scheduled-thrash, no surprise scans.
- **Index-aware.** Views with a unique index get `REFRESH ... CONCURRENTLY` automatically.

### Observability
- **Every refresh is logged** to `agent_mv.refresh_history` with start/end/duration/status/error.
- **Lineage is queryable** via `get_lineage(name)` — sources, dependencies, reverse dependencies. Used for refresh ordering in `refresh-all` and for the dashboard's SVG graph.
- **The dashboard renders to one HTML file** — `agent-mv dashboard build`. Status table, refresh history, lineage graph. No server, works offline.

### Workflow
- **Config-as-code.** `materialize.yaml` + per-view `.sql` files live in your repo. View bodies are diffable. PRs review changes the way they should.
- **Idempotent apply.** Re-run `agent-mv apply` as often as you like. It diffs the YAML against the live DB, prompts before drops, and refuses to drop a view that another kept view still depends on.
- **Skills shipped with the package.** Four skills walk your agent through discovery, querying, adding views, and troubleshooting refresh. Symlinked into `.claude/skills/` on `init`, so they stay current with the installed package version.

## Configuration

`materialize.yaml`:

```yaml
version: 1
target_schema: agent_mv
views:
  - name: customer_rollup
    sql_file: materialize/customer_rollup.sql
    description: "One row per customer with lifetime value + activity."
    indexes:
      - columns: [customer_id]
        unique: true                        # required for CONCURRENTLY refresh
    sources: []                              # written by `agent-mv apply`
```

`materialize/customer_rollup.sql` — a plain `SELECT`. `apply` wraps it in `CREATE MATERIALIZED VIEW`.

```sql
SELECT
    u.id AS customer_id,
    COUNT(o.id) AS order_count,
    COALESCE(SUM(p.amount), 0) AS lifetime_value,
    MAX(o.created_at) AS last_order_at
FROM public.users u
LEFT JOIN public.orders o ON o.user_id = u.id
LEFT JOIN public.payments p ON p.order_id = o.id
GROUP BY u.id;
```

The `sources` field is owned by the lineage parser. Humans don't write it.

## CLI reference

These are what your agent runs for you. You typically don't type them by hand — but they're there as an escape hatch (CI, scripted ops, debugging a stuck setup):

```
agent-mv init                  # scaffold materialize.yaml, .env.example, materialize/, skills
agent-mv discover              # printable instructions; discovery is agent-driven via setup-mcp
agent-mv apply                 # diff config vs DB; prompt on drops; write lineage
agent-mv doctor                # assert roles, schema, and the access boundary
agent-mv status                # rich-table status of all views
agent-mv refresh <name>        # refresh one view via the SECURITY DEFINER function
agent-mv refresh-all           # refresh all views in topological order
agent-mv drop <name>           # remove from YAML and from the database
agent-mv dashboard build       # render dashboard.html
```

For day-to-day querying, your agent uses the runtime MCP's tools (`list_views`, `describe_view`, `query_view`, `refresh_view`, `get_lineage`) instead of the CLI.

## System dependencies

- Python ≥ 3.11
- Postgres ≥ 14 on the target side
- `graphviz` (for `dashboard build`): `brew install graphviz` on macOS, `apt install graphviz` on Debian/Ubuntu

## Development

```bash
uv sync
uv run pytest -v
```

Integration tests use `testcontainers` to spin up an ephemeral Postgres per test database. Docker must be running. The full suite is 69 tests, ~6 seconds.

If you also installed `agent-mv` globally with `uv tool install .`, that's a **snapshot** of the source at install time — local edits won't show up in the global CLI or in MCP servers launched from `.mcp.json`. After changing source, refresh the global binary:

```bash
uv tool install --reinstall .
```

Then reconnect any MCP clients so they pick up the new server binary.

## Non-goals (v0.1.0)

- **Column-level lineage.** Table-level only for v1; column-level is on the roadmap.
- **Auto-refresh policies** (cron, freshness contracts, refresh-on-query). Agent-pull only.
- **Live dashboard.** Static HTML rebuilt on demand.
- **Cascading refresh from the runtime tool.** Cascade is a deliberate human action via `refresh-all`.
- **Multi-tenant / multi-DB.** One config, one target DB per repo.

## License

MIT.
