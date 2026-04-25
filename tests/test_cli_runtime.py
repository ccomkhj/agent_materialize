from pathlib import Path

import psycopg
from typer.testing import CliRunner

from agent_materialize.apply import apply_config
from agent_materialize.cli import app
from agent_materialize.config import load_config


def _setup(tmp_path: Path, with_base_tables: str, monkeypatch):
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    after_at = with_base_tables.split("@")[1]
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")


def test_status(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "uc" in result.stdout


def test_refresh_one(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh", "uc"])
    assert result.exit_code == 0, result.stdout
    assert "refreshed" in result.stdout.lower()


def test_refresh_unknown_view_fails(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh", "nope"])
    assert result.exit_code != 0


def test_refresh_all_uses_topo_order(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh-all"])
    assert result.exit_code == 0, result.stdout


def test_drop_removes_from_yaml_and_db(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["drop", "uc", "--yes"])
    assert result.exit_code == 0, result.stdout
    cfg = load_config(tmp_path / "materialize.yaml")
    assert cfg.views == []
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='uc'"
            )
            assert cur.fetchone() is None
