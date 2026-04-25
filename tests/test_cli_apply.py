from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.cli import app


def _seed_config(tmp: Path):
    (tmp / "materialize").mkdir()
    (tmp / "materialize" / "user_count.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp / "materialize.yaml").write_text(
        "version: 1\n"
        "target_schema: agent_mv\n"
        "views:\n"
        "  - name: user_count\n"
        "    sql_file: materialize/user_count.sql\n"
        "    description: \"u\"\n"
    )


def test_apply_uses_env(monkeypatch, tmp_path, with_base_tables):
    _seed_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    monkeypatch.setenv("AGENT_MV_RUNTIME_PASSWORD", "rt")
    runner = CliRunner()
    result = runner.invoke(app, ["apply", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "✓" in result.stdout or "applied" in result.stdout.lower()


def test_apply_loads_database_url_from_dotenv(monkeypatch, tmp_path, with_base_tables):
    _seed_config(tmp_path)
    (tmp_path / ".env").write_text(
        f"DATABASE_URL={with_base_tables}\n"
        "AGENT_MV_RUNTIME_PASSWORD=rt\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MV_RUNTIME_PASSWORD", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert "✓" in result.stdout or "applied" in result.stdout.lower()


def test_apply_explains_empty_config(monkeypatch, tmp_path, with_base_tables):
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\n"
        "target_schema: agent_mv\n"
        "views: []\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)

    runner = CliRunner()
    result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert "No views are configured in materialize.yaml" in result.stdout
    assert "setup-database" in result.stdout
