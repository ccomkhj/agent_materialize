import pytest

from agent_materialize.lineage import parse_sources, topological_order


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
