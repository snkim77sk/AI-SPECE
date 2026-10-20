"""Institution/department history filters must run before bounded paging."""
import re

import pytest
from sqlalchemy import event

import budget_pg_store
import budget_read_vnext
import budget_storage
import vnext_store


@pytest.fixture
def pg_history_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'history-pg.sqlite3'}"
    )
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(key, raising=False)
    budget_pg_store.reset_engine_cache()
    yield
    budget_pg_store.reset_engine_cache()


def _save(preserve, key, organization, department, day):
    preserve(
        "budget", key,
        {
            "fyr": "2026", "exe_ymd": day.replace("-", ""),
            "wa_laf_hg_nm": "충남", "laf_hg_nm": organization,
            "dept_nm": department, "dbiz_cd": key,
            "dbiz_nm": f"가로등 LED 교체 {key}",
            "bdg_cash_amt": "100", "ep_amt": "25",
        },
        source_system="REGRESSION_FIXTURE", source_operation="QWGJK",
        source_date=day,
    )


@pytest.mark.parametrize("region,institution,scope,expected_contains", [
    ("충남", "천안시", "", ()),
    ("인천광역시", "", "INCHEON_GENERAL_CONSTRUCTION", ("종합건설본부",)),
])
def test_history_passes_selected_scope_and_department_before_storage_limit(
    monkeypatch, region, institution, scope, expected_contains,
):
    captured = {}

    def read(dataset, **kwargs):
        captured.update(dataset=dataset, **kwargs)
        return []

    monkeypatch.setattr(budget_storage, "revision_project_rows", read)
    assert budget_read_vnext.qwgjk_history_rows(
        start_date="2026-01-01", end_date="2026-12-31", region=region,
        institution_name=institution, institution_scope=scope,
        department_name=" 도로과 ", limit=2, offset=1,
    ) == []
    assert captured["dataset"] == "budget"
    assert captured["organization_exact_names"] == (
        budget_read_vnext.institution_name_forms(region, institution)
        if institution else ()
    )
    assert captured["organization_contains_terms"] == expected_contains
    assert captured["department_exact_names"] == ("도로과",)
    # Keep the existing bounded history window for delta calculation; all
    # selected scope predicates now belong to the storage query before it.
    assert captured["limit"] == 5000
    assert captured["offset"] == 0


def test_storage_forwards_history_scope_to_postgres(monkeypatch):
    captured = {}
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(budget_storage, "require_storage", lambda: None)

    def read(dataset, **kwargs):
        captured.update(dataset=dataset, **kwargs)
        return []

    monkeypatch.setattr(budget_pg_store, "revision_project_rows", read)
    assert budget_storage.revision_project_rows(
        "budget", organization_exact_names=("충남천안시",),
        organization_contains_terms=("종합건설본부",),
        department_exact_names=("도로과",), limit=2, offset=1,
    ) == []
    assert captured["organization_exact_names"] == ("충남천안시",)
    assert captured["organization_contains_terms"] == ("종합건설본부",)
    assert captured["department_exact_names"] == ("도로과",)
    assert (captured["limit"], captured["offset"]) == (2, 1)


