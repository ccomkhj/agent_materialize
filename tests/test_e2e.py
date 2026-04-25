"""End-to-end: simulates a fresh project's full workflow.

init → write yaml + sql → apply → list_views via runtime → refresh → query → dashboard build.
"""
from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.apply import apply_config
from agent_materialize.cli import app
from agent_materialize.config import load_config
from agent_materialize.mcp_runtime import list_views, query_view, refresh_view, get_lineage


def test_e2e_full_workflow(tmp_path: Path, with_base_tables, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    # 1. init
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.stdout

    # 2. write a real view (overwriting init's empty views list)
    (tmp_path / "materialize" / "customer_orders.sql").write_text(
        "SELECT u.id AS user_id, u.name, count(o.id)::bigint AS n_orders, "
        "       coalesce(sum(o.amount), 0)::bigint AS total "
        "FROM public.users u LEFT JOIN public.orders o ON o.user_id = u.id "
        "GROUP BY u.id, u.name"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: customer_orders\n"
        "    sql_file: materialize/customer_orders.sql\n"
        "    description: \"Per-user order counts and totals\"\n"
        "    indexes:\n      - columns: [user_id]\n        unique: true\n"
    )

    # 3. apply (using direct call since CLI requires DATABASE_URL env)
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)

    # 4. set runtime env and exercise the runtime MCP functions
    after_at = with_base_tables.split("@")[1]
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")

    views = list_views()
    assert any(v["name"] == "customer_orders" for v in views)

    rows = query_view(sql="SELECT * FROM agent_mv.customer_orders ORDER BY user_id")
    assert rows == [
        {"user_id": 1, "name": "a", "n_orders": 2, "total": 12},
        {"user_id": 2, "name": "b", "n_orders": 1, "total": 3},
    ]

    refresh_out = refresh_view(name="customer_orders")
    assert refresh_out["mode"] == "concurrent"

    lineage = get_lineage(name="customer_orders")
    assert "public.users" in lineage["sources"]
    assert "public.orders" in lineage["sources"]

    # 5. dashboard build
    dash_out = tmp_path / "dashboard.html"
    r = runner.invoke(app, ["dashboard", "build", "--out", str(dash_out)])
    assert r.exit_code == 0, r.stdout
    html = dash_out.read_text()
    assert "customer_orders" in html

    # 6. doctor — set DATABASE_URL too so doctor can run
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.stdout
    assert "access boundary verified" in r.stdout.lower()
