"""Hermetic regression coverage for regional budget reads on the PG code path.

Fixtures use the explicit test-only SQLAlchemy SQLite adapter. No production
database, source collection, normalization repair, or source-network I/O occurs.
"""
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest
from sqlalchemy import event

import budget_excel_vnext
import budget_pg_store
import budget_read_vnext


ALL_LAYERS = ("DETAIL_EXECUTION", "APPROPRIATION", "EDUCATION")
REGION_CASES = (
    ("충청남도", "충남", "천안시"),
    ("충청북도", "충북", "청주시"),
    ("경상남도", "경남", "창원시"),
    ("경상북도", "경북", "포항시"),
)


@pytest.fixture
def pg_read_adapter(monkeypatch, tmp_path):
    """Exercise production SQL filters using a disposable local adapter."""
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'region-pg.sqlite3'}"
    )
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(key, raising=False)
    budget_pg_store.reset_engine_cache()
    yield
    budget_pg_store.reset_engine_cache()


def _detail(key, region, organization, *, amount=100, executed=25,
            department="도로과", day="2026-10-07", year=2026):
    return budget_pg_store.preserve_observation(
        "budget", key,
        {
            "fyr": str(year), "exe_ymd": day.replace("-", ""),
            "wa_laf_hg_nm": region, "laf_hg_nm": organization,
            "dept_nm": department, "dbiz_cd": key,
            "dbiz_nm": f"가로등 LED 교체 {key}",
            "bdg_cash_amt": str(amount), "ep_amt": str(executed),
        },
        source_system="REGRESSION_FIXTURE", source_operation="QWGJK",
        source_date=day,
    )


def _appropriation(key, region, organization, *, amount=400):
    budget_pg_store.preserve_observation(
        "budget_appropriation", key,
        {
            "fyr": "2026", "wa_laf_hg_nm": region,
            "laf_hg_nm": organization, "fld_nm": "교통및물류",
            "sect_nm": key, "biz_bdg_tott_amt": str(amount),
        },
        source_system="REGRESSION_FIXTURE", source_operation="AIDFA",
        source_date="2026-10-07",
    )


def _education(key, office, school, *, amount=500):
    budget_pg_store.preserve_observation(
        "education_budget", key,
        {
            "fiscalYear": "2026", "officeName": office,
            "schoolName": school, "departmentName": "시설과",
            "projectCode": key, "projectName": f"학교 LED 교체 {key}",
            "budget_amount": amount, "executed_amount": 0,
        },
        source_system="REGRESSION_FIXTURE", source_operation="EDUCATION",
        source_date="2026-10-07",
    )


def _keys(rows):
    return {row.get("raw_source_key") or row["record_key"] for row in rows}


def _scope(region, **kwargs):
    return dict(
        fiscal_year=2026, source_layers=ALL_LAYERS,
        region=region, **kwargs,
    )


@pytest.mark.parametrize("formal,short,city", REGION_CASES)
def test_short_and_formal_regions_keep_stored_and_compact_organizations(
    pg_read_adapter, formal, short, city,
):
    _detail("formal", formal, f"{formal} {city}", amount=100)
    _detail("short", short, f"{short}{city}", amount=200)
    _detail("org-fallback", "", f"{short}{city}", amount=300)
    _detail("other-region", "경기도", "경기도 수원시", amount=9000)
    _detail("other-year", short, f"{short}{city}", year=2025, amount=9000)

    assert short in budget_read_vnext._region_search_terms(formal)
    for selected in (formal, short):
        rows = budget_read_vnext.screen_budget_rows(**_scope(selected), limit=20)
        assert _keys(rows) == {"formal", "short", "org-fallback"}
        assert all(budget_read_vnext.region_matches(row, formal) for row in rows)
        summary = budget_read_vnext.screen_budget_summary(**_scope(selected))
        assert summary["project_count"] == 3
        assert summary["budget_total"] == 600
        assert summary["executed_total"] == 75
        assert summary["remaining_total"] == 525
        assert summary["source_io_performed"] is False
        names = budget_read_vnext.budget_institution_names(
            fiscal_year=2026, region=selected,
        )
        assert len(names) == 1
        assert budget_read_vnext.institution_name_allowed(formal, city, names)


