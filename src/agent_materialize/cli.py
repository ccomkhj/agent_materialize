from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from dotenv import load_dotenv

app = typer.Typer(help="agent-materialize CLI")


@app.callback()
def _autoload_env() -> None:
    """Load .env from cwd before running any subcommand. Existing env vars win."""
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)


SKILL_NAMES = [
    "setup-database.md",
    "querying-views.md",
    "adding-a-view.md",
    "troubleshoot-refresh.md",
]

# (dst_filename, src_filename) — dst names are user-typed slash commands.
COMMAND_FILES = [
    ("agent-materialize-onboard.md", "onboard.md"),
    ("agent-materialize-add-view.md", "add-view.md"),
    ("agent-materialize-troubleshoot.md", "troubleshoot.md"),
]


@app.command()
def init() -> None:
    """Scaffold materialize.yaml, .env.example, materialize/, and symlink skills."""
    cwd = Path.cwd()
    yaml_path = cwd / "materialize.yaml"
    if yaml_path.exists():
        typer.echo(f"materialize.yaml already exists at {yaml_path}", err=True)
        raise typer.Exit(code=1)

    yaml_path.write_text(
        "version: 1\n"
        "target_schema: agent_mv\n"
        "views: []\n"
    )
    (cwd / ".env.example").write_text(
        "# Connection string with full setup-time privileges (used by agent-mv apply + setup-mcp)\n"
        "DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME\n"
        "# Connection string with view-only runtime privileges (used by runtime-mcp).\n"
        "# The password here MUST match AGENT_MV_RUNTIME_PASSWORD below.\n"
        "AGENT_MV_RUNTIME_URL=postgresql://agent_mv_runtime:PASS@HOST:5432/DBNAME\n"
        "# Password set on the runtime role when `agent-mv apply` first creates it.\n"
        "# Must match the password embedded in AGENT_MV_RUNTIME_URL above. Default: agent_mv_runtime.\n"
        "AGENT_MV_RUNTIME_PASSWORD=PASS\n"
    )
    (cwd / "materialize").mkdir(exist_ok=True)

    mcp_path = cwd / ".mcp.json"
    if not mcp_path.exists():
        mcp_template = (Path(__file__).parent / "templates" / "mcp.json").read_text()
        mcp_path.write_text(mcp_template)

    skills_dst = cwd / ".claude" / "skills" / "agent-materialize"
    skills_dst.mkdir(parents=True, exist_ok=True)
    skills_src = Path(__file__).parent / "skills"
    for name in SKILL_NAMES:
        src = skills_src / name
        dst = skills_dst / name
        if dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    commands_dst = cwd / ".claude" / "commands"
    commands_dst.mkdir(parents=True, exist_ok=True)
    commands_src = Path(__file__).parent / "commands"
    for dst_name, src_name in COMMAND_FILES:
        src = commands_src / src_name
        dst = commands_dst / dst_name
        if dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    typer.echo("✓ initialized agent-materialize")
    typer.echo("  - materialize.yaml")
    typer.echo("  - .env.example")
    typer.echo("  - materialize/  (empty — your agent fills this during onboarding)")
    typer.echo("  - .mcp.json (setup MCP wired up)")
    typer.echo(f"  - skills symlinked to {skills_dst}")
    typer.echo(f"  - slash commands symlinked to {commands_dst}")


@app.command()
def discover() -> None:
    """Run discovery via the setup-mcp server (this command is informational)."""
    typer.echo(
        "Discovery is agent-driven. Wire up the setup-mcp in your MCP client and ask\n"
        "the agent to run the `setup-database` skill. See README for details."
    )


