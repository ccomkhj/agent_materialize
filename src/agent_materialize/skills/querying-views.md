---
name: querying-views
description: Use when about to query data through agent-materialize's runtime MCP. Covers staleness, describe-before-query, and error handling.
---

# Querying materialized views at runtime

Triggered when you're about to use `query_view`, `refresh_view`, `list_views`, `describe_view`, or `get_lineage`.

## Before querying

1. **Check `last_refreshed_at`** via `list_views()`. If the data behind the view changes throughout the day and `last_refreshed_at` is older than your task tolerates, call `refresh_view(name=...)` first.
2. **Don't refresh pre-emptively.** Refresh costs CPU and locks (when not `CONCURRENTLY`). Refresh only when you need fresher data than what's there.
3. **For complex SQL, call `describe_view(name=...)` first** — confirm column names and types before composing the query. The runtime tools won't show you the SQL body of the view, only its columns.

## Querying

- Use `query_view(sql=..., limit=N)`. The MCP enforces `limit ≤ 1000` regardless of what you pass.
- Queries that reference any schema other than `agent_mv` are rejected.
- On error, the response has `{error_type, message, hint}` — read `hint` first; it points at the most likely fix.

## Refresh semantics

- `refresh_view(name)` returns `{started_at, finished_at, duration_ms, rows_after, mode}`.
- `mode` is `"concurrent"` if a unique index exists, `"blocking"` otherwise. Blocking mode locks reads on the view for the duration of the refresh — flag this to the user if you see it.
- Refresh does NOT cascade. If the view depends on another MV (`get_lineage` → `depends_on`), refresh those first if they're stale.