@pytest.mark.parametrize("selected", ("천안시", "충남천안시", "충청남도 천안시"))
def test_institution_variants_match_rows_totals_departments_history_and_excel(
    pg_read_adapter, selected,
):
    _detail("cheonan-formal", "충청남도", "충청남도 천안시", amount=100,
            department="도로과")
    _detail("cheonan-short", "충남", "충남천안시", amount=200,
            department="조명과")
    _detail("cheonan-local", "충남", "천안시", amount=300,
            department="공원과")
    _detail("gongju", "충남", "충남공주시", amount=9000,
            department="다른부서")
    scope = _scope("충청남도", institution_name=selected)
    expected = {"cheonan-formal", "cheonan-short", "cheonan-local"}

    rows = budget_read_vnext.screen_budget_rows(**scope, limit=20)
    assert _keys(rows) == expected
    summary = budget_read_vnext.screen_budget_summary(**scope)
    assert summary["project_count"] == 3
    assert summary["budget_total"] == 600
    assert summary["remaining_total"] == 525
    assert set(budget_read_vnext.budget_department_names(**scope)) == {
        "도로과", "조명과", "공원과",
    }
    history = budget_read_vnext.qwgjk_history_rows(
        start_date="2026-01-01", end_date="2026-12-31",
        region="충청남도", institution_name=selected, limit=20,
    )
    assert _keys(history) == expected

    data = budget_excel_vnext.build_budget_xlsx(rows)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    exported = sheet.findall("m:sheetData/m:row", ns)[1:]
    assert len(exported) == 3
    projects = {
        row.find("m:c[@r='G%s']/m:is/m:t" % row.attrib["r"], ns).text
        for row in exported
    }
    assert projects == expected
    assert sum(
        int(row.find("m:c[@r='L%s']/m:v" % row.attrib["r"], ns).text)
        for row in exported
    ) == summary["budget_total"]


def test_institution_options_include_all_stored_layers_and_deduplicate_aliases(
    pg_read_adapter,
):
    _detail("cheonan-detail", "충남", "충남천안시")
    _detail("cheonan-formal", "충청남도", "충청남도 천안시")
    _appropriation("cheonan-aidfa", "충남", "천안시")
    _appropriation("gongju-only-aidfa", "충남", "충남공주시")
    _education("school-only-education", "충남교육청", "충남천안고등학교")
    _detail("other-region", "충북", "충북청주시")

    names = budget_read_vnext.budget_institution_names(
        fiscal_year=2026, region="충청남도",
    )
    assert budget_read_vnext.institution_name_allowed("충남", "공주시", names)
    assert "충남교육청" in names
    assert "충남천안고등학교" in names
    cheonan_forms = set(budget_read_vnext.institution_name_forms("충남", "천안시"))
    assert sum(
        bool(cheonan_forms.intersection(
            budget_read_vnext.institution_name_forms("충남", name)
        ))
        for name in names
    ) == 1
    assert not budget_read_vnext.institution_name_allowed("충남", "청주시", names)


def test_sql_region_precedence_matches_python_recognized_fallbacks(pg_read_adapter):
    _detail("region-wins", "충북", "충남천안시", amount=9000)
    _detail("org-fallback", "", "충남천안시", amount=100)
    _detail("unknown-region-fallback", "출처미확인", "충남천안시", amount=200)
    _education("institution-fallback", "", "충청남도천안고등학교", amount=300)
    _education("org-wins", "충북교육청", "충청남도천안고등학교", amount=9000)
    _detail("other-region-query-decoy", "경기도", "경기도 광주시", amount=9000,
            department="충남 지원부서")
    terms = budget_read_vnext._region_search_terms("충청남도")

    unfiltered = budget_pg_store.current_project_rows(
        fiscal_year=2026, source_layers=ALL_LAYERS, limit=100,
    )
    python_keys = _keys(budget_read_vnext._filter_region(unfiltered, "충남"))
    assert python_keys == {
        "org-fallback", "unknown-region-fallback", "institution-fallback",
    }
    sql_rows = budget_pg_store.current_project_rows(
        fiscal_year=2026, source_layers=ALL_LAYERS, region_terms=terms, limit=100,
    )
    assert _keys(sql_rows) == python_keys
    summary = budget_read_vnext.screen_budget_summary(**_scope("충남"))
    assert summary["project_count"] == 3
    assert summary["budget_total"] == 600
    names = budget_read_vnext.budget_institution_names(fiscal_year=2026, region="충남")
    assert "충북교육청" not in names
    assert "경기도 광주시" not in names
    departments = budget_read_vnext.budget_department_names(**_scope("충남"))
    assert set(departments) == {"도로과", "시설과"}


