# agent-materialize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shareable Python framework that wraps Postgres with a thin "foundation layer" of materialized views, enforces a two-role access boundary, and ships skills + two MCP servers + a CLI + a static HTML dashboard.

**Architecture:** One Python package, four entrypoints (`mcp-setup`, `mcp-runtime`, `agent-mv` CLI, dashboard builder). Setup runs as a privileged `agent_mv_setup` Postgres role; runtime runs as `agent_mv_runtime` (view-only + `EXECUTE` on a `SECURITY DEFINER` refresh function). Config lives in `materialize.yaml` + per-view `materialize/<name>.sql`. Lineage is parsed with `sqlglot` at apply time and stored in both YAML and a metadata table.

**Tech Stack:** Python 3.11+, `uv` for package management, `psycopg[binary]` (psycopg3) for Postgres, `sqlglot` for SQL parsing, `pyyaml` + `pydantic` for config, `typer` + `rich` for CLI, `mcp[server]` (FastMCP) for MCP servers, `pytest` + `testcontainers[postgres]` for integration tests, `jinja2` + `graphviz` (Python package + system binary) for dashboard.

**Spec:** `docs/superpowers/specs/2026-04-25-agent-materialize-design.md`

---

## Phase 0 — Project scaffolding

### Task 0.1: Create the Python package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/agent_materialize/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "agent-materialize"
version = "0.1.0"
description = "Foundation layer for agents over Postgres via materialized views"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "psycopg[binary]>=3.2",
    "sqlglot>=25.0",
    "pyyaml>=6.0",
    "pydantic>=2.7",
    "typer>=0.12",
    "rich>=13.7",
    "jinja2>=3.1",
    "graphviz>=0.20",
    "mcp[cli]>=1.2",
]

[project.scripts]
agent-mv = "agent_materialize.cli:app"
agent-materialize-setup-mcp = "agent_materialize.mcp_setup:main"
agent-materialize-runtime-mcp = "agent_materialize.mcp_runtime:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_materialize"]

[tool.hatch.build.targets.wheel.force-include]
"src/agent_materialize/skills" = "agent_materialize/skills"
"src/agent_materialize/templates" = "agent_materialize/templates"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "testcontainers[postgres]>=4.7",
    "ruff>=0.5",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
dist/
build/
*.egg-info/
.materialize-staging.yaml
dashboard.html
.env
```

- [ ] **Step 3: Create empty package files**

```bash
touch src/agent_materialize/__init__.py tests/__init__.py
echo '"""agent-materialize: foundation layer for agents over Postgres."""' > src/agent_materialize/__init__.py
echo '__version__ = "0.1.0"' >> src/agent_materialize/__init__.py
```

- [ ] **Step 4: Install with `uv` and verify**

Run: `uv sync && uv run python -c "import agent_materialize; print(agent_materialize.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/ tests/
git commit -m "chore: project scaffolding (pyproject.toml, package skeleton)"
```

### Task 0.2: Create README and example directory

**Files:**
- Create: `README.md`
- Create: `examples/starter-config/materialize.yaml`
- Create: `examples/starter-config/materialize/customer_rollup.sql`

- [ ] **Step 1: Write README.md**

```markdown
# agent-materialize

Foundation layer for agents over Postgres. Wraps a database in a small set of materialized views behind a two-role access boundary, so agents can query and refresh data without ever touching base tables.

## Quickstart

```bash
uv add agent-materialize
agent-mv init
# configure .env with DATABASE_URL (full role) and AGENT_MV_RUNTIME_URL (view-only role)
agent-mv apply
agent-mv doctor  # verifies the access boundary
```

See `examples/starter-config/` for a sample config.
```

- [ ] **Step 2: Write the starter-config example**

`examples/starter-config/materialize.yaml`:

```yaml
version: 1
target_schema: agent_mv
views:
  - name: customer_rollup
    sql_file: materialize/customer_rollup.sql
    indexes:
      - columns: [customer_id]
        unique: true
    description: "One row per customer with lifetime value + activity."
    sources: []  # filled by `agent-mv apply`
```

`examples/starter-config/materialize/customer_rollup.sql`:

```sql
SELECT
    u.id AS customer_id,
    COUNT(o.id) AS order_count,
    COALESCE(SUM(p.amount), 0) AS lifetime_value,
    MAX(o.created_at) AS last_order_at
FROM public.users u
LEFT JOIN public.orders o ON o.user_id = u.id
LEFT JOIN public.payments p ON p.order_id = o.id
GROUP BY u.id;
```

- [ ] **Step 3: Commit**

```bash
git add README.md examples/
git commit -m "docs: add README and starter-config example"
```

---

## Phase 1 — Config loading and validation

### Task 1.1: Config schema with Pydantic

**Files:**
- Create: `src/agent_materialize/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_materialize.config'`

- [ ] **Step 3: Implement `config.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/config.py tests/test_config.py
git commit -m "feat(config): pydantic schema + loader for materialize.yaml"
```

### Task 1.2: `write_sources` round-trip test

**Files:**
- Modify: `tests/test_config.py` (append test)

- [ ] **Step 1: Append the failing test**

```python
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
```

- [ ] **Step 2: Run the test (already passes — `write_sources` already implemented)**

Run: `uv run pytest tests/test_config.py::test_write_sources_updates_yaml_in_place -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py
git commit -m "test(config): write_sources round-trip"
```

---

## Phase 2 — Lineage parsing

### Task 2.1: Extract base-table sources from a single SELECT

**Files:**
- Create: `src/agent_materialize/lineage.py`
- Test: `tests/test_lineage.py`

- [ ] **Step 1: Write the failing test**

`tests/test_lineage.py`:

```python
from agent_materialize.lineage import parse_sources


def test_simple_select():
    sql = "SELECT id FROM public.users"
    assert parse_sources(sql, target_schema="agent_mv") == ({"public.users"}, set())


def test_join():
    sql = """
        SELECT u.id, o.id
        FROM public.users u
        JOIN public.orders o ON o.user_id = u.id
    """
    assert parse_sources(sql, target_schema="agent_mv") == (
        {"public.users", "public.orders"},
        set(),
    )


def test_unqualified_uses_search_path_default_public():
    sql = "SELECT id FROM users"
    assert parse_sources(sql, target_schema="agent_mv") == ({"public.users"}, set())


def test_mv_to_mv_dependency_classified_separately():
    sql = "SELECT * FROM agent_mv.customer_rollup"
    assert parse_sources(sql, target_schema="agent_mv") == (set(), {"customer_rollup"})


def test_cte_does_not_appear_as_source():
    sql = """
        WITH active AS (SELECT id FROM public.users WHERE active = true)
        SELECT * FROM active a JOIN public.orders o ON o.user_id = a.id
    """
    sources, mvs = parse_sources(sql, target_schema="agent_mv")
    assert sources == {"public.users", "public.orders"}
    assert mvs == set()


def test_subquery_extracts_inner_table():
    sql = """
        SELECT * FROM (SELECT id FROM public.users) u
        JOIN public.orders o ON o.user_id = u.id
    """
    assert parse_sources(sql, target_schema="agent_mv") == (
        {"public.users", "public.orders"},
        set(),
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lineage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `lineage.py`**

```python
from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp


def parse_sources(sql: str, target_schema: str) -> tuple[set[str], set[str]]:
    """Return (base_tables, mv_dependencies) for the given SQL.

    base_tables: schema-qualified names of tables NOT in target_schema (e.g. "public.users")
    mv_dependencies: bare names of tables IN target_schema (e.g. "customer_rollup")
    CTEs are excluded.
    """
    tree = sqlglot.parse_one(sql, read="postgres")

    cte_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        cte_names.add(cte.alias_or_name.lower())

    base: set[str] = set()
    mvs: set[str] = set()
    for tbl in tree.find_all(exp.Table):
        name = tbl.name.lower()
        if name in cte_names:
            continue
        schema = (tbl.db or "public").lower()
        if schema == target_schema.lower():
            mvs.add(name)
        else:
            base.add(f"{schema}.{name}")
    return base, mvs


def topological_order(views: dict[str, set[str]]) -> list[str]:
    """Sort view names so each view appears after the MVs it depends on.

    `views` maps view name → set of MV dependencies (other view names).
    Raises ValueError on cycles.
    """
    in_degree = {name: 0 for name in views}
    for name, deps in views.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    queue.sort()
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for other, deps in views.items():
            if n in deps and other in in_degree and in_degree[other] > 0:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)
                    queue.sort()
    if len(order) != len(views):
        raise ValueError("cycle detected in MV dependency graph")
    return order
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lineage.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/lineage.py tests/test_lineage.py
git commit -m "feat(lineage): sqlglot-based source extraction (table + MV deps)"
```

### Task 2.2: Topological sort tests

**Files:**
- Modify: `tests/test_lineage.py`

- [ ] **Step 1: Append tests**

```python
import pytest

