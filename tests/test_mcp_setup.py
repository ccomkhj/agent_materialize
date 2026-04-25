import pytest

from agent_materialize.mcp_setup import (
    list_schemas, list_tables, describe_table, profile_table,
    sample_query, propose_view, finalize_config, read_repo_files,
    SampleQueryError,
)


def test_list_schemas(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    schemas = list_schemas()
    assert "public" in schemas


def test_list_tables(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    tables = list_tables(schema="public")
    assert "users" in tables
    assert "orders" in tables


def test_describe_table(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    desc = describe_table(name="public.users")
    cols = {c["name"]: c["type"] for c in desc["columns"]}
    assert cols["id"] == "integer"
    assert cols["name"] == "text"


def test_profile_table_returns_row_count(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    profile = profile_table(name="public.users")
    assert profile["row_count"] == 2


def test_sample_query_caps_limit(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    rows = sample_query(sql="SELECT * FROM public.users", limit=10000)
    assert len(rows) <= 1000


def test_sample_query_rejects_pg_catalog(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    with pytest.raises(SampleQueryError, match="pg_"):
        sample_query(sql="SELECT * FROM pg_class")


def test_sample_query_rejects_writes(monkeypatch, with_base_tables):
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    with pytest.raises(SampleQueryError, match="read-only"):
        sample_query(sql="INSERT INTO public.users VALUES (3, 'x')")


def test_propose_and_finalize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews: []\n"
    )
    propose_view(name="v1", sql="SELECT 1 AS x", rationale="just a test")
    propose_view(name="v2", sql="SELECT 2 AS x", rationale="another")
    out = finalize_config(approved_names=["v1"])
    assert out == {"added": ["v1"]}
    assert (tmp_path / "materialize" / "v1.sql").read_text() == "SELECT 1 AS x"
    assert not (tmp_path / ".materialize-staging.yaml").exists()


def test_read_repo_files_caps_size(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 300_000)
    out = read_repo_files(globs=["*.txt"])
    assert "big.txt" in out
    assert "[truncated]" in out["big.txt"]
