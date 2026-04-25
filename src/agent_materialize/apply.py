from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import psycopg

from agent_materialize.config import Config, write_sources
from agent_materialize.lineage import parse_sources, topological_order
from agent_materialize.schema import bootstrap_schema

log = logging.getLogger(__name__)


def _ident(name: str) -> str:
    """Validate a Postgres identifier: must start with letter/underscore, then alnum/underscore."""
    if not name or not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"unsafe identifier: {name}")
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name}")
    return name


def apply_config(
    cfg: Config,
    *,
    config_path: Path,
    admin_dsn: str,
    runtime_role: str,
    runtime_password: str,
    confirm_drops: Callable[[list[str]], bool],
) -> None:
    """Apply `cfg` to the database. Idempotent.

    `confirm_drops` is called with the list of view names that the YAML no longer mentions.
    Returning True allows the drops; False aborts without dropping.
    """
    bootstrap_schema(
        admin_dsn,
        target_schema=cfg.target_schema,
        runtime_role=runtime_role,
        runtime_password=runtime_password,
    )
    schema = _ident(cfg.target_schema)

    # 1. compute lineage and sort
    deps: dict[str, set[str]] = {}
    sources_per_view: dict[str, list[str]] = {}
    for v in cfg.views:
        base, mvs = parse_sources(v.sql, target_schema=cfg.target_schema)
        deps[v.name] = mvs
        sources_per_view[v.name] = sorted(base | {f"{cfg.target_schema}.{m}" for m in mvs})
    order = topological_order(deps)

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 2. find existing MVs in the schema
            cur.execute(
                "SELECT matviewname FROM pg_matviews WHERE schemaname = %s",
                (cfg.target_schema,),
            )
            existing = {row[0] for row in cur.fetchall()}

            # 3. drop views that are no longer in config
            wanted = {v.name for v in cfg.views}
            removed = sorted(existing - wanted)
            # Guard: a removed view depended on by a kept view would cause a partial-apply
            # failure mid-DROP. Surface it as a clear error before touching the database.
            kept_dependencies = {dep for v in cfg.views for dep in deps[v.name]}
            blocked = [r for r in removed if r in kept_dependencies]
            if blocked:
                raise ValueError(
                    f"cannot drop views {blocked}: still referenced by kept views in materialize.yaml"
                )
            if removed:
                if not confirm_drops(removed):
                    raise SystemExit("apply aborted: drops not confirmed")
                for name in removed:
                    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {schema}.{_ident(name)}")
                    cur.execute(
                        f"DELETE FROM {schema}.lineage WHERE view_name = %s", (name,)
                    )

            # 4. create or replace views in topological order
            views_by_name = {v.name: v for v in cfg.views}
            for name in order:
                v = views_by_name[name]
                if name in existing:
                    cur.execute(f"DROP MATERIALIZED VIEW {schema}.{_ident(name)}")
                cur.execute(
                    f"CREATE MATERIALIZED VIEW {schema}.{_ident(name)} AS {v.sql}"
                )
                for idx in v.indexes:
                    cols = ", ".join(_ident(c) for c in idx.columns)
                    unique = "UNIQUE " if idx.unique else ""
                    idx_name = f"{name}_{'_'.join(idx.columns)}_idx"
                    cur.execute(
                        f"CREATE {unique}INDEX IF NOT EXISTS {_ident(idx_name)} "
                        f"ON {schema}.{_ident(name)} ({cols})"
                    )
                cur.execute(
                    f"GRANT SELECT ON {schema}.{_ident(name)} TO {_ident(runtime_role)}"
                )

            # 5. write lineage into the table
            # Scoped delete: only clear rows for views we manage; leaves any external rows alone.
            managed_names = list(sources_per_view.keys())
            cur.execute(
                f"DELETE FROM {schema}.lineage WHERE view_name = ANY(%s)",
                (managed_names,),
            )
            schema_prefix = cfg.target_schema + "."
            for name in order:
                for src in sources_per_view[name]:
                    if src.startswith(schema_prefix):
                        kind = "view"
                        stored = src[len(schema_prefix):]
                    else:
                        kind = "table"
                        stored = src
                    cur.execute(
                        f"INSERT INTO {schema}.lineage (view_name, source_kind, source_name) "
                        f"VALUES (%s, %s, %s)",
                        (name, kind, stored),
                    )

            # 6. pg_depend cross-check (defensive — sqlglot is the YAML source of truth)
            for name in order:
                cur.execute(
                    """
                    SELECT DISTINCT n.nspname || '.' || c.relname AS src
                    FROM pg_depend d
                    JOIN pg_rewrite r ON r.oid = d.objid
                    JOIN pg_class mv ON mv.oid = r.ev_class
                    JOIN pg_namespace mvn ON mvn.oid = mv.relnamespace
                    JOIN pg_class c ON c.oid = d.refobjid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE mvn.nspname = %s AND mv.relname = %s
                      AND c.relkind IN ('r', 'm')
                      AND NOT (n.nspname = mvn.nspname AND c.relname = mv.relname)
                    """,
                    (cfg.target_schema, name),
                )
                pg_sources = {row[0] for row in cur.fetchall()}
                sqlglot_sources = set(sources_per_view[name])
                log.info(
                    "pg_depend cross-check for %s: pg=%s sqlglot=%s",
                    name, sorted(pg_sources), sorted(sqlglot_sources),
                )
                if pg_sources != sqlglot_sources:
                    log.warning(
                        "lineage mismatch for view '%s': pg_depend=%s sqlglot=%s",
                        name, sorted(pg_sources), sorted(sqlglot_sources),
                    )

    # 7. write sources back to YAML (outside DB transaction)
    for name, srcs in sources_per_view.items():
        write_sources(config_path, name, srcs)
