from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import sqlglot
from sqlglot import expressions as exp


class SampleQueryError(ValueError):
    pass


def _admin_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for setup-mcp")
    return dsn


def list_schemas() -> list[str]:
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' "
                "ORDER BY schema_name"
            )
            return [r[0] for r in cur.fetchall()]


def list_tables(schema: str) -> list[str]:
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') "
                "ORDER BY table_name",
                (schema,),
            )
            return [r[0] for r in cur.fetchall()]


def describe_table(name: str) -> dict:
    schema, table = name.split(".", 1) if "." in name else ("public", name)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, table),
            )
            cols = [
                {"name": r[0], "type": r[1], "nullable": r[2] == "YES"}
                for r in cur.fetchall()
            ]
    return {"name": f"{schema}.{table}", "columns": cols}


def profile_table(name: str) -> dict:
    schema, table = name.split(".", 1) if "." in name else ("public", name)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            row_count = cur.fetchone()[0]
            cur.execute(
                """
                SELECT kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type='FOREIGN KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
                """,
                (schema, table),
            )
            fks = [
                {"column": r[0], "references": f"{r[1]}.{r[2]}.{r[3]}"}
                for r in cur.fetchall()
            ]
    return {"name": f"{schema}.{table}", "row_count": row_count, "foreign_keys": fks}


_FORBIDDEN_PREFIX_RE = re.compile(r"\bpg_[a-z_]+", re.IGNORECASE)


def sample_query(sql: str, limit: int = 1000) -> list[dict]:
    """Execute a read-only SELECT, capped at 1000 rows."""
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed or not isinstance(parsed[0], (exp.Select, exp.Subquery, exp.With)):
        raise SampleQueryError("only read-only SELECT statements are allowed")
    if len(parsed) != 1:
        raise SampleQueryError("only a single statement is allowed")
    for tbl in parsed[0].find_all(exp.Table):
        full = f"{tbl.db or ''}.{tbl.name}".lstrip(".")
        if _FORBIDDEN_PREFIX_RE.match(tbl.name) or full.startswith("pg_"):
            raise SampleQueryError(f"queries against pg_* are forbidden: {full}")
        if (tbl.db or "").lower() == "information_schema":
            raise SampleQueryError("queries against information_schema are forbidden")

    capped = min(limit, 1000)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM ({sql}) _sub LIMIT {capped}")
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def read_repo_files(globs: list[str]) -> dict[str, str]:
    """Read files matching the globs (relative to CWD); return {path: contents}.

    Capped at 100 files and 200KB per file. Skips binary files.
    """
    out: dict[str, str] = {}
    cwd = Path.cwd()
    for pattern in globs:
        for path in cwd.glob(pattern):
            if not path.is_file():
                continue
            if len(out) >= 100:
                break
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            if len(content) > 200_000:
                content = content[:200_000] + "\n... [truncated]"
            out[str(path.relative_to(cwd))] = content
    return out


_STAGING_FILE = ".materialize-staging.yaml"


def propose_view(name: str, sql: str, rationale: str) -> dict:
    """Append a proposal to the staging YAML. Returns the staged proposal list."""
    import yaml as _yaml

    staging_path = Path.cwd() / _STAGING_FILE
    if staging_path.exists():
        raw = _yaml.safe_load(staging_path.read_text()) or {"proposals": []}
    else:
        raw = {"proposals": []}
    raw["proposals"].append({"name": name, "sql": sql, "rationale": rationale})
    staging_path.write_text(_yaml.safe_dump(raw, sort_keys=False))
    return raw


def finalize_config(approved_names: list[str]) -> dict:
    """Move approved staged proposals into materialize.yaml + materialize/<name>.sql."""
    import yaml as _yaml

    staging_path = Path.cwd() / _STAGING_FILE
    if not staging_path.exists():
        raise RuntimeError("no staging file; nothing to finalize")
    staging = _yaml.safe_load(staging_path.read_text()) or {"proposals": []}
    proposals = {p["name"]: p for p in staging["proposals"]}

    yaml_path = Path.cwd() / "materialize.yaml"
    raw = _yaml.safe_load(yaml_path.read_text())
    raw.setdefault("views", [])

    sql_dir = Path.cwd() / "materialize"
    sql_dir.mkdir(exist_ok=True)

    added: list[str] = []
    for name in approved_names:
        if name not in proposals:
            raise ValueError(f"unknown proposal: {name}")
        p = proposals[name]
        sql_path = sql_dir / f"{name}.sql"
        sql_path.write_text(p["sql"])
        raw["views"].append(
            {
                "name": name,
                "sql_file": f"materialize/{name}.sql",
                "description": p["rationale"][:200],
            }
        )
        added.append(name)

    yaml_path.write_text(_yaml.safe_dump(raw, sort_keys=False))
    staging_path.unlink()
    return {"added": added}


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("agent-materialize-setup")
    server.tool()(list_schemas)
    server.tool()(list_tables)
    server.tool()(describe_table)
    server.tool()(profile_table)
    server.tool()(sample_query)
    server.tool()(read_repo_files)
    server.tool()(propose_view)
    server.tool()(finalize_config)
    server.run()
