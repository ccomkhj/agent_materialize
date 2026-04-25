"""CI BLOCKER: the runtime role must NEVER read base tables.

If this test fails, the framework's core security promise is broken.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def _runtime_url(fresh_db: str, role: str, pw: str) -> str:
    after_at = fresh_db.split("@")[1]
    return f"postgresql://{role}:{pw}@{after_at}"


def _seed(tmp_path: Path, with_base_tables: str):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)


def test_runtime_role_cannot_select_base_tables(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT * FROM public.users")


def test_runtime_role_cannot_drop_views(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("DROP MATERIALIZED VIEW agent_mv.uc")


def test_runtime_role_cannot_refresh_directly(tmp_path, with_base_tables):
    """Refresh must go through the SECURITY DEFINER function. Direct REFRESH must fail."""
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("REFRESH MATERIALIZED VIEW agent_mv.uc")


def test_runtime_role_cannot_refresh_arbitrary_name_via_function(tmp_path, with_base_tables):
    """The SECURITY DEFINER function must validate against the lineage allowlist."""
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.Error, match="unknown"):
                cur.execute("SELECT agent_mv.refresh_view('public.users')")


def test_runtime_role_can_select_view(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.uc")
            assert cur.fetchone() == (2,)
