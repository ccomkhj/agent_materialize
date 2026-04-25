from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when materialize.yaml is invalid."""


class IndexSpec(BaseModel):
    columns: list[str] = Field(min_length=1)
    unique: bool = False


class ViewSpec(BaseModel):
    name: str
    sql_file: str
    description: str
    indexes: list[IndexSpec] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    sql: str = ""  # filled in after load

    @model_validator(mode="after")
    def _check_name(self) -> "ViewSpec":
        if not self.name.replace("_", "").isalnum():
            raise ValueError(f"view name must be alphanumeric/underscore: {self.name}")
        return self


class Config(BaseModel):
    version: Annotated[int, Field(ge=1, le=1)]
    target_schema: str
    views: list[ViewSpec]

    @model_validator(mode="after")
    def _no_duplicates(self) -> "Config":
        names = [v.name for v in self.views]
        if len(names) != len(set(names)):
            raise ValueError("duplicate view names in config")
        return self


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text())
        cfg = Config.model_validate(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    except ValidationError as exc:
        if "version" in str(exc):
            raise ConfigError(f"unsupported version in {path}: {exc}") from exc
        if "duplicate" in str(exc):
            raise ConfigError(f"duplicate view names in {path}: {exc}") from exc
        raise ConfigError(f"invalid config {path}: {exc}") from exc

    base = path.parent
    for view in cfg.views:
        sql_path = base / view.sql_file
        if not sql_path.is_file():
            raise ConfigError(f"sql_file not found for view '{view.name}': {sql_path}")
        view.sql = sql_path.read_text()
    return cfg


def write_sources(path: Path, view_name: str, sources: list[str]) -> None:
    """Update the `sources` field of one view in materialize.yaml in place."""
    raw = yaml.safe_load(path.read_text())
    for v in raw.get("views", []):
        if v["name"] == view_name:
            v["sources"] = sorted(sources)
            break
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
