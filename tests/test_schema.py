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
