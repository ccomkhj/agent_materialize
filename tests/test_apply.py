import logging
from pathlib import Path

import psycopg
import pytest

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def _write_simple_config(tmp_path: Path) -> Path:
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "user_count.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    yaml_path = tmp_path / "materialize.yaml"
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: user_count
            sql_file: materialize/user_count.sql
            description: "How many users"
        """
    )
    return yaml_path


def test_apply_creates_view(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(
        cfg,
        config_path=yaml_path,
        admin_dsn=with_base_tables,
        runtime_role="rt",
        runtime_password="rt",
        confirm_drops=lambda names: True,
    )
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.user_count")
            assert cur.fetchone() == (2,)


def test_apply_idempotent(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.user_count")
            assert cur.fetchone() == (2,)


def test_apply_writes_lineage_into_yaml_and_table(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    cfg2 = load_config(yaml_path)
    assert cfg2.views[0].sources == ["public.users"]

    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_kind, source_name FROM agent_mv.lineage WHERE view_name='user_count'"
            )
            assert cur.fetchall() == [("table", "public.users")]


def test_apply_prompts_before_drop_and_aborts_on_no(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg1 = load_config(yaml_path)
    apply_config(cfg1, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views: []
        """
    )
    cfg2 = load_config(yaml_path)

    prompted: list[list[str]] = []
    def deny(names):
        prompted.append(names)
        return False

    with pytest.raises(SystemExit, match="aborted"):
        apply_config(cfg2, config_path=yaml_path, admin_dsn=with_base_tables,
                     runtime_role="rt", runtime_password="rt",
                     confirm_drops=deny)

    assert prompted == [["user_count"]]

    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='user_count'"
            )
            assert cur.fetchone() == (1,)


def test_apply_drops_when_confirmed(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg1 = load_config(yaml_path)
    apply_config(cfg1, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views: []
        """
    )
    cfg2 = load_config(yaml_path)
    apply_config(cfg2, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='user_count'"
            )
            assert cur.fetchone() is None


def test_apply_logs_pg_depend_cross_check(with_base_tables, tmp_path, caplog):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    with caplog.at_level(logging.INFO, logger="agent_materialize.apply"):
        apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                     runtime_role="rt", runtime_password="rt",
                     confirm_drops=lambda names: True)
    assert any("pg_depend cross-check" in r.message for r in caplog.records)


def test_apply_refuses_to_drop_view_still_referenced_by_kept_view(with_base_tables, tmp_path):
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "uc.sql").write_text("SELECT count(*)::bigint AS n FROM public.users")
    (sql_dir / "uc_doubled.sql").write_text("SELECT n * 2 AS n2 FROM agent_mv.uc")
    yaml_path = tmp_path / "materialize.yaml"
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: uc
            sql_file: materialize/uc.sql
            description: a
          - name: uc_doubled
            sql_file: materialize/uc_doubled.sql
            description: b
        """
    )
    cfg1 = load_config(yaml_path)
    apply_config(cfg1, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)

    # Now remove `uc` but keep `uc_doubled` (which depends on it)
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: uc_doubled
            sql_file: materialize/uc_doubled.sql
            description: b
        """
    )
    cfg2 = load_config(yaml_path)

    with pytest.raises(ValueError, match="still referenced"):
        apply_config(cfg2, config_path=yaml_path, admin_dsn=with_base_tables,
                     runtime_role="rt", runtime_password="rt",
                     confirm_drops=lambda n: True)

    # MV should still exist
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='uc'"
            )
            assert cur.fetchone() == (1,)