@pytest.mark.parametrize("backend", ["pg", "sqlite"])
@pytest.mark.parametrize("organization_filter", ["exact", "contains", "department_field"])
def test_history_scope_filters_precede_limit_and_offset(
    request, monkeypatch, backend, organization_filter,
):
    if backend == "pg":
        request.getfixturevalue("pg_history_adapter")
        preserve = budget_pg_store.preserve_observation
    else:
        preserve = vnext_store.preserve_raw

    # Two matching rows are older than both the wrong department and a
    # different institution. A small SQL limit reproduces the 5,000-row bug.
    _save(preserve, "target-old", "충남천안시", "도로과", "2026-01-01")
    _save(preserve, "target-new", "충남천안시", "도로과", "2026-01-02")
    _save(preserve, "wrong-department", "충남천안시", "공원과", "2026-02-01")
    _save(
        preserve, "wrong-institution", "충남공주시",
        "시설과" if organization_filter == "department_field" else "도로과",
        "2026-03-01",
    )

    if organization_filter == "exact":
        scope = {"organization_exact_names": ("충남천안시",)}
    elif organization_filter == "contains":
        scope = {"organization_contains_terms": ("천안시",)}
    else:
        # Institution-scope presets also consider department names.
        scope = {"organization_exact_names": ("도로과",)}

    observed = []
    bounded_timeouts = []
    if backend == "pg":
        engine, _tables = budget_pg_store._engine_and_tables()
        original_bound = budget_pg_store._bound_budget_view_query

        def bound(conn, timeout_ms):
            bounded_timeouts.append(timeout_ms)
            return original_bound(conn, timeout_ms)

        monkeypatch.setattr(budget_pg_store, "_bound_budget_view_query", bound)

        def capture(_conn, _cursor, statement, parameters, context, _executemany):
            assert not re.match(
                r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|TRUNCATE)\b",
                statement, re.IGNORECASE,
            ), statement
            observed.append((statement, context.compiled))

        event.listen(engine, "before_cursor_execute", capture)
    try:
        rows = budget_storage.revision_project_rows(
            "budget", start_date="2026-01-01", end_date="2026-12-31",
            region_terms=budget_read_vnext._region_search_terms("충남"),
            department_exact_names=("도로과",), limit=1, offset=1, **scope,
        )
    finally:
        if backend == "pg":
            event.remove(engine, "before_cursor_execute", capture)

    assert [row["record_key"] for row in rows] == ["target-old"]
    if backend == "pg":
        assert bounded_timeouts == [4000]
        bounded = [
            (statement, compiled) for statement, compiled in observed
            if "LIMIT" in statement.upper() and compiled is not None
        ]
        assert len(bounded) == 1
        statement, compiled = bounded[0]
        assert "WHERE" in statement.upper()
        assert compiled.statement._limit_clause.value == 1
        assert compiled.statement._offset_clause.value == 1


@pytest.mark.parametrize("backend", ["pg", "sqlite"])
@pytest.mark.parametrize("padding", [" ", "\t", "\r\n"])
def test_padded_stored_labels_match_options_current_totals_and_history(
    request, backend, padding,
):
    if backend == "pg":
        request.getfixturevalue("pg_history_adapter")
        preserve = budget_pg_store.preserve_observation
    else:
        preserve = vnext_store.preserve_raw

    raw_org = f"{padding}충남천안시{padding}"
    raw_department = f"{padding}도로과{padding}"
    _save(preserve, "padded-target", raw_org, raw_department, "2026-01-01")
    _save(preserve, "other-department", "충남천안시", "공원과", "2026-02-01")
    _save(preserve, "other-institution", "충남공주시", "도로과", "2026-03-01")

    names = budget_read_vnext.budget_institution_names(fiscal_year=2026, region="충남")
    assert "충남천안시" in names
    assert budget_read_vnext.institution_name_allowed("충남", "천안시", names)
    scope = dict(
        fiscal_year=2026, region="충남", institution_name="천안시",
        source_layers=("DETAIL_EXECUTION",),
    )
    assert set(budget_read_vnext.budget_department_names(**scope)) == {"도로과", "공원과"}

    filtered = dict(scope, department_name="도로과")
    rows = budget_read_vnext.screen_budget_rows(**filtered, limit=1)
    assert [row["record_key"] for row in rows] == ["padded-target"]
    summary = budget_read_vnext.screen_budget_summary(**filtered)
    assert summary["project_count"] == 1
    assert summary["budget_total"] == 100
    history = budget_read_vnext.qwgjk_history_rows(
        start_date="2026-01-01", end_date="2026-12-31", region="충남",
        institution_name="천안시", department_name="도로과", limit=1,
    )
    assert [row["record_key"] for row in history] == ["padded-target"]
    # Normalization is a read predicate; source labels retain their original text.
    assert rows[0]["org_name"] == history[0]["org_name"] == raw_org
    assert rows[0]["dept_name"] == history[0]["dept_name"] == raw_department
