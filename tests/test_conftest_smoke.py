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
