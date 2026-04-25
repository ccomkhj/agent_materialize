from pathlib import Path

import pytest

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config
from agent_materialize.mcp_runtime import (
    list_views, describe_view, query_view, refresh_view, get_lineage, QueryError,
)


def _setup(tmp_path: Path, with_base_tables: str, monkeypatch):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text(
        "SELECT u.id AS user_id, count(o.id)::bigint AS n_orders "
        "FROM public.users u LEFT JOIN public.orders o ON o.user_id = u.id GROUP BY u.id"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
        "    indexes:\n      - columns: [user_id]\n        unique: true\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)
    after_at = with_base_tables.split("@")[1]
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")
    monkeypatch.chdir(tmp_path)


def test_list_views(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    views = list_views()
    assert any(v["name"] == "uc" for v in views)
    uc = next(v for v in views if v["name"] == "uc")
    assert uc["row_count"] == 2
    assert "public.users" in uc["sources"]


def test_describe_view(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    desc = describe_view(name="uc")
    cols = {c["name"]: c["type"] for c in desc["columns"]}
    assert "user_id" in cols
    assert "n_orders" in cols


def test_query_view_caps_limit(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    rows = query_view(sql="SELECT * FROM agent_mv.uc", limit=10000)
    assert len(rows) <= 1000


def test_query_view_rejects_base_table(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    with pytest.raises(QueryError):
        query_view(sql="SELECT * FROM public.users")


def test_refresh_view_logs_history(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    out = refresh_view(name="uc")
    assert out["mode"] == "concurrent"
    assert out["rows_after"] == 2


def test_refresh_unknown_view_errors(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    with pytest.raises(Exception, match="unknown"):
        refresh_view(name="does_not_exist")


def test_get_lineage(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    lin = get_lineage(name="uc")
    assert "public.users" in lin["sources"]
    assert "public.orders" in lin["sources"]
    assert lin["depends_on"] == []
    assert lin["depended_on_by"] == []