from agent_materialize.lineage import topological_order


def test_topo_no_deps():
    assert topological_order({"a": set(), "b": set()}) == ["a", "b"]


def test_topo_chain():
    assert topological_order({"c": {"b"}, "b": {"a"}, "a": set()}) == ["a", "b", "c"]


def test_topo_diamond():
    order = topological_order({
        "a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"},
    })
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_topo_cycle_raises():
    with pytest.raises(ValueError, match="cycle"):
        topological_order({"a": {"b"}, "b": {"a"}})
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_lineage.py -v`
Expected: 10 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_lineage.py
git commit -m "test(lineage): topological order"
```

---

## Phase 3 — Database layer

### Task 3.1: Postgres test fixture

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the fixture**

```python
"""Test fixtures: ephemeral Postgres via testcontainers."""
from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
def superuser_url(pg_container: PostgresContainer) -> str:
    return pg_container.get_connection_url(driver=None)


@pytest.fixture
def fresh_db(pg_container: PostgresContainer) -> Iterator[str]:
    """Provide a fresh database name per test, dropped on exit."""
    import uuid

    db_name = f"t_{uuid.uuid4().hex[:8]}"
    base_url = pg_container.get_connection_url(driver=None)
    # Connect to default DB to CREATE the new one
    with psycopg.connect(base_url, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {db_name}")
    test_url = base_url.rsplit("/", 1)[0] + f"/{db_name}"
    try:
        yield test_url
    finally:
        with psycopg.connect(base_url, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {db_name} WITH (FORCE)")


@pytest.fixture
def with_base_tables(fresh_db: str) -> str:
    """A fresh DB with `public.users` and `public.orders` populated."""
    with psycopg.connect(fresh_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE public.users (id int PRIMARY KEY, name text)")
        conn.execute("CREATE TABLE public.orders (id int PRIMARY KEY, user_id int, amount numeric)")
        conn.execute("INSERT INTO public.users VALUES (1, 'a'), (2, 'b')")
        conn.execute("INSERT INTO public.orders VALUES (10, 1, 5), (11, 1, 7), (12, 2, 3)")
    return fresh_db
```

- [ ] **Step 2: Verify Docker / testcontainers works with a smoke test**

Add to `tests/test_conftest_smoke.py`:

```python
import psycopg


def test_fresh_db(fresh_db):
    with psycopg.connect(fresh_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)


def test_with_base_tables(with_base_tables):
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.users")
            assert cur.fetchone() == (2,)
```

- [ ] **Step 3: Run smoke tests**

Run: `uv run pytest tests/test_conftest_smoke.py -v`
Expected: 2 passed (Docker required; if Docker not running, this will fail with a clear message — start Docker Desktop)

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_conftest_smoke.py
git commit -m "test: testcontainers Postgres fixture"
```

### Task 3.2: DB connection helpers

**Files:**
- Create: `src/agent_materialize/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
import psycopg

from agent_materialize.db import connect_setup, connect_runtime, ensure_role


