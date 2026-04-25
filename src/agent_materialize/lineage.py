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
