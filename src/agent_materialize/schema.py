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
    if not target_schema or not (target_schema[0].isalpha() or target_schema[0] == "_") or not target_schema.replace("_", "").isalnum():
        raise ValueError(f"unsafe target_schema: {target_schema}")
    if not runtime_role or not (runtime_role[0].isalpha() or runtime_role[0] == "_") or not runtime_role.replace("_", "").isalnum():
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
                SET search_path = pg_catalog
                AS $$
                DECLARE
                    v_started timestamptz := clock_timestamp();
                    v_finished timestamptz;
                    v_rows bigint;
                    v_mode text;
                    v_has_unique_idx boolean;
                    v_history_id bigint;
                BEGIN
                    IF length(p_name) > 128 THEN
                        RAISE EXCEPTION 'view name too long';
                    END IF;

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

            # REVOKE FROM PUBLIC does not touch named-role grants; do it first to make the
            # schema private, then issue all named-role grants.
            cur.execute(f"REVOKE ALL ON SCHEMA {target_schema} FROM PUBLIC")
            cur.execute(f"GRANT USAGE ON SCHEMA {target_schema} TO {runtime_role}")
            cur.execute(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {target_schema} TO {runtime_role}"
            )
            # ALTER DEFAULT PRIVILEGES is grantor-scoped: future tables created by THIS
            # admin role will auto-grant SELECT to runtime. apply.py must use the same DSN.
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target_schema} "
                f"GRANT SELECT ON TABLES TO {runtime_role}"
            )
            cur.execute(
                f"GRANT EXECUTE ON FUNCTION {target_schema}.refresh_view(text) TO {runtime_role}"
            )