def test_ensure_role_creates_role(fresh_db):
    ensure_role(fresh_db, role="agent_mv_test", password="pw")
    with psycopg.connect(fresh_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'agent_mv_test'")
            assert cur.fetchone() == (1,)


def test_ensure_role_idempotent(fresh_db):
    ensure_role(fresh_db, role="agent_mv_test", password="pw")
    ensure_role(fresh_db, role="agent_mv_test", password="pw")  # second call must not error


def test_connect_setup_uses_dsn(fresh_db):
    with connect_setup(fresh_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            user = cur.fetchone()[0]
            assert user  # whatever the DSN role is


def test_connect_runtime_with_explicit_role(fresh_db):
    ensure_role(fresh_db, role="rt_user", password="rt_pw")
    runtime_url = fresh_db.replace("postgres:test", "rt_user:rt_pw").replace(
        "test:test", "rt_user:rt_pw"
    )
    # If the conftest URL doesn't match, build one explicitly:
    parts = fresh_db.split("@")
    runtime_url = f"postgresql://rt_user:rt_pw@{parts[1]}"
    with connect_runtime(runtime_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == "rt_user"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `db.py`**

```python
from __future__ import annotations

import contextlib
from collections.abc import Iterator

import psycopg


@contextlib.contextmanager
def connect_setup(dsn: str) -> Iterator[psycopg.Connection]:
    """Connect with the setup-time role (DSN points to agent_mv_setup or superuser)."""
    with psycopg.connect(dsn) as conn:
        yield conn


@contextlib.contextmanager
def connect_runtime(dsn: str) -> Iterator[psycopg.Connection]:
    """Connect with the runtime view-only role."""
    with psycopg.connect(dsn) as conn:
        yield conn


def ensure_role(admin_dsn: str, role: str, password: str) -> None:
    """Create the role if it does not exist. Idempotent."""
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                # role names cannot be parameterized; whitelist via simple regex
                if not role.replace("_", "").isalnum():
                    raise ValueError(f"unsafe role name: {role}")
                cur.execute(
                    f"CREATE ROLE {role} LOGIN PASSWORD %s",
                    (password,),
                )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/db.py tests/test_db.py
git commit -m "feat(db): connection helpers + ensure_role (idempotent)"
```

### Task 3.3: Schema bootstrap (agent_mv schema, lineage + history tables, refresh function)

**Files:**
- Create: `src/agent_materialize/schema.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing test**

```python
import psycopg

from agent_materialize.db import ensure_role
from agent_materialize.schema import bootstrap_schema


def _runtime_url(fresh_db: str, role: str, pw: str) -> str:
    after_at = fresh_db.split("@")[1]
    return f"postgresql://{role}:{pw}@{after_at}"


def test_bootstrap_creates_schema_and_tables(fresh_db):
    bootstrap_schema(fresh_db, target_schema="agent_mv", runtime_role="rt", runtime_password="rt")
    with psycopg.connect(fresh_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'agent_mv'")
            assert cur.fetchone() == (1,)
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='agent_mv' AND table_name='lineage'"
            )
            assert cur.fetchone() == (1,)
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='agent_mv' AND table_name='refresh_history'"
            )
            assert cur.fetchone() == (1,)
            cur.execute(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname='agent_mv' AND p.proname='refresh_view'"
            )
            assert cur.fetchone() == (1,)


def test_bootstrap_grants_runtime_select_on_schema(fresh_db):
    bootstrap_schema(fresh_db, target_schema="agent_mv", runtime_role="rt", runtime_password="rt")
    runtime = _runtime_url(fresh_db, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM agent_mv.refresh_history")
            assert cur.fetchone() == (0,)


def test_runtime_role_cannot_select_base_tables(with_base_tables):
    bootstrap_schema(
        with_base_tables, target_schema="agent_mv", runtime_role="rt", runtime_password="rt"
    )
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT * FROM public.users")
            except psycopg.errors.InsufficientPrivilege:
                return
            raise AssertionError("runtime role MUST NOT be able to read public.users")


def test_bootstrap_idempotent(fresh_db):
    bootstrap_schema(fresh_db, target_schema="agent_mv", runtime_role="rt", runtime_password="rt")
    bootstrap_schema(fresh_db, target_schema="agent_mv", runtime_role="rt", runtime_password="rt")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `schema.py`**

```python
from __future__ import annotations

import psycopg

from agent_materialize.db import ensure_role


def bootstrap_schema(
    admin_dsn: str,
    *,
    target_schema: str,
    runtime_role: str,
    runtime_password: str,
) -> None:
    """Create the target schema, lineage/history tables, refresh function, runtime role.

    Idempotent. Safe to run on every `apply`.
    """
    if not target_schema.replace("_", "").isalnum():
        raise ValueError(f"unsafe target_schema: {target_schema}")
    if not runtime_role.replace("_", "").isalnum():
        raise ValueError(f"unsafe runtime_role: {runtime_role}")

    ensure_role(admin_dsn, role=runtime_role, password=runtime_password)

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {target_schema}")

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {target_schema}.lineage (
                    view_name text NOT NULL,
                    source_kind text NOT NULL CHECK (source_kind IN ('table', 'view')),
                    source_name text NOT NULL,
                    PRIMARY KEY (view_name, source_kind, source_name)
                )
                """
            )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {target_schema}.refresh_history (
                    id bigserial PRIMARY KEY,
                    view_name text NOT NULL,
                    started_at timestamptz NOT NULL DEFAULT now(),
                    finished_at timestamptz,
                    status text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
                    error text,
                    rows_after bigint,
                    mode text CHECK (mode IN ('concurrent', 'blocking'))
                )
                """
            )

            cur.execute(
                f"""
                CREATE OR REPLACE FUNCTION {target_schema}.refresh_view(p_name text)
                RETURNS jsonb
                LANGUAGE plpgsql
                SECURITY DEFINER
                SET search_path = pg_catalog, public
                AS $$
                DECLARE
                    v_started timestamptz := clock_timestamp();
                    v_finished timestamptz;
                    v_rows bigint;
                    v_mode text;
                    v_has_unique_idx boolean;
                    v_history_id bigint;
                BEGIN
                    -- Validate name appears in lineage (acts as allowlist)
                    IF NOT EXISTS (
                        SELECT 1 FROM {target_schema}.lineage WHERE view_name = p_name
                    ) THEN
                        RAISE EXCEPTION 'unknown materialized view: %', p_name;
                    END IF;

                    -- Check for unique index on the MV
                    SELECT EXISTS (
                        SELECT 1 FROM pg_index i
                        JOIN pg_class c ON c.oid = i.indrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = '{target_schema}'
                          AND c.relname = p_name
                          AND i.indisunique
                    ) INTO v_has_unique_idx;

                    v_mode := CASE WHEN v_has_unique_idx THEN 'concurrent' ELSE 'blocking' END;

                    INSERT INTO {target_schema}.refresh_history (view_name, status, mode)
                    VALUES (p_name, 'running', v_mode)
                    RETURNING id INTO v_history_id;

                    BEGIN
                        IF v_has_unique_idx THEN
                            EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %I.%I',
                                           '{target_schema}', p_name);
                        ELSE
                            EXECUTE format('REFRESH MATERIALIZED VIEW %I.%I',
                                           '{target_schema}', p_name);
                        END IF;

                        EXECUTE format('SELECT count(*) FROM %I.%I', '{target_schema}', p_name)
                            INTO v_rows;

                        v_finished := clock_timestamp();
                        UPDATE {target_schema}.refresh_history
                           SET finished_at = v_finished,
                               status = 'success',
                               rows_after = v_rows
                         WHERE id = v_history_id;

                        RETURN jsonb_build_object(
                            'started_at', v_started,
                            'finished_at', v_finished,
                            'duration_ms', extract(epoch from (v_finished - v_started)) * 1000,
                            'rows_after', v_rows,
                            'mode', v_mode
                        );
                    EXCEPTION WHEN OTHERS THEN
                        UPDATE {target_schema}.refresh_history
                           SET finished_at = clock_timestamp(),
                               status = 'failed',
                               error = SQLERRM
                         WHERE id = v_history_id;
                        RAISE;
                    END;
                END;
                $$;
                """
            )

            # Grants for runtime role
            cur.execute(f"GRANT USAGE ON SCHEMA {target_schema} TO {runtime_role}")
            cur.execute(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {target_schema} TO {runtime_role}"
            )
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target_schema} "
                f"GRANT SELECT ON TABLES TO {runtime_role}"
            )
            cur.execute(
                f"GRANT EXECUTE ON FUNCTION {target_schema}.refresh_view(text) TO {runtime_role}"
            )

            # Explicitly REVOKE everything else from PUBLIC on the schema (defense in depth)
            cur.execute(f"REVOKE ALL ON SCHEMA {target_schema} FROM PUBLIC")
            cur.execute(f"GRANT USAGE ON SCHEMA {target_schema} TO {runtime_role}")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_schema.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/schema.py tests/test_schema.py
git commit -m "feat(schema): bootstrap agent_mv schema + SECURITY DEFINER refresh fn"
```

---

## Phase 4 — `apply` core

### Task 4.1: Create / replace materialized views from config

**Files:**
- Create: `src/agent_materialize/apply.py`
- Test: `tests/test_apply.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

import psycopg

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def _write_simple_config(tmp_path: Path) -> Path:
    sql_dir = tmp_path / "materialize"
    sql_dir.mkdir()
    (sql_dir / "user_count.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    yaml_path = tmp_path / "materialize.yaml"
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views:
          - name: user_count
            sql_file: materialize/user_count.sql
            description: "How many users"
        """
    )
    return yaml_path


def test_apply_creates_view(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(
        cfg,
        config_path=yaml_path,
        admin_dsn=with_base_tables,
        runtime_role="rt",
        runtime_password="rt",
        confirm_drops=lambda names: True,  # auto-confirm; no drops here anyway
    )
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.user_count")
            assert cur.fetchone() == (2,)


def test_apply_idempotent(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.user_count")
            assert cur.fetchone() == (2,)


def test_apply_writes_lineage_into_yaml_and_table(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    cfg2 = load_config(yaml_path)
    assert cfg2.views[0].sources == ["public.users"]

    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_kind, source_name FROM agent_mv.lineage WHERE view_name='user_count'"
            )
            assert cur.fetchall() == [("table", "public.users")]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `apply.py`**

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg

from agent_materialize.config import Config, write_sources
from agent_materialize.lineage import parse_sources, topological_order
from agent_materialize.schema import bootstrap_schema


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name}")
    return name


def apply_config(
    cfg: Config,
    *,
    config_path: Path,
    admin_dsn: str,
    runtime_role: str,
    runtime_password: str,
    confirm_drops: Callable[[list[str]], bool],
) -> None:
    """Apply `cfg` to the database. Idempotent.

    `confirm_drops` is called with the list of view names that the YAML no longer mentions.
    Returning True allows the drops; False aborts without dropping.
    """
    bootstrap_schema(
        admin_dsn,
        target_schema=cfg.target_schema,
        runtime_role=runtime_role,
        runtime_password=runtime_password,
    )
    schema = _ident(cfg.target_schema)

    # 1. compute lineage and sort
    deps: dict[str, set[str]] = {}
    sources_per_view: dict[str, list[str]] = {}
    for v in cfg.views:
        base, mvs = parse_sources(v.sql, target_schema=cfg.target_schema)
        deps[v.name] = mvs
        sources_per_view[v.name] = sorted(base | {f"{cfg.target_schema}.{m}" for m in mvs})
    order = topological_order(deps)

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 2. find existing MVs in the schema
            cur.execute(
                "SELECT matviewname FROM pg_matviews WHERE schemaname = %s",
                (cfg.target_schema,),
            )
            existing = {row[0] for row in cur.fetchall()}

            # 3. drop views that are no longer in config
            wanted = {v.name for v in cfg.views}
            removed = sorted(existing - wanted)
            if removed:
                if not confirm_drops(removed):
                    raise SystemExit("apply aborted: drops not confirmed")
                for name in removed:
                    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {schema}.{_ident(name)}")
                    cur.execute(
                        f"DELETE FROM {schema}.lineage WHERE view_name = %s", (name,)
                    )

            # 4. create or replace views in topological order
            views_by_name = {v.name: v for v in cfg.views}
            for name in order:
                v = views_by_name[name]
                if name in existing:
                    cur.execute(f"DROP MATERIALIZED VIEW {schema}.{_ident(name)}")
                cur.execute(
                    f"CREATE MATERIALIZED VIEW {schema}.{_ident(name)} AS {v.sql}"
                )
                # indexes
                for idx in v.indexes:
                    cols = ", ".join(_ident(c) for c in idx.columns)
                    unique = "UNIQUE " if idx.unique else ""
                    idx_name = f"{name}_{'_'.join(idx.columns)}_idx"
                    cur.execute(
                        f"CREATE {unique}INDEX IF NOT EXISTS {_ident(idx_name)} "
                        f"ON {schema}.{_ident(name)} ({cols})"
                    )
                # grant SELECT to runtime role (already covered by default privileges + GRANT ALL)
                cur.execute(
                    f"GRANT SELECT ON {schema}.{_ident(name)} TO {_ident(runtime_role)}"
                )

            # 5. write lineage into the table
            cur.execute(f"DELETE FROM {schema}.lineage")
            for name in order:
                base, mvs = parse_sources(views_by_name[name].sql, target_schema=cfg.target_schema)
                for src in base:
                    cur.execute(
                        f"INSERT INTO {schema}.lineage (view_name, source_kind, source_name) "
                        f"VALUES (%s, 'table', %s)",
                        (name, src),
                    )
                for src in mvs:
                    cur.execute(
                        f"INSERT INTO {schema}.lineage (view_name, source_kind, source_name) "
                        f"VALUES (%s, 'view', %s)",
                        (name, src),
                    )

            # 6. populate views (initial REFRESH for `WITH NO DATA` if used; CREATE MV without
            # WITH DATA already populates by default in Postgres, so this is a no-op safety net)

    # 7. write sources back to YAML (outside DB transaction)
    for name, srcs in sources_per_view.items():
        write_sources(config_path, name, srcs)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_apply.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/apply.py tests/test_apply.py
git commit -m "feat(apply): create/replace MVs, write lineage to YAML + table"
```

### Task 4.2: `apply` prompts on drop

**Files:**
- Modify: `tests/test_apply.py`

- [ ] **Step 1: Append the failing test**

```python
import pytest


def test_apply_prompts_before_drop_and_aborts_on_no(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg1 = load_config(yaml_path)
    apply_config(cfg1, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    # Now write a config without `user_count`
    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views: []
        """
    )
    cfg2 = load_config(yaml_path)

    prompted: list[list[str]] = []
    def deny(names):
        prompted.append(names)
        return False

    with pytest.raises(SystemExit, match="aborted"):
        apply_config(cfg2, config_path=yaml_path, admin_dsn=with_base_tables,
                     runtime_role="rt", runtime_password="rt",
                     confirm_drops=deny)

    assert prompted == [["user_count"]]

    # MV must still exist after abort
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='user_count'"
            )
            assert cur.fetchone() == (1,)


def test_apply_drops_when_confirmed(with_base_tables, tmp_path):
    yaml_path = _write_simple_config(tmp_path)
    cfg1 = load_config(yaml_path)
    apply_config(cfg1, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    yaml_path.write_text(
        """
        version: 1
        target_schema: agent_mv
        views: []
        """
    )
    cfg2 = load_config(yaml_path)
    apply_config(cfg2, config_path=yaml_path, admin_dsn=with_base_tables,
                 runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda names: True)

    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='user_count'"
            )
            assert cur.fetchone() is None
```

- [ ] **Step 2: Run tests (these should already pass — `apply` already has confirm_drops)**

Run: `uv run pytest tests/test_apply.py::test_apply_prompts_before_drop_and_aborts_on_no tests/test_apply.py::test_apply_drops_when_confirmed -v`
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_apply.py
git commit -m "test(apply): drop confirmation behavior"
```

### Task 4.3: `apply` cross-checks `pg_depend`

**Files:**
- Modify: `src/agent_materialize/apply.py`
- Test: `tests/test_apply.py` (append)

- [ ] **Step 1: Add a test asserting a warning is raised on pg_depend mismatch**

Append to `tests/test_apply.py`:

```python
import logging


def test_apply_logs_warning_when_pg_depend_disagrees(with_base_tables, tmp_path, caplog):
    # We can't easily fabricate a disagreement (sqlglot is correct on simple SQL).
    # Instead, assert the cross-check ran by checking for a known INFO log line.
    yaml_path = _write_simple_config(tmp_path)
    cfg = load_config(yaml_path)
    with caplog.at_level(logging.INFO, logger="agent_materialize.apply"):
        apply_config(cfg, config_path=yaml_path, admin_dsn=with_base_tables,
                     runtime_role="rt", runtime_password="rt",
                     confirm_drops=lambda names: True)
    assert any("pg_depend cross-check" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_apply.py::test_apply_logs_warning_when_pg_depend_disagrees -v`
Expected: FAIL — assertion (no log message yet)

- [ ] **Step 3: Add cross-check to `apply.py`**

After the `# 5. write lineage into the table` block in `apply_config`, add:

```python
            # 6. pg_depend cross-check (defensive — sqlglot is the YAML source of truth)
            import logging
            log = logging.getLogger(__name__)
            for name in order:
                cur.execute(
                    """
                    SELECT DISTINCT n.nspname || '.' || c.relname AS src
                    FROM pg_depend d
                    JOIN pg_rewrite r ON r.oid = d.objid
                    JOIN pg_class mv ON mv.oid = r.ev_class
                    JOIN pg_namespace mvn ON mvn.oid = mv.relnamespace
                    JOIN pg_class c ON c.oid = d.refobjid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE mvn.nspname = %s AND mv.relname = %s
                      AND c.relkind IN ('r', 'm')
                      AND NOT (n.nspname = mvn.nspname AND c.relname = mv.relname)
                    """,
                    (cfg.target_schema, name),
                )
                pg_sources = {row[0] for row in cur.fetchall()}
                sqlglot_sources = set(sources_per_view[name])
                log.info(
                    "pg_depend cross-check for %s: pg=%s sqlglot=%s",
                    name, sorted(pg_sources), sorted(sqlglot_sources),
                )
                if pg_sources != sqlglot_sources:
                    log.warning(
                        "lineage mismatch for view '%s': pg_depend=%s sqlglot=%s",
                        name, sorted(pg_sources), sorted(sqlglot_sources),
                    )
```

- [ ] **Step 4: Run all apply tests**

Run: `uv run pytest tests/test_apply.py -v`
Expected: all passed (5)

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/apply.py tests/test_apply.py
git commit -m "feat(apply): pg_depend cross-check warns on lineage mismatch"
```

---

## Phase 5 — CLI surface

### Task 5.1: `agent-mv init`

**Files:**
- Create: `src/agent_materialize/cli.py`
- Test: `tests/test_cli_init.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.cli import app


def test_init_creates_files(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["init"], cwd=str(tmp_path))
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "materialize.yaml").is_file()
    assert (tmp_path / ".env.example").is_file()
    assert (tmp_path / "materialize").is_dir()
    # skills symlinked into .claude/skills/agent-materialize/
    skills_dir = tmp_path / ".claude" / "skills" / "agent-materialize"
    assert skills_dir.is_dir()
    assert (skills_dir / "setup-database.md").is_symlink() or (skills_dir / "setup-database.md").is_file()


def test_init_does_not_clobber_existing_yaml(tmp_path: Path):
    (tmp_path / "materialize.yaml").write_text("version: 1\ntarget_schema: x\nviews: []\n")
    runner = CliRunner()
    result = runner.invoke(app, ["init"], cwd=str(tmp_path))
    assert result.exit_code != 0
    assert "already exists" in result.stdout.lower()
```

Note: Typer's CliRunner does not accept `cwd`. Use `runner.invoke(app, ["init"], catch_exceptions=False)` after `monkeypatch.chdir(tmp_path)`. Update test:

```python
def test_init_creates_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    ...
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `cli.py` with `init`**

```python
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
```

- [ ] **Step 4: Create skill stub files so init has something to symlink**

```bash
mkdir -p src/agent_materialize/skills
for s in setup-database querying-views adding-a-view troubleshoot-refresh; do
  echo "---" > src/agent_materialize/skills/$s.md
  echo "name: $s" >> src/agent_materialize/skills/$s.md
  echo "description: stub" >> src/agent_materialize/skills/$s.md
  echo "---" >> src/agent_materialize/skills/$s.md
  echo "Stub. Filled in Phase 11." >> src/agent_materialize/skills/$s.md
done
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/agent_materialize/cli.py src/agent_materialize/skills/ tests/test_cli_init.py
git commit -m "feat(cli): agent-mv init scaffolds project + symlinks skill stubs"
```

### Task 5.2: `agent-mv apply` and `agent-mv doctor`

**Files:**
- Modify: `src/agent_materialize/cli.py`
- Test: `tests/test_cli_apply.py`, `tests/test_cli_doctor.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_apply.py`:

```python
import os
from pathlib import Path

from typer.testing import CliRunner

from agent_materialize.cli import app


def _seed_config(tmp: Path):
    (tmp / "materialize").mkdir()
    (tmp / "materialize" / "user_count.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp / "materialize.yaml").write_text(
        "version: 1\n"
        "target_schema: agent_mv\n"
        "views:\n"
        "  - name: user_count\n"
        "    sql_file: materialize/user_count.sql\n"
        "    description: \"u\"\n"
    )


def test_apply_uses_env(monkeypatch, tmp_path, with_base_tables):
    _seed_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    monkeypatch.setenv("AGENT_MV_RUNTIME_PASSWORD", "rt")
    runner = CliRunner()
    result = runner.invoke(app, ["apply", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "✓" in result.stdout or "applied" in result.stdout.lower()
```

`tests/test_cli_doctor.py`:

```python
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
    # Create runtime role with too many privileges
    with psycopg.connect(with_base_tables, autocommit=True) as conn:
        conn.execute("CREATE ROLE rt LOGIN PASSWORD 'rt' SUPERUSER")
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", runtime)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0
    assert "access boundary" in result.stdout.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_apply.py tests/test_cli_doctor.py -v`
Expected: FAIL — `apply` and `doctor` commands missing

- [ ] **Step 3: Add commands to `cli.py`**

Append to `cli.py`:

```python
import os

import psycopg

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


@app.command()
def apply(
    yes: bool = typer.Option(False, "--yes", help="Auto-confirm drops"),
) -> None:
    """Diff materialize.yaml against the database; create/update/drop MVs."""
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
    typer.echo(f"✓ applied {len(cfg.views)} view(s)")


@app.command()
def doctor() -> None:
    """Verify roles, schema, and the access boundary."""
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

    # 2. runtime role can SELECT from agent_mv
    with psycopg.connect(runtime_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT count(*) FROM agent_mv.refresh_history")
            except psycopg.errors.InsufficientPrivilege:
                typer.echo("✗ runtime role cannot read agent_mv schema", err=True)
                raise typer.Exit(code=1)

    # 3. CRITICAL: runtime role MUST NOT be able to read base tables.
    #    We test by trying public.* — every Postgres has at least information_schema views.
    #    Find any user table in `public` to test against; if none, skip with a warning.
    with psycopg.connect(admin_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' LIMIT 1"
            )
            row = cur.fetchone()
    if row is None:
        typer.echo(
            "⚠ no tables in public schema; cannot verify access boundary. "
            "Create at least one base table and re-run.",
            err=True,
        )
        raise typer.Exit(code=1)
    sample_table = row[0]

    with psycopg.connect(runtime_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f"SELECT 1 FROM public.{sample_table} LIMIT 1")
                typer.echo(
                    f"✗ access boundary FAILED: runtime role can read public.{sample_table}",
                    err=True,
                )
                raise typer.Exit(code=1)
            except psycopg.errors.InsufficientPrivilege:
                pass  # expected

    typer.echo("✓ access boundary verified (runtime role cannot read base tables)")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_apply.py tests/test_cli_doctor.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/cli.py tests/test_cli_apply.py tests/test_cli_doctor.py
git commit -m "feat(cli): agent-mv apply + agent-mv doctor with access-boundary check"
```

### Task 5.3: `agent-mv status`, `refresh`, `refresh-all`, `drop`

**Files:**
- Modify: `src/agent_materialize/cli.py`
- Test: `tests/test_cli_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
import psycopg
from pathlib import Path
from typer.testing import CliRunner

from agent_materialize.cli import app
from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def _setup(tmp_path: Path, with_base_tables: str, monkeypatch):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", with_base_tables)
    after_at = with_base_tables.split("@")[1]
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")


def test_status(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "uc" in result.stdout


def test_refresh_one(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh", "uc"])
    assert result.exit_code == 0, result.stdout
    assert "refreshed" in result.stdout.lower()


def test_refresh_unknown_view_fails(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh", "nope"])
    assert result.exit_code != 0


def test_refresh_all_uses_topo_order(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["refresh-all"])
    assert result.exit_code == 0, result.stdout


def test_drop_removes_from_yaml_and_db(tmp_path, with_base_tables, monkeypatch):
    _setup(tmp_path, with_base_tables, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["drop", "uc", "--yes"])
    assert result.exit_code == 0, result.stdout
    cfg = load_config(tmp_path / "materialize.yaml")
    assert cfg.views == []
    with psycopg.connect(with_base_tables) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_matviews WHERE schemaname='agent_mv' AND matviewname='uc'"
            )
            assert cur.fetchone() is None
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_cli_runtime.py -v`
Expected: FAIL — commands missing

- [ ] **Step 3: Add commands to `cli.py`**

Append to `cli.py`:

```python
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
        confirm_drops=lambda names: True,  # already confirmed via the explicit drop command
    )
    typer.echo(f"✓ dropped {name}")
```

(Note: the test uses runtime role `rt` while `apply` uses `agent_mv_runtime`. The test's `_setup` runs `apply_config` directly with role `rt`. The CLI's `drop` will re-`apply` with `agent_mv_runtime`. To make the test pass deterministically, the test sets `DATABASE_URL` to the admin DSN; the runtime role to grant to in re-apply is `agent_mv_runtime` — that role doesn't exist yet but `apply_config` calls `bootstrap_schema` which calls `ensure_role` to create it. So the test passes.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_runtime.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/cli.py tests/test_cli_runtime.py
git commit -m "feat(cli): status, refresh, refresh-all, drop"
```

---

## Phase 6 — Setup MCP

### Task 6.1: Setup MCP — schema introspection tools

**Files:**
- Create: `src/agent_materialize/mcp_setup.py`
- Test: `tests/test_mcp_setup.py`

- [ ] **Step 1: Write the failing test**

```python
import os

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_setup.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `mcp_setup.py`** (function-level; FastMCP decorators added in next step)

```python
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import sqlglot
from sqlglot import expressions as exp


class SampleQueryError(ValueError):
    pass


def _admin_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for setup-mcp")
    return dsn


def list_schemas() -> list[str]:
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' "
                "ORDER BY schema_name"
            )
            return [r[0] for r in cur.fetchall()]


def list_tables(schema: str) -> list[str]:
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') "
                "ORDER BY table_name",
                (schema,),
            )
            return [r[0] for r in cur.fetchall()]


def describe_table(name: str) -> dict:
    schema, table = name.split(".", 1) if "." in name else ("public", name)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, table),
            )
            cols = [
                {"name": r[0], "type": r[1], "nullable": r[2] == "YES"}
                for r in cur.fetchall()
            ]
    return {"name": f"{schema}.{table}", "columns": cols}


def profile_table(name: str) -> dict:
    schema, table = name.split(".", 1) if "." in name else ("public", name)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            row_count = cur.fetchone()[0]
            # FK references
            cur.execute(
                """
                SELECT kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type='FOREIGN KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
                """,
                (schema, table),
            )
            fks = [
                {"column": r[0], "references": f"{r[1]}.{r[2]}.{r[3]}"}
                for r in cur.fetchall()
            ]
    return {"name": f"{schema}.{table}", "row_count": row_count, "foreign_keys": fks}


_FORBIDDEN_PREFIX_RE = re.compile(r"\bpg_[a-z_]+", re.IGNORECASE)


def sample_query(sql: str, limit: int = 1000) -> list[dict]:
    """Execute a read-only SELECT, capped at 1000 rows."""
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed or not isinstance(parsed[0], (exp.Select, exp.Subquery, exp.With)):
        raise SampleQueryError("only read-only SELECT statements are allowed")
    if len(parsed) != 1:
        raise SampleQueryError("only a single statement is allowed")
    for tbl in parsed[0].find_all(exp.Table):
        full = f"{tbl.db or ''}.{tbl.name}".lstrip(".")
        if _FORBIDDEN_PREFIX_RE.match(tbl.name) or full.startswith("pg_"):
            raise SampleQueryError(f"queries against pg_* are forbidden: {full}")
        if (tbl.db or "").lower() == "information_schema":
            raise SampleQueryError("queries against information_schema are forbidden")

    capped = min(limit, 1000)
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM ({sql}) _sub LIMIT {capped}")
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def read_repo_files(globs: list[str]) -> dict[str, str]:
    """Read files matching the globs (relative to CWD); return {path: contents}.

    Capped at 100 files and 200KB per file. Skips binary files.
    """
    out: dict[str, str] = {}
    cwd = Path.cwd()
    for pattern in globs:
        for path in cwd.glob(pattern):
            if not path.is_file():
                continue
            if len(out) >= 100:
                break
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            if len(content) > 200_000:
                content = content[:200_000] + "\n... [truncated]"
            out[str(path.relative_to(cwd))] = content
    return out


_STAGING_FILE = ".materialize-staging.yaml"


def propose_view(name: str, sql: str, rationale: str) -> dict:
    """Append a proposal to the staging YAML. Returns the staged proposal list."""
    import yaml as _yaml

    staging_path = Path.cwd() / _STAGING_FILE
    if staging_path.exists():
        raw = _yaml.safe_load(staging_path.read_text()) or {"proposals": []}
    else:
        raw = {"proposals": []}
    raw["proposals"].append({"name": name, "sql": sql, "rationale": rationale})
    staging_path.write_text(_yaml.safe_dump(raw, sort_keys=False))
    return raw


def finalize_config(approved_names: list[str]) -> dict:
    """Move approved staged proposals into materialize.yaml + materialize/<name>.sql."""
    import yaml as _yaml

    staging_path = Path.cwd() / _STAGING_FILE
    if not staging_path.exists():
        raise RuntimeError("no staging file; nothing to finalize")
    staging = _yaml.safe_load(staging_path.read_text()) or {"proposals": []}
    proposals = {p["name"]: p for p in staging["proposals"]}

    yaml_path = Path.cwd() / "materialize.yaml"
    raw = _yaml.safe_load(yaml_path.read_text())
    raw.setdefault("views", [])

    sql_dir = Path.cwd() / "materialize"
    sql_dir.mkdir(exist_ok=True)

    added: list[str] = []
    for name in approved_names:
        if name not in proposals:
            raise ValueError(f"unknown proposal: {name}")
        p = proposals[name]
        sql_path = sql_dir / f"{name}.sql"
        sql_path.write_text(p["sql"])
        raw["views"].append(
            {
                "name": name,
                "sql_file": f"materialize/{name}.sql",
                "description": p["rationale"][:200],
            }
        )
        added.append(name)

    yaml_path.write_text(_yaml.safe_dump(raw, sort_keys=False))
    staging_path.unlink()
    return {"added": added}


# ---- FastMCP wrapper ----
def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("agent-materialize-setup")
    server.tool()(list_schemas)
    server.tool()(list_tables)
    server.tool()(describe_table)
    server.tool()(profile_table)
    server.tool()(sample_query)
    server.tool()(read_repo_files)
    server.tool()(propose_view)
    server.tool()(finalize_config)
    server.run()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_setup.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/mcp_setup.py tests/test_mcp_setup.py
git commit -m "feat(mcp_setup): introspection + sample_query + staging tools"
```

### Task 6.2: Setup MCP — propose_view + finalize_config tests

**Files:**
- Modify: `tests/test_mcp_setup.py` (append)

- [ ] **Step 1: Add tests**

```python
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
    # v2 was not approved → was discarded along with the staging file
    assert not (tmp_path / ".materialize-staging.yaml").exists()


def test_read_repo_files_caps_size(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 300_000)
    out = read_repo_files(globs=["*.txt"])
    assert "big.txt" in out
    assert "[truncated]" in out["big.txt"]
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_mcp_setup.py -v`
Expected: 9 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_setup.py
git commit -m "test(mcp_setup): propose_view/finalize_config + read_repo_files cap"
```

---

## Phase 7 — Runtime MCP

### Task 7.1: Runtime MCP — list/describe/query/refresh/lineage

**Files:**
- Create: `src/agent_materialize/mcp_runtime.py`
- Test: `tests/test_mcp_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
import os
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mcp_runtime.py -v`
Expected: FAIL — module missing

- [ ] **Step 3: Implement `mcp_runtime.py`**

```python
from __future__ import annotations

import os
import re

import psycopg
import sqlglot
from sqlglot import expressions as exp


TARGET_SCHEMA = os.environ.get("AGENT_MV_TARGET_SCHEMA", "agent_mv")


class QueryError(ValueError):
    pass


def _runtime_dsn() -> str:
    dsn = os.environ.get("AGENT_MV_RUNTIME_URL")
    if not dsn:
        raise RuntimeError("AGENT_MV_RUNTIME_URL is required for runtime-mcp")
    return dsn


def list_views() -> list[dict]:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            # We rely on the lineage table for the canonical list of registered views.
            cur.execute(
                f"""
                SELECT view_name,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'table') AS sources,
                       array_agg(DISTINCT source_name ORDER BY source_name)
                         FILTER (WHERE source_kind = 'view') AS depends_on
                FROM {TARGET_SCHEMA}.lineage
                GROUP BY view_name
                ORDER BY view_name
                """
            )
            base_rows = cur.fetchall()

            results: list[dict] = []
            for view_name, sources, depends_on in base_rows:
                cur.execute(
                    f"SELECT count(*) FROM {TARGET_SCHEMA}.{view_name}"
                )
                rows = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT finished_at, status FROM {TARGET_SCHEMA}.refresh_history
                    WHERE view_name = %s ORDER BY started_at DESC LIMIT 1
                    """,
                    (view_name,),
                )
                hist = cur.fetchone()
                last_at = hist[0].isoformat() if hist and hist[0] else None
                last_status = hist[1] if hist else None
                results.append(
                    {
                        "name": view_name,
                        "row_count": rows,
                        "sources": list(sources or []),
                        "depends_on": list(depends_on or []),
                        "last_refreshed_at": last_at,
                        "last_status": last_status,
                    }
                )
            return results


def describe_view(name: str) -> dict:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (TARGET_SCHEMA, name),
            )
            cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
    if not cols:
        raise QueryError(f"unknown view: {name}")
    return {"name": name, "columns": cols}


def query_view(sql: str, limit: int = 1000) -> list[dict]:
    """Execute a SELECT against agent_mv only. Caps limit at 1000."""
    parsed = sqlglot.parse(sql, read="postgres")
    if not parsed or not isinstance(parsed[0], (exp.Select, exp.Subquery, exp.With)):
        raise QueryError("only SELECT statements are allowed")
    for tbl in parsed[0].find_all(exp.Table):
        schema = (tbl.db or "").lower()
        if schema and schema != TARGET_SCHEMA.lower():
            raise QueryError(
                f"queries may only reference the {TARGET_SCHEMA} schema; "
                f"found {tbl.db}.{tbl.name}"
            )
    capped = min(limit, 1000)
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f"SELECT * FROM ({sql}) _sub LIMIT {capped}")
            except psycopg.errors.InsufficientPrivilege as exc:
                raise QueryError(f"permission denied: {exc}") from exc
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def refresh_view(name: str) -> dict:
    with psycopg.connect(_runtime_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {TARGET_SCHEMA}.refresh_view(%s)", (name,))
            return cur.fetchone()[0]


def get_lineage(name: str) -> dict:
    with psycopg.connect(_runtime_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT source_kind, source_name FROM {TARGET_SCHEMA}.lineage
                WHERE view_name = %s
                """,
                (name,),
            )
            rows = cur.fetchall()
            sources = sorted(r[1] for r in rows if r[0] == "table")
            depends_on = sorted(r[1] for r in rows if r[0] == "view")
            cur.execute(
                f"""
                SELECT view_name FROM {TARGET_SCHEMA}.lineage
                WHERE source_kind = 'view' AND source_name = %s
                """,
                (name,),
            )
            depended_on_by = sorted(r[0] for r in cur.fetchall())
    return {
        "name": name,
        "sources": sources,
        "depends_on": depends_on,
        "depended_on_by": depended_on_by,
    }


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("agent-materialize-runtime")
    server.tool()(list_views)
    server.tool()(describe_view)
    server.tool()(query_view)
    server.tool()(refresh_view)
    server.tool()(get_lineage)
    server.run()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_runtime.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/mcp_runtime.py tests/test_mcp_runtime.py
git commit -m "feat(mcp_runtime): list/describe/query/refresh/get_lineage tools"
```

---

## Phase 8 — Security boundary integration test

### Task 8.1: End-to-end security assertion (CI blocker)

**Files:**
- Create: `tests/test_security_boundary.py`

This test exists *separately* from the per-component tests because its failure must scream the loudest in CI: it's the framework's core promise.

- [ ] **Step 1: Write the test**

```python
"""CI BLOCKER: the runtime role must NEVER read base tables.

If this test fails, the framework's core security promise is broken.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def _runtime_url(fresh_db: str, role: str, pw: str) -> str:
    after_at = fresh_db.split("@")[1]
    return f"postgresql://{role}:{pw}@{after_at}"


def _seed(tmp_path: Path, with_base_tables: str):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text(
        "SELECT count(*)::bigint AS n FROM public.users"
    )
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)


def test_runtime_role_cannot_select_base_tables(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT * FROM public.users")


def test_runtime_role_cannot_drop_views(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("DROP MATERIALIZED VIEW agent_mv.uc")


def test_runtime_role_cannot_refresh_directly(tmp_path, with_base_tables):
    """Refresh must go through the SECURITY DEFINER function. Direct REFRESH must fail."""
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("REFRESH MATERIALIZED VIEW agent_mv.uc")


def test_runtime_role_cannot_refresh_arbitrary_name_via_function(tmp_path, with_base_tables):
    """The SECURITY DEFINER function must validate against the lineage allowlist."""
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.Error, match="unknown"):
                cur.execute("SELECT agent_mv.refresh_view('public.users')")


def test_runtime_role_can_select_view(tmp_path, with_base_tables):
    _seed(tmp_path, with_base_tables)
    runtime = _runtime_url(with_base_tables, "rt", "rt")
    with psycopg.connect(runtime) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n FROM agent_mv.uc")
            assert cur.fetchone() == (2,)
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_security_boundary.py -v`
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_security_boundary.py
git commit -m "test(security): runtime role cannot read base tables, drop, or escalate refresh"
```

---

## Phase 9 — Dashboard

### Task 9.1: Dashboard HTML builder

**Files:**
- Create: `src/agent_materialize/dashboard.py`
- Create: `src/agent_materialize/templates/dashboard.html.j2`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
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
    # Lineage SVG inlined
    assert "<svg" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: FAIL — module missing

- [ ] **Step 3: Write the Jinja template**

`src/agent_materialize/templates/dashboard.html.j2`:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>agent-materialize — {{ target_schema }}</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; }
h1 { font-size: 1.4rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 0.9rem; }
th { background: #f4f4f4; }
.failed { color: #b00; font-weight: bold; }
.section { margin-top: 2rem; }
.muted { color: #888; font-size: 0.85rem; }
.graph { background: #fafafa; padding: 1rem; border: 1px solid #eee; }
</style>
</head>
<body>
<h1>agent-materialize</h1>
<div class="muted">target schema: <code>{{ target_schema }}</code> · generated {{ generated_at }} · {{ views|length }} view(s)</div>

<div class="section">
<h2>Status</h2>
<table>
<thead><tr>
<th>view</th><th>description</th><th>sources</th>
<th>last refreshed</th><th>rows</th><th>last status</th>
</tr></thead>
<tbody>
{% for v in views %}
<tr>
  <td><code>{{ v.name }}</code></td>
  <td>{{ v.description }}</td>
  <td><code>{{ v.sources|join(", ") }}</code></td>
  <td>{{ v.last_refreshed_at or "—" }}</td>
  <td>{{ v.row_count }}</td>
  <td class="{{ 'failed' if v.last_status == 'failed' else '' }}">{{ v.last_status or "—" }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<div class="section">
<h2>Lineage</h2>
<div class="graph">{{ lineage_svg | safe }}</div>
</div>

<div class="section">
<h2>Recent refresh history (last 50)</h2>
<table>
<thead><tr>
<th>view</th><th>started</th><th>finished</th><th>status</th><th>mode</th><th>rows</th><th>error</th>
</tr></thead>
<tbody>
{% for h in history %}
<tr>
  <td><code>{{ h.view_name }}</code></td>
  <td>{{ h.started_at }}</td>
  <td>{{ h.finished_at or "—" }}</td>
  <td class="{{ 'failed' if h.status == 'failed' else '' }}">{{ h.status }}</td>
  <td>{{ h.mode or "—" }}</td>
  <td>{{ h.rows_after if h.rows_after is not none else "—" }}</td>
  <td>{{ h.error or "" }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</body>
</html>
```

- [ ] **Step 4: Implement `dashboard.py`**

```python
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
        g.node(f"t::{tbl}", label=tbl, shape="box", style="filled", fillcolor="#e8f0ff")
    for vn in seen_views:
        g.node(f"v::{vn}", label=vn, shape="component", style="filled", fillcolor="#fff0e8")

    for v in views_with_sources:
        for src in v["sources"]:
            g.edge(f"t::{src}", f"v::{v['name']}")
        for dep in v.get("depends_on", []):
            g.edge(f"v::{dep}", f"v::{v['name']}")

    svg_bytes = g.pipe(format="svg")
    svg = svg_bytes.decode("utf-8")
    # strip the <?xml declaration so it inlines cleanly into HTML
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
```

- [ ] **Step 5: Run test**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: 1 passed (system `graphviz` binary required — `brew install graphviz` if missing)

- [ ] **Step 6: Commit**

```bash
git add src/agent_materialize/dashboard.py src/agent_materialize/templates/ tests/test_dashboard.py
git commit -m "feat(dashboard): static HTML builder with inline graphviz SVG"
```

### Task 9.2: Wire `agent-mv dashboard build` into the CLI

**Files:**
- Modify: `src/agent_materialize/cli.py`
- Test: `tests/test_cli_dashboard.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from typer.testing import CliRunner

from agent_materialize.cli import app
from agent_materialize.apply import apply_config
from agent_materialize.config import load_config


def test_dashboard_build_command(tmp_path: Path, with_base_tables, monkeypatch):
    (tmp_path / "materialize").mkdir()
    (tmp_path / "materialize" / "uc.sql").write_text("SELECT 1::bigint AS n")
    (tmp_path / "materialize.yaml").write_text(
        "version: 1\ntarget_schema: agent_mv\nviews:\n"
        "  - name: uc\n    sql_file: materialize/uc.sql\n    description: u\n"
    )
    cfg = load_config(tmp_path / "materialize.yaml")
    apply_config(cfg, config_path=tmp_path / "materialize.yaml",
                 admin_dsn=with_base_tables, runtime_role="rt", runtime_password="rt",
                 confirm_drops=lambda n: True)
    after_at = with_base_tables.split("@")[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_MV_RUNTIME_URL", f"postgresql://rt:rt@{after_at}")

    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "build"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "dashboard.html").is_file()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_dashboard.py -v`
Expected: FAIL — `dashboard` subcommand missing

- [ ] **Step 3: Add `dashboard build` to `cli.py`**

Append to `cli.py`:

```python
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
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_cli_dashboard.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent_materialize/cli.py tests/test_cli_dashboard.py
git commit -m "feat(cli): agent-mv dashboard build"
```

---

## Phase 10 — Skills

### Task 10.1: Write the four shipped skills

**Files:**
- Modify: `src/agent_materialize/skills/setup-database.md`
- Modify: `src/agent_materialize/skills/querying-views.md`
- Modify: `src/agent_materialize/skills/adding-a-view.md`
- Modify: `src/agent_materialize/skills/troubleshoot-refresh.md`

- [ ] **Step 1: Write `setup-database.md`**

```markdown
---
name: setup-database
description: Use when the user wants to bootstrap a new agent-materialize project. Walks through schema discovery, view proposal, and config finalization.
---

# Setting up the materialized-view foundation layer

Use this skill **only** when the setup-mcp server is wired up. The runtime MCP cannot perform discovery.

## Workflow

1. **Confirm the user has run `agent-mv init`.** The directory must contain `materialize.yaml` (with `views: []`) and `.env.example`.
2. **Read the consuming codebase first.** Call `read_repo_files(globs=["**/*.py", "**/*.sql", "**/*.ts"])` (or whatever the repo uses) to understand what queries the agent / app actually runs. Without this you will propose generic views that don't match the user's actual workload.
3. **Explore the schema.**
   - `list_schemas()` — orient yourself
   - For each user schema, `list_tables(schema=...)`
   - For each promising table, `describe_table(name=...)` and `profile_table(name=...)` (row count, FKs)
4. **Hypothesize 3-5 candidate views.** Each candidate must satisfy:
   - Maps cleanly to a question the consuming code actually asks
   - Joins ≤ 4 tables (anything bigger is a smell — split it)
   - Has a natural unique key (so refresh can use `CONCURRENTLY`)
5. **For each candidate, present to the user:**
   - Name + one-sentence rationale
   - The SQL
   - **A sample query result** — call `sample_query(sql=..., limit=10)` and show the rows. **Never propose a view without showing sample data.**
6. **User picks/edits.** Call `propose_view(name, sql, rationale)` for each approved candidate.
7. **Finalize:** `finalize_config(approved_names=[...])` writes the YAML and the per-view SQL files. Tell the user to run `agent-mv apply`.

## Rules

- **Never propose a view without showing a sample.** Sample data catches "this column is always NULL" and "this join produces 0 rows" before they become a stale MV.
- **Always include a unique-key column when the underlying data has one.** It enables `REFRESH ... CONCURRENTLY`.
- **Don't propose more than 5 views in v1.** Iterate. Smaller is reviewable.
- **Don't query `pg_*` or `information_schema` via `sample_query` — use `list_tables` / `describe_table` instead.** sample_query rejects them.
```

- [ ] **Step 2: Write `querying-views.md`**

```markdown
---
name: querying-views
description: Use when about to query data through agent-materialize's runtime MCP. Covers staleness, describe-before-query, and error handling.
---

# Querying materialized views at runtime

Triggered when you're about to use `query_view`, `refresh_view`, `list_views`, `describe_view`, or `get_lineage`.

## Before querying

1. **Check `last_refreshed_at`** via `list_views()`. If the data behind the view changes throughout the day and `last_refreshed_at` is older than your task tolerates, call `refresh_view(name=...)` first.
2. **Don't refresh pre-emptively.** Refresh costs CPU and locks (when not `CONCURRENTLY`). Refresh only when you need fresher data than what's there.
3. **For complex SQL, call `describe_view(name=...)` first** — confirm column names and types before composing the query. The runtime tools won't show you the SQL body of the view, only its columns.

## Querying

- Use `query_view(sql=..., limit=N)`. The MCP enforces `limit ≤ 1000` regardless of what you pass.
- Queries that reference any schema other than `agent_mv` are rejected.
- On error, the response has `{error_type, message, hint}` — read `hint` first; it points at the most likely fix.

## Refresh semantics

- `refresh_view(name)` returns `{started_at, finished_at, duration_ms, rows_after, mode}`.
- `mode` is `"concurrent"` if a unique index exists, `"blocking"` otherwise. Blocking mode locks reads on the view for the duration of the refresh — flag this to the user if you see it.
- Refresh does NOT cascade. If the view depends on another MV (`get_lineage` → `depends_on`), refresh those first if they're stale.
```

- [ ] **Step 3: Write `adding-a-view.md`**

```markdown
---
name: adding-a-view
description: Use when the user wants to add a new materialized view to an existing agent-materialize project.
---

# Adding a new view

After initial setup, adding a view is a config-first workflow.

## Steps

1. **Write the SQL** in `materialize/<name>.sql`. Format: a plain `SELECT` (no `CREATE MATERIALIZED VIEW` wrapper — `apply` adds it).
2. **Add an entry to `materialize.yaml`:**

   ```yaml
   - name: <name>
     sql_file: materialize/<name>.sql
     description: "What this view answers in one sentence."
     indexes:
       - columns: [<unique_key_column>]
         unique: true
   ```

3. **Run `agent-mv apply`.** It parses the SQL with `sqlglot`, fills in `sources:`, creates the MV, creates the index, grants `SELECT` to the runtime role.
4. **Run `agent-mv refresh <name>`** once to confirm the refresh path works in `CONCURRENTLY` mode.

## Rules

- **Always declare a unique index** when the data has one. Without it, every refresh is a blocking refresh.
- **Don't reference base tables outside the user's intended set.** If `materialize.yaml` lives next to a `.env`, treat the schemas listed in the consuming code as the allowed set; reaching into `pg_*` or other databases will work at apply time but break the refresh allowlist later.
- **Don't write `CREATE MATERIALIZED VIEW` in the SQL file** — `apply` does that.
```

- [ ] **Step 4: Write `troubleshoot-refresh.md`**

```markdown
---
name: troubleshoot-refresh
description: Use when refresh_view or agent-mv refresh fails. Decision tree for common refresh failures.
---

# Troubleshooting refresh failures

Start by running `query_view(sql="SELECT * FROM agent_mv.refresh_history WHERE view_name = '<name>' ORDER BY started_at DESC LIMIT 5")` to see the most recent error.

## Decision tree

### "cannot refresh materialized view ... concurrently"

The view doesn't have a unique index. Two options:

- **Add one:** edit `materialize.yaml` to add `indexes: [{columns: [...], unique: true}]`, run `agent-mv apply`.
- **Accept blocking refresh:** the refresh function falls back to plain `REFRESH MATERIALIZED VIEW`, which works but locks reads for the duration. Acceptable for small views.

### "permission denied for table ..."

The view body references a base table that the **setup** role doesn't have access to. The runtime role doesn't matter here — refresh runs as definer (the setup role at the time the function was created). Grant `SELECT` to the setup role on the offending base table, then re-run `agent-mv apply` to recreate the function with the now-valid grants visible at definition time.

### "could not serialize access" / lock timeout

Another transaction holds a conflicting lock. Most common cause: two refreshes of the same view at the same time. Wait, retry. If chronic, look at the consuming code: someone is hammering refresh in a loop.

### "function ... does not exist"

The `agent_mv.refresh_view(text)` function got dropped or never created. Run `agent-mv apply` to recreate it.

### "unknown materialized view: <name>"

Either:
- the view name doesn't appear in `agent_mv.lineage` (apply was never run, or apply was aborted) — run `agent-mv apply`
- the caller mistyped the name — `list_views()` to see what's registered

## When in doubt

`agent-mv doctor` re-asserts the basic invariants (roles exist, schema exists, runtime can't read base tables). If `doctor` fails, fix that before investigating refresh.
```

- [ ] **Step 5: Sanity-check that all four skill files have valid frontmatter**

```bash
for f in src/agent_materialize/skills/*.md; do
  head -3 "$f" | grep -q "^name:" || echo "MISSING name in $f"
  head -3 "$f" | grep -q "^description:" || echo "MISSING description in $f"
done
```

Expected: no output (all good).

- [ ] **Step 6: Commit**

```bash
git add src/agent_materialize/skills/
git commit -m "feat(skills): full content for setup, querying, adding, troubleshooting"
```

---

## Phase 11 — End-to-end smoke test

### Task 11.1: Full workflow integration test

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: simulates a fresh project's full workflow.

init → write yaml + sql → apply → list_views via runtime → refresh → query → dashboard build.
"""
from pathlib import Path

import psycopg
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
        "       coalesce(sum(o.amount), 0) AS total "
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

    # 3. apply (using direct call, since CLI requires DATABASE_URL env)
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
```

- [ ] **Step 2: Run the e2e test**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: 1 passed

- [ ] **Step 3: Run the entire suite to make sure nothing regressed**

Run: `uv run pytest -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): full workflow init→apply→query→refresh→dashboard→doctor"
```

---

## Phase 12 — README polish + first version tag

### Task 12.1: Final README + tag v0.1.0

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README.md with the v0.1.0 version**

```markdown
# agent-materialize

A foundation layer for agents over Postgres. Wraps the database in a small set of materialized views behind a two-role access boundary, so agents query and refresh the views without ever touching base tables.

## What you get

- **Two MCP servers:** `setup-mcp` (privileged, used once during discovery) and `runtime-mcp` (view-only, used every day).
- **One CLI:** `agent-mv` for `init`, `apply`, `status`, `refresh`, `refresh-all`, `drop`, `dashboard build`, `doctor`.
- **Four skills:** `setup-database`, `querying-views`, `adding-a-view`, `troubleshoot-refresh`. Symlinked into `.claude/skills/` on `init`.
- **Static HTML dashboard:** rebuilt on demand, no server, inline SVG lineage graph.

## Quickstart

```bash
uv add agent-materialize
agent-mv init
# fill in .env using .env.example
# wire up setup-mcp in your MCP client (see docs)
# ask the agent to run the `setup-database` skill
agent-mv apply        # creates the schema, roles, views, lineage table, refresh function
agent-mv doctor       # verifies the access boundary
```

## Access boundary

After `agent-mv apply`:
- `agent_mv_setup` role: full DB access (used by `setup-mcp` and by `apply`)
- `agent_mv_runtime` role: `SELECT` on `agent_mv` schema, `EXECUTE` on `agent_mv.refresh_view(name text)`. Cannot read base tables.

`agent-mv doctor` asserts the boundary by trying to read a base table as the runtime role and asserting the query fails.

## Configuration

Single source of truth: `materialize.yaml` + `materialize/<view_name>.sql`. The `sources:` field on each view is owned by the lineage parser; humans don't write it. View bodies live in separate `.sql` files so SQL stays diffable.

## System dependencies

- Python ≥ 3.11
- `graphviz` (for `dashboard build`): `brew install graphviz` on macOS, `apt install graphviz` on Debian/Ubuntu
- Postgres ≥ 14 on the target side

## Development

```bash
uv sync
uv run pytest -v
```

Integration tests use `testcontainers` to spin up an ephemeral Postgres. Docker must be running.

## License

MIT.
```

- [ ] **Step 2: Run the full suite once more**

Run: `uv run pytest -v`
Expected: all passed

- [ ] **Step 3: Commit and tag**

```bash
git add README.md
git commit -m "docs: README for v0.1.0"
git tag v0.1.0
```

---

## Self-review

**Spec coverage check (against `docs/superpowers/specs/2026-04-25-agent-materialize-design.md`):**

| Spec section | Plan task |
|---|---|
| Two DB roles | 3.3 (`bootstrap_schema`), 5.2 (CLI `apply` wires it), 8.1 (security tests) |
| `agent_mv` schema + lineage + refresh_history tables | 3.3 |
| `SECURITY DEFINER refresh_view()` | 3.3, 8.1 |
| Config schema (`materialize.yaml` + per-view SQL files) | 1.1, 1.2 |
| sqlglot-based lineage parsing (table-level + MV deps + CTE handling) | 2.1 |
| Topological sort for refresh order | 2.2 |
| `agent-mv init` (scaffolds + symlinks skills) | 5.1 |
| `agent-mv apply` (idempotent, prompts on drops, writes lineage) | 4.1, 4.2 |
| `pg_depend` cross-check | 4.3 |
| `agent-mv doctor` (access-boundary verification) | 5.2 |
| `agent-mv status` (rich table) | 5.3 |
| `agent-mv refresh`, `refresh-all`, `drop` | 5.3 |
| Setup MCP tools (list_schemas, list_tables, describe_table, profile_table, sample_query w/ caps, read_repo_files, propose_view, finalize_config) | 6.1, 6.2 |
| `sample_query` rejects pg_* and writes; caps limit | 6.1 |
| Runtime MCP tools (list_views, describe_view, query_view w/ caps, refresh_view, get_lineage) | 7.1 |
| `query_view` rejects non-`agent_mv` schemas | 7.1 |
| Static HTML dashboard with inline graphviz SVG | 9.1 |
| `agent-mv dashboard build` | 9.2 |
| Four skills (setup-database, querying-views, adding-a-view, troubleshoot-refresh) | 10.1 |
| Skills symlinked from package on init | 5.1 |
| Security boundary CI-blocker tests | 8.1 |
| End-to-end smoke test | 11.1 |

All spec items are mapped to a task. No gaps.

**Placeholder scan:** No `TBD`, `TODO`, "implement later", "fill in details", or `Similar to Task N` references. All code blocks are concrete.

**Type/signature consistency check:**
- `parse_sources(sql, target_schema)` — same signature in `lineage.py` (Task 2.1), `apply.py` (4.1, 4.3), and `mcp_runtime.py` test setup (7.1).
- `apply_config(cfg, *, config_path, admin_dsn, runtime_role, runtime_password, confirm_drops)` — same signature in `apply.py` (4.1) and called identically from `cli.py` (5.2), `tests/conftest.py` setups, and e2e (11.1).
- `bootstrap_schema(admin_dsn, *, target_schema, runtime_role, runtime_password)` — same signature in `schema.py` (3.3), called from `apply.py` (4.1) and `doctor` test (5.2).
- `refresh_view(name)` — runtime MCP function (7.1) returns `{started_at, finished_at, duration_ms, rows_after, mode}`, matching the SQL function definition in 3.3.
- `confirm_drops: Callable[[list[str]], bool]` — consistent across 4.1, 4.2, and 5.2.
- Skill file names listed in `cli.py` (`SKILL_NAMES`, Task 5.1) match the four files written in Task 10.1.

No mismatches found.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-25-agent-materialize.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
