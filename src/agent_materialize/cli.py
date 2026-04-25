from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer

app = typer.Typer(help="agent-materialize CLI")


SKILL_NAMES = [
    "setup-database.md",
    "querying-views.md",
    "adding-a-view.md",
    "troubleshoot-refresh.md",
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
        "# Connection string with view-only runtime privileges (used by runtime-mcp)\n"
        "AGENT_MV_RUNTIME_URL=postgresql://agent_mv_runtime:PASS@HOST:5432/DBNAME\n"
    )
    (cwd / "materialize").mkdir(exist_ok=True)

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

    typer.echo("✓ initialized agent-materialize")
    typer.echo("  - materialize.yaml")
    typer.echo("  - .env.example")
    typer.echo("  - materialize/")
    typer.echo(f"  - skills symlinked to {skills_dst}")


@app.command()
def discover() -> None:
    """Run discovery via the setup-mcp server (this command is informational)."""
    typer.echo(
        "Discovery is agent-driven. Wire up the setup-mcp in your MCP client and ask\n"
        "the agent to run the `setup-database` skill. See README for details."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
