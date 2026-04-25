from __future__ import annotations

import contextlib
from collections.abc import Iterator

import psycopg
from psycopg import sql


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
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )
