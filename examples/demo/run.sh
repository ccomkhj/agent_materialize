#!/usr/bin/env bash
# End-to-end demo of agent-materialize against a throwaway Postgres in Docker.
#
# Brings up Postgres + seed data, applies 3 materialized views (one with an
# MV-on-MV dependency), proves the access boundary, refreshes in topo order,
# and renders the static dashboard.
#
# Re-runnable. `./run.sh down` tears the container back down.

set -euo pipefail

cd "$(dirname "$0")"
DEMO_DIR="$(pwd)"
REPO_ROOT="$(cd ../.. && pwd)"

if [[ "${1:-}" == "down" ]]; then
    docker compose down -v
    rm -f .env dashboard.html
    echo "✓ torn down"
    exit 0
fi

# Resolve the agent-mv command. Prefer the globally-installed CLI; fall back
# to `uv run` from the repo root so a fresh clone works without `uv tool install`.
if command -v agent-mv >/dev/null 2>&1; then
    AGENT_MV=(agent-mv)
else
    if ! command -v uv >/dev/null 2>&1; then
        echo "Need either 'agent-mv' on PATH or 'uv' to fall back. Install one." >&2
        exit 1
    fi
    AGENT_MV=(uv run --project "$REPO_ROOT" agent-mv)
fi

if ! command -v dot >/dev/null 2>&1; then
    echo "graphviz ('dot') is required for 'agent-mv dashboard build'." >&2
    echo "  macOS:  brew install graphviz" >&2
    echo "  Debian: sudo apt install graphviz" >&2
    exit 1
fi

echo "==> bringing up Postgres"
docker compose up -d --wait

echo "==> writing .env from .env.example"
[[ -f .env ]] || cp .env.example .env

# agent-mv auto-loads .env; we export here for psql/echo too.
set -a
# shellcheck disable=SC1091
source .env
set +a

echo
echo "==> agent-mv apply  (creates roles, schema, 3 views, lineage)"
"${AGENT_MV[@]}" apply --yes

echo
echo "==> agent-mv doctor  (proves runtime role cannot read base tables)"
"${AGENT_MV[@]}" doctor

echo
# `apply` already populates each view (CREATE MATERIALIZED VIEW … AS …), so
# refresh-all here is pedagogical: it exercises the topo-ordered refresh and
# writes rows into agent_mv.refresh_history that the dashboard renders.
echo "==> agent-mv refresh-all  (topological order: customer_rollup → top_customers_30d)"
"${AGENT_MV[@]}" refresh-all

echo
echo "==> agent-mv status  (rich-table view of current state, post-refresh)"
"${AGENT_MV[@]}" status

echo
echo "==> agent-mv dashboard build"
"${AGENT_MV[@]}" dashboard build --out dashboard.html

echo
echo "==> querying agent_mv.top_customers_30d as the runtime role"
psql "$AGENT_MV_RUNTIME_URL" -c "SELECT email, order_count, lifetime_value_cents FROM agent_mv.top_customers_30d;"

echo
echo "==> proving the boundary: same role trying to read public.users"
# Capture into a var so `set -o pipefail` doesn't kill the script when psql
# exits non-zero on permission-denied (which is the success case here).
boundary_output=$(psql "$AGENT_MV_RUNTIME_URL" -c "SELECT count(*) FROM public.users;" 2>&1 || true)
denial=$(echo "$boundary_output" | grep -E "permission denied" || true)
if [[ -n "$denial" ]]; then
    echo "$denial"
    echo "  ✓ blocked by Postgres, as expected"
else
    echo "$boundary_output"
    echo "  ✗ boundary breach!"
    exit 1
fi

echo
echo "Dashboard: file://$DEMO_DIR/dashboard.html"
echo "Tear down: ./run.sh down"
