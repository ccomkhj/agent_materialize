# agent-materialize

Foundation layer for agents over Postgres. Wraps a database in a small set of materialized views behind a two-role access boundary, so agents can query and refresh data without ever touching base tables.

## Quickstart

```bash
uv add agent-materialize
agent-mv init
# configure .env with DATABASE_URL (full role) and AGENT_MV_RUNTIME_URL (view-only role)
agent-mv apply
agent-mv doctor  # verifies the access boundary
```

See `examples/starter-config/` for a sample config.
