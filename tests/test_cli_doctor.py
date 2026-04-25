import psycopg
from typer.testing import CliRunner

from agent_materialize.cli import app
from agent_materialize.schema import bootstrap_schema


def _runtime_url(fresh_db: str, role: str, pw: str) -> str:
    after_at = fresh_db.split("@")[1]
    return f"postgresql://{role}:{pw}@{after_at}"


def test_doctor_passes_after_bootstrap(monkeypatch, with_base_tables):
    bootstrap_schema(with_base_tables, target_schema="agent_mv",
                     runtime_role="rt", runtime_password="rt")
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", runtime)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "access boundary verified" in result.stdout.lower()


def test_doctor_fails_when_runtime_can_read_base_tables(monkeypatch, with_base_tables):
    # Create runtime role with too many privileges (skip bootstrap, give SUPERUSER).
    # Use a distinct role name so the cluster-wide "rt" role used by other tests is not polluted.
    role = "rt_bad_actor"
    pw = "rt_bad_actor"
    with psycopg.connect(with_base_tables, autocommit=True) as conn:
        conn.execute(f"DROP ROLE IF EXISTS {role}")
        conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}' SUPERUSER")
        conn.execute("CREATE SCHEMA IF NOT EXISTS agent_mv")  # so first check passes
    runtime = _runtime_url(with_base_tables, role, pw)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", runtime)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "access boundary" in combined
