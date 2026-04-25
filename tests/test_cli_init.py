from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.cli import app


def test_init_creates_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "materialize.yaml").is_file()
    assert (tmp_path / ".env.example").is_file()
    assert (tmp_path / "materialize").is_dir()
    skills_dir = tmp_path / ".claude" / "skills" / "agent-materialize"
    assert skills_dir.is_dir()
    skill_target = skills_dir / "setup-database.md"
    assert skill_target.is_symlink() or skill_target.is_file()


def test_init_does_not_clobber_existing_yaml(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "materialize.yaml").write_text("version: 1\ntarget_schema: x\nviews: []\n")
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "already exists" in combined
