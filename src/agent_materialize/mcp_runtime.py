from __future__ import annotations

import os

import psycopg
import sqlglot
from sqlglot import expressions as exp


TARGET_SCHEMA = os.environ.get("AGENT_MV_TARGET_SCHEMA", "agent_mv")


class QueryError(ValueError):
    pass


def _runtime_dsn() -> str:
    dsn = os.environ.get("AGENT_MV_RUNTIME_URL")
    if not dsn:
        raise RuntimeError("AGENT_MV_RUNTIME_URL is required for runtime-mcp")
    return dsn


def list_views() -> list[dict]:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT view_name,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'table') AS sources,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'view') AS depends_on
                FROM {TARGET_SCHEMA}.lineage
                GROUP BY view_name
                ORDER BY view_name
                """
            )
            base_rows = cur.fetchall()

            results: list[dict] = []
            for view_name, sources, depends_on in base_rows:
                cur.execute(
                    f"SELECT count(*) FROM {TARGET_SCHEMA}.{view_name}"
                )
                rows = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT finished_at, status FROM {TARGET_SCHEMA}.refresh_history
                    WHERE view_name = %s ORDER BY started_at DESC LIMIT 1
                    """,
                    (view_name,),
                )
                hist = cur.fetchone()
                last_at = hist[0].isoformat() if hist and hist[0] else None
                last_status = hist[1] if hist else None
                results.append(
                    {
                        "name": view_name,
                        "row_count": rows,
                        "sources": list(sources or []),
                        "depends_on": list(depends_on or []),
                        "last_refreshed_at": last_at,
                        "last_status": last_status,
                    }
                )
            return results


def describe_view(name: str) -> dict:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            # information_schema.columns omits materialized views; use pg_catalog instead.
            cur.execute(
                """
                SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (TARGET_SCHEMA, name),
            )
            cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
    if not cols:
        raise QueryError(f"unknown view: {name}")
    return {"name": name, "columns": cols}


def query_view(sql: str, limit: int = 1000) -> list[dict]:
    """Execute a SELECT against agent_mv only. Caps limit at 1000."""
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed or not isinstance(parsed[0], (exp.Select, exp.Subquery, exp.With)):
        raise QueryError("only SELECT statements are allowed")
    for tbl in parsed[0].find_all(exp.Table):
        schema = (tbl.db or "").lower()
        if schema and schema != TARGET_SCHEMA.lower():
            raise QueryError(
                f"queries may only reference the {TARGET_SCHEMA} schema; "
                f"found {tbl.db}.{tbl.name}"
            )
    capped = min(limit, 1000)
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f"SELECT * FROM ({sql}) _sub LIMIT {capped}")
            except psycopg.errors.InsufficientPrivilege as exc:
                raise QueryError(f"permission denied: {exc}") from exc
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def refresh_view(name: str) -> dict:
    with psycopg.connect(_runtime_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {TARGET_SCHEMA}.refresh_view(%s)", (name,))
            return cur.fetchone()[0]


def get_lineage(name: str) -> dict:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT source_kind, source_name FROM {TARGET_SCHEMA}.lineage
                WHERE view_name = %s
                """,
                (name,),
            )
            rows = cur.fetchall()
            sources = sorted(r[1] for r in rows if r[0] == "table")
            depends_on = sorted(r[1] for r in rows if r[0] == "view")
            cur.execute(
                f"""
                SELECT view_name FROM {TARGET_SCHEMA}.lineage
                WHERE source_kind = 'view' AND source_name = %s
                """,
                (name,),
            )
            depended_on_by = sorted(r[0] for r in cur.fetchall())
    return {
        "name": name,
        "sources": sources,
        "depends_on": depends_on,
        "depended_on_by": depended_on_by,
    }


def main() -> None:
    from pathlib import Path

    from dotenv import load_dotenv
    from mcp.server.fastmcp import FastMCP

    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

    server = FastMCP("agent-materialize-runtime")
    server.tool()(list_views)
    server.tool()(describe_view)
    server.tool()(query_view)
    server.tool()(refresh_view)
    server.tool()(get_lineage)
    server.run()
