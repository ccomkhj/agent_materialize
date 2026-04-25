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
