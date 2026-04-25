from pathlib import Path

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config
from agent_materialize.dashboard import build_dashboard


def _seed(tmp_path: Path, with_base_tables: str):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: \"user count\"\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)


def test_dashboard_build_writes_html(tmp_path, with_base_tables, monkeypatch):
    _seed(tmp_path, with_base_tables)
    after_at = with_base_tables.split("@")[1]
    runtime = f"postgresql://rt:rt@{after_at}"
    out_path = tmp_path / "dashboard.html"
    build_dashboard(
        runtime_dsn=runtime,
        config_path=tmp_path / "materialize.yaml",
        out_path=out_path,
    )
    assert out_path.is_file()
    html = out_path.read_text()
    assert "<!DOCTYPE html>" in html
    assert "uc" in html
    assert "user count" in html
    assert "<svg" in html