@app.command()
def apply(
    yes: bool = typer.Option(False, "--yes", help="Auto-confirm drops"),
) -> None:
    """Diff materialize.yaml against the database; create/update/drop MVs."""
    import os

    import psycopg  # noqa: F401 — imported for side-effects check at module level

    from agent_materialize.apply import apply_config
    from agent_materialize.config import load_config

    cwd = Path.cwd()
    yaml_path = cwd / "materialize.yaml"
    if not yaml_path.is_file():
        typer.echo("materialize.yaml not found. Run `agent-mv init` first.", err=True)
        raise typer.Exit(code=1)

    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        typer.echo("DATABASE_URL is not set", err=True)
        raise typer.Exit(code=1)
    runtime_password = os.environ.get("AGENT_MV_RUNTIME_PASSWORD", "agent_mv_runtime")

    cfg = load_config(yaml_path)
    has_configured_views = bool(cfg.views)

    def _confirm(names: list[str]) -> bool:
        if yes:
            return True
        return typer.confirm(f"Drop {len(names)} view(s): {', '.join(names)}?")

    apply_config(
        cfg,
        config_path=yaml_path,
        admin_dsn=admin_dsn,
        runtime_role="agent_mv_runtime",
        runtime_password=runtime_password,
        confirm_drops=_confirm,
    )
    if not has_configured_views:
        typer.echo(
            "No views are configured in materialize.yaml, so `agent-mv apply` only "
            "bootstrapped the schema/roles and reconciled any existing materialized views.\n"
            "To choose views interactively, wire up the setup MCP and run the "
            "`setup-database` skill, or add view definitions to materialize.yaml and re-run "
            "`agent-mv apply`."
        )
        typer.echo("✓ applied 0 view(s)")
        return

    typer.echo(f"✓ applied {len(cfg.views)} view(s)")


