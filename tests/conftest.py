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
