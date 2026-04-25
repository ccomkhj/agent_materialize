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
    parts = fresh_db.split("@")
    runtime_url = f"postgresql://rt_user:rt_pw@{parts[1]}"
    with connect_runtime(runtime_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == "rt_user"
