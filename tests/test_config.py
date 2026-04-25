from pathlib import Path

import pytest

from agent_materialize.config import Config, ConfigError, load_config


def test_load_minimal_config(tmp_path: Path) -> None:
    yaml_path = tmp_path / "materialize.yaml"
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "v1.sql").write_text("SELECT 1 AS x")
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: v1
            sql_file: materialize/v1.sql
            description: "One"
        """
    )
    cfg = load_config(yaml_path)
    assert isinstance(cfg, Config)
    assert cfg.version == 1
    assert cfg.target_schema == "agent_mv"
    assert len(cfg.views) == 1
    assert cfg.views[0].name == "v1"
    assert cfg.views[0].sql == "SELECT 1 AS x"


def test_load_rejects_unknown_version(tmp_path: Path) -> None:
    yaml_path = tmp_path / "materialize.yaml"
    yaml_path.write_text("version: 99\ntarget_schema: x\nviews: []\n")
    with pytest.raises(ConfigError, match="version"):
        load_config(yaml_path)


def test_load_rejects_missing_sql_file(tmp_path: Path) -> None:
    yaml_path = tmp_path / "materialize.yaml"
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: v1
            sql_file: materialize/missing.sql
            description: "X"
        """
    )
    with pytest.raises(ConfigError, match="sql_file"):
        load_config(yaml_path)


def test_load_rejects_duplicate_view_names(tmp_path: Path) -> None:
    yaml_path = tmp_path / "materialize.yaml"
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "v1.sql").write_text("SELECT 1")
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: v1
            sql_file: materialize/v1.sql
            description: "a"
          - name: v1
            sql_file: materialize/v1.sql
            description: "b"
        """
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(yaml_path)


def test_write_sources_updates_yaml_in_place(tmp_path: Path) -> None:
    from agent_materialize.config import write_sources

    yaml_path = tmp_path / "materialize.yaml"
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "v1.sql").write_text("SELECT 1")
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: v1
            sql_file: materialize/v1.sql
            description: "a"
        """
    )
    write_sources(yaml_path, "v1", ["public.users", "public.orders"])
    cfg = load_config(yaml_path)
    assert cfg.views[0].sources == ["public.orders", "public.users"]
