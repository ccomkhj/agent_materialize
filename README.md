# agent-materialize

A foundation layer for agents over Postgres. Wraps the database in a small set of materialized views behind a two-role access boundary, so agents query and refresh the views without ever touching base tables.

## What you get

- **Two MCP servers:** `setup-mcp` (privileged, used once during discovery) and `runtime-mcp` (view-only, used every day).
- **One CLI:** `agent-mv` for `init`, `apply`, `status`, `refresh`, `refresh-all`, `drop`, `dashboard build`, `doctor`.
- **Four skills:** `setup-database`, `querying-views`, `adding-a-view`, `troubleshoot-refresh`. Symlinked into `.claude/skills/` on `init`.
- **Static HTML dashboard:** rebuilt on demand, no server, inline SVG lineage graph.

## Quickstart

```bash
uv add agent-materialize
agent-mv init
# fill in .env using .env.example
# wire up setup-mcp in your MCP client (see docs)
# ask the agent to run the `setup-database` skill
agent-mv apply        # creates the schema, roles, views, lineage table, refresh function
agent-mv doctor       # verifies the access boundary
```

## Access boundary

After `agent-mv apply`:
- `agent_mv_setup` role: full DB access (used by `setup-mcp` and by `apply`)
- `agent_mv_runtime` role: `SELECT` on `agent_mv` schema, `EXECUTE` on `agent_mv.refresh_view(name text)`. Cannot read base tables.

`agent-mv doctor` asserts the boundary by trying to read a base table as the runtime role and asserting the query fails.

## Configuration

Single source of truth: `materialize.yaml` + `materialize/<view_name>.sql`. The `sources:` field on each view is owned by the lineage parser; humans don't write it. View bodies live in separate `.sql` files so SQL stays diffable.

## System dependencies

- Python ≥ 3.11
- `graphviz` (for `dashboard build`): `brew install graphviz` on macOS, `apt install graphviz` on Debian/Ubuntu
- Postgres ≥ 14 on the target side

## Development

```bash
uv sync
uv run pytest -v
```

Integration tests use `testcontainers` to spin up an ephemeral Postgres. Docker must be running.

## License

MIT.