@app.command()
def doctor() -> None:
    """Verify roles, schema, and the access boundary."""
    import os

    import psycopg

    admin_dsn = os.environ.get("DATABASE_URL")
    runtime_dsn = os.environ.get("AGENT_MV_RUNTIME_URL")
    if not admin_dsn or not runtime_dsn:
        typer.echo("DATABASE_URL and AGENT_MV_RUNTIME_URL must be set", err=True)
        raise typer.Exit(code=1)

    # 1. agent_mv schema exists
    with psycopg.connect(admin_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'agent_mv'"
            )
            if cur.fetchone() is None:
                typer.echo("✗ agent_mv schema missing — run `agent-mv apply`", err=True)
                raise typer.Exit(code=1)

    # 2. runtime role can SELECT from agent_mv (validates basic GRANTs)
    with psycopg.connect(runtime_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT count(*) FROM agent_mv.refresh_history")
            except psycopg.errors.InsufficientPrivilege:
                typer.echo("✗ runtime role cannot read agent_mv schema", err=True)
                raise typer.Exit(code=1)
            except psycopg.errors.UndefinedTable:
                pass  # schema exists but table not yet created — grants are set at schema level

    # 3. CRITICAL: runtime role MUST NOT be able to read base tables.
    # Find any user table outside agent_mv / system schemas to test against.
    with psycopg.connect(admin_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT schemaname, tablename FROM pg_tables
                WHERE schemaname NOT LIKE 'pg_%%'
                  AND schemaname NOT IN ('information_schema', 'agent_mv')
                ORDER BY schemaname, tablename
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if row is None:
        typer.echo(
            "⚠ no user tables found outside agent_mv; cannot verify access boundary. "
            "Create at least one base table in a user schema and re-run.",
            err=True,
        )
        raise typer.Exit(code=1)
    sample_schema, sample_table = row

    with psycopg.connect(runtime_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f'SELECT 1 FROM "{sample_schema}"."{sample_table}" LIMIT 1')
                typer.echo(
                    f"✗ access boundary FAILED: runtime role can read {sample_schema}.{sample_table}",
                    err=True,
                )
                raise typer.Exit(code=1)
            except psycopg.errors.InsufficientPrivilege:
                pass  # expected

    typer.echo("✓ access boundary verified (runtime role cannot read base tables)")


import yaml as _yaml

from rich.console import Console
from rich.table import Table

from agent_materialize.lineage import topological_order, parse_sources

_console = Console()


def _runtime_dsn() -> str:
    dsn = os.environ.get("AGENT_MV_RUNTIME_URL")
    if not dsn:
        typer.echo("AGENT_MV_RUNTIME_URL is not set", err=True)
        raise typer.Exit(code=1)
    return dsn


@app.command()
def status() -> None:
    """Show a status table for all materialized views."""
    from agent_materialize.config import load_config
    import psycopg

    cfg = load_config(Path.cwd() / "materialize.yaml")
    dsn = _runtime_dsn()
    table = Table(title="agent-materialize: views")
    for col in ["name", "rows", "last_refreshed_at", "last_status", "sources"]:
        table.add_column(col)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for v in cfg.views:
                cur.execute(
                    f"SELECT count(*) FROM {cfg.target_schema}.{v.name}"
                )
                rows = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT finished_at, status FROM {cfg.target_schema}.refresh_history
                    WHERE view_name = %s ORDER BY started_at DESC LIMIT 1
                    """,
                    (v.name,),
                )
                hist = cur.fetchone()
                last_at = hist[0].isoformat() if hist and hist[0] else "-"
                last_status = hist[1] if hist else "-"
                table.add_row(v.name, str(rows), last_at, last_status, ", ".join(v.sources))
    _console.print(table)


@app.command()
def refresh(name: str) -> None:
    """Refresh a single view via the runtime SECURITY DEFINER function."""
    import psycopg

    dsn = _runtime_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT agent_mv.refresh_view(%s)", (name,))
                result = cur.fetchone()[0]
            except psycopg.Error as exc:
                typer.echo(f"refresh failed: {exc}", err=True)
                raise typer.Exit(code=1)
    typer.echo(f"✓ refreshed {name} ({result['mode']}, {result['rows_after']} rows)")


@app.command(name="refresh-all")
def refresh_all() -> None:
    """Refresh all views in topological order."""
    from agent_materialize.config import load_config
    import psycopg

    cfg = load_config(Path.cwd() / "materialize.yaml")
    deps = {v.name: parse_sources(v.sql, target_schema=cfg.target_schema)[1] for v in cfg.views}
    order = topological_order(deps)
    dsn = _runtime_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for name in order:
                cur.execute("SELECT agent_mv.refresh_view(%s)", (name,))
                typer.echo(f"✓ refreshed {name}")


@app.command()
def drop(name: str, yes: bool = typer.Option(False, "--yes")) -> None:
    """Drop a view: remove from YAML and from the database."""
    from agent_materialize.apply import apply_config
    from agent_materialize.config import load_config

    yaml_path = Path.cwd() / "materialize.yaml"
    raw = _yaml.safe_load(yaml_path.read_text())
    raw["views"] = [v for v in raw.get("views", []) if v["name"] != name]
    if not yes and not typer.confirm(f"Drop view '{name}'?"):
        raise typer.Exit(code=1)
    yaml_path.write_text(_yaml.safe_dump(raw, sort_keys=False))
    cfg = load_config(yaml_path)
    apply_config(
        cfg,
        config_path=yaml_path,
        admin_dsn=os.environ["DATABASE_URL"],
        runtime_role="agent_mv_runtime",
        runtime_password=os.environ.get("AGENT_MV_RUNTIME_PASSWORD", "agent_mv_runtime"),
        confirm_drops=lambda names: True,
    )
    typer.echo(f"✓ dropped {name}")


dashboard_app = typer.Typer(help="Static HTML dashboard")
app.add_typer(dashboard_app, name="dashboard")


@dashboard_app.command("build")
def dashboard_build(
    out: Path = typer.Option(Path("dashboard.html"), "--out", help="Output HTML path"),
) -> None:
    """Build a static dashboard.html from runtime DB state."""
    from agent_materialize.dashboard import build_dashboard

    build_dashboard(
        runtime_dsn=_runtime_dsn(),
        config_path=Path.cwd() / "materialize.yaml",
        out_path=out,
    )
    typer.echo(f"✓ wrote {out}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
