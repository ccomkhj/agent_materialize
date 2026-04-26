-- Demonstrates MV-on-MV lineage: this view reads from agent_mv.customer_rollup.
-- `agent-mv apply` parses the SQL with sqlglot, sees the dependency, writes it
-- into agent_mv.lineage, and uses it for refresh ordering in `refresh-all`.
SELECT
    customer_id,
    email,
    order_count,
    lifetime_value_cents,
    last_order_at
FROM agent_mv.customer_rollup
WHERE last_order_at >= now() - interval '30 days'
ORDER BY lifetime_value_cents DESC
LIMIT 10;
