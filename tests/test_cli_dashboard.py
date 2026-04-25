from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.apply import apply_config
from agent_materialize.cli import app
from agent_materialize.config import load_config


def test_dashboard_build_command(tmp_path: Path, with_base_tables, monkeypatch):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text("SELECT 1::bigint AS n")
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)
    after_at = with_base_tables.split("@")[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")

    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "build"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "dashboard.html").is_file()