def test_gyeonggi_gwangju_city_is_not_inferred_as_gwangju_metropolitan(
    pg_read_adapter,
):
    _detail("gyeonggi-city-only", "", "광주시", amount=9000)
    _detail("gyeonggi-city-prefixed", "경기", "경기도 광주시", amount=9000)
    _detail("gwangju-metropolitan", "광주광역시", "광주광역시 북구", amount=100)
    _detail("gwangju-metropolitan-short", "광주", "광주 북구", amount=200)

    assert budget_read_vnext.canonical_region("광주시") == ""
    assert budget_read_vnext.canonical_region("경기도 광주시") == "경기도"
    for selected in ("광주", "광주광역시"):
        rows = budget_read_vnext.screen_budget_rows(**_scope(selected), limit=20)
        assert _keys(rows) == {"gwangju-metropolitan", "gwangju-metropolitan-short"}
        summary = budget_read_vnext.screen_budget_summary(**_scope(selected))
        assert summary["project_count"] == 2
        assert summary["budget_total"] == 300


def test_region_filter_precedes_paging_and_reads_only_select_existing_facts(
    pg_read_adapter,
):
    for index in range(8):
        _detail(f"target-{index}", "충남", "충남천안시", amount=100,
                day=f"2026-09-{index + 1:02d}")
    for index in range(12):
        _detail(f"newer-decoy-{index}", "충북", "충북청주시", amount=9000,
                day=f"2026-10-{index + 1:02d}")

    engine, _tables = budget_pg_store._engine_and_tables()
    observed = []

    def capture(_conn, _cursor, statement, parameters, context, _executemany):
        assert not re.match(
            r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|TRUNCATE)\b",
            statement, re.IGNORECASE,
        ), statement
        observed.append((statement, parameters, context.compiled))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        rows = budget_read_vnext.screen_budget_rows(
            **_scope("충청남도"), limit=2, offset=1,
        )
        summary = budget_read_vnext.screen_budget_summary(**_scope("충청남도"))
        budget_read_vnext.budget_institution_names(fiscal_year=2026, region="충남")
        budget_read_vnext.budget_department_names(**_scope("충남"))
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert _keys(rows) == {"target-6", "target-5"}
    assert summary["project_count"] == 8
    assert summary["budget_total"] == 800
    paged = [
        compiled.statement for statement, _parameters, compiled in observed
        if "LIMIT" in statement.upper() and compiled is not None
    ]
    assert paged
    assert any(
        stmt._limit_clause.value == 2 and stmt._offset_clause.value == 1
        for stmt in paged
        if stmt._limit_clause is not None and stmt._offset_clause is not None
    )


def test_top_level_institution_aliases_keep_all_projects_and_totals(pg_read_adapter):
    for index, name in enumerate(("충청남도", "충남", "충남도")):
        _detail(f"government-{index}", "충남", name, amount=100)
    for selected in ("충청남도", "충남", "충남도"):
        scope = _scope("충청남도", institution_name=selected)
        assert len(budget_read_vnext.screen_budget_rows(**scope)) == 3
        summary = budget_read_vnext.screen_budget_summary(**scope)
        assert summary["project_count"] == 3
        assert summary["budget_total"] == 300


def test_invalid_region_does_not_expand_summary_to_nationwide(pg_read_adapter):
    _detail("valid-chungnam", "충남", "천안시")
    assert budget_read_vnext.screen_budget_rows(**_scope("알수없는지역")) == []
    summary = budget_read_vnext.screen_budget_summary(**_scope("알수없는지역"))
    assert summary["project_count"] == 0
    assert summary["budget_total"] == 0
