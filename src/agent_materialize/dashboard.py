from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import graphviz
import psycopg
from jinja2 import Environment, FileSystemLoader, select_autoescape


def _render_lineage_svg(views_with_sources: list[dict]) -> str:
    g = graphviz.Digraph("lineage", format="svg")
    g.attr("graph", rankdir="LR", bgcolor="transparent")
    g.attr("node", fontname="Helvetica", fontsize="10")

    seen_tables: set[str] = set()
    seen_views: set[str] = set()
    for v in views_with_sources:
        seen_views.add(v["name"])
        for src in v["sources"]:
            seen_tables.add(src)

    for tbl in seen_tables:
        g.node(f"t__{tbl}", label=tbl, shape="box", style="filled", fillcolor="#e8f0ff")
    for vn in seen_views:
        g.node(f"v__{vn}", label=vn, shape="component", style="filled", fillcolor="#fff0e8")

    for v in views_with_sources:
        for src in v["sources"]:
            g.edge(f"t__{src}", f"v__{v['name']}")
        for dep in v.get("depends_on", []):
            g.edge(f"v__{dep}", f"v__{v['name']}")

    svg_bytes = g.pipe(format="svg")
    svg = svg_bytes.decode("utf-8")
    if svg.startswith("<?xml"):
        svg = svg.split("?>", 1)[1].lstrip()
    return svg


def build_dashboard(*, runtime_dsn: str, config_path: Path, out_path: Path) -> None:
    from agent_materialize.config import load_config

    cfg = load_config(config_path)

    with psycopg.connect(runtime_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT view_name,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'table') AS sources,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'view') AS depends_on
                FROM {cfg.target_schema}.lineage
                GROUP BY view_name
                """
            )
            lineage = {
                row[0]: {"sources": list(row[1] or []), "depends_on": list(row[2] or [])}
                for row in cur.fetchall()
            }

            views: list[dict] = []
            for v in cfg.views:
                cur.execute(f"SELECT count(*) FROM {cfg.target_schema}.{v.name}")
                rows = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT finished_at, status FROM {cfg.target_schema}.refresh_history
                    WHERE view_name = %s ORDER BY started_at DESC LIMIT 1
                    """,
                    (v.name,),
                )
                hist = cur.fetchone()
                lin = lineage.get(v.name, {"sources": [], "depends_on": []})
                views.append({
                    "name": v.name,
                    "description": v.description,
                    "sources": lin["sources"],
                    "depends_on": lin["depends_on"],
                    "last_refreshed_at": hist[0].isoformat() if hist and hist[0] else None,
                    "last_status": hist[1] if hist else None,
                    "row_count": rows,
                })

            cur.execute(
                f"""
                SELECT view_name, started_at, finished_at, status, mode, rows_after, error
                FROM {cfg.target_schema}.refresh_history
                ORDER BY started_at DESC LIMIT 50
                """
            )
            history = [
                {"view_name": r[0], "started_at": r[1].isoformat(),
                 "finished_at": r[2].isoformat() if r[2] else None,
                 "status": r[3], "mode": r[4], "rows_after": r[5], "error": r[6]}
                for r in cur.fetchall()
            ]

    lineage_svg = _render_lineage_svg(views)

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")
    html = template.render(
        target_schema=cfg.target_schema,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        views=views,
        history=history,
        lineage_svg=lineage_svg,
    )
    out_path.write_text(html)
