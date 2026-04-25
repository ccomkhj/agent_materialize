---
name: troubleshoot-refresh
description: Use when refresh_view or agent-mv refresh fails. Decision tree for common refresh failures.
---

# Troubleshooting refresh failures

Start by running `query_view(sql="SELECT * FROM agent_mv.refresh_history WHERE view_name = '<name>' ORDER BY started_at DESC LIMIT 5")` to see the most recent error.

## Decision tree

### "cannot refresh materialized view ... concurrently"

The view doesn't have a unique index. Two options:

- **Add one:** edit `materialize.yaml` to add `indexes: [{columns: [...], unique: true}]`, run `agent-mv apply`.
- **Accept blocking refresh:** the refresh function falls back to plain `REFRESH MATERIALIZED VIEW`, which works but locks reads for the duration. Acceptable for small views.

### "permission denied for table ..."

The view body references a base table that the **setup** role doesn't have access to. The runtime role doesn't matter here — refresh runs as definer (the setup role at the time the function was created). Grant `SELECT` to the setup role on the offending base table, then re-run `agent-mv apply` to recreate the function with the now-valid grants visible at definition time.

### "could not serialize access" / lock timeout

Another transaction holds a conflicting lock. Most common cause: two refreshes of the same view at the same time. Wait, retry. If chronic, look at the consuming code: someone is hammering refresh in a loop.

### "function ... does not exist"

The `agent_mv.refresh_view(text)` function got dropped or never created. Run `agent-mv apply` to recreate it.

### "unknown materialized view: <name>"

Either:
- the view name doesn't appear in `agent_mv.lineage` (apply was never run, or apply was aborted) — run `agent-mv apply`
- the caller mistyped the name — `list_views()` to see what's registered

## When in doubt

`agent-mv doctor` re-asserts the basic invariants (roles exist, schema exists, runtime can't read base tables). If `doctor` fails, fix that before investigating refresh.
