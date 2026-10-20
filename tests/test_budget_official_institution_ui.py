"""Budget institution selectors distinguish official names from stored evidence."""
import importlib
import re
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

import budget_read_vnext
import budget_storage
import incheon_budget_scope_vnext


def _option(name, label, kind="LOCAL_GOVERNMENT", *, stored=False,
            layers=(), official=True, source_url="https://www.mois.go.kr/"):
    return {
        "name": name, "label": label, "kind": kind, "official": official,
        "source_url": source_url, "has_stored": stored, "source_layers": layers,
    }


def _render(monkeypatch, options, *, region="충청남도", selected="", query="",
            option_error=None):
    clean = importlib.import_module("vnext_clean_app")
    monkeypatch.setattr(clean, "TEST_MODE", True)
    monkeypatch.setattr(clean, "require_user", lambda _request: {"username": "audit"})
    monkeypatch.setattr(clean, "is_result_server", lambda: False)
    monkeypatch.setattr(clean, "layout", lambda _title, body, _section, _user: body)
    monkeypatch.setattr(budget_storage, "backend_name", lambda: "SQLITE")
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: False)
    monkeypatch.setattr(budget_storage, "storage_configured", lambda: True)
    calls = {"options": [], "summary": [], "rows": [], "departments": []}

    def choices(**kwargs):
        calls["options"].append(kwargs)
        if option_error is not None:
            raise option_error
        return options

    def forbidden_names(**_kwargs):
        raise AssertionError("UI_MUST_REUSE_SINGLE_RICH_OPTION_READ")

    def summary(**kwargs):
        calls["summary"].append(kwargs)
        return {"scope": "FULL_FILTERED_CURRENT", "project_count": 0}

    def rows(**kwargs):
        calls["rows"].append(kwargs)
        return []

    def departments(**kwargs):
        calls["departments"].append(kwargs)
        return []

    monkeypatch.setattr(budget_read_vnext, "budget_institution_options", choices)
    monkeypatch.setattr(budget_read_vnext, "budget_institution_names", forbidden_names)
    monkeypatch.setattr(budget_read_vnext, "budget_department_names", departments)
    monkeypatch.setattr(budget_read_vnext, "screen_budget_summary", summary)
    monkeypatch.setattr(budget_read_vnext, "screen_budget_rows", rows)
    params = {
        "year": "2026", "region": region, "institution_filter": selected,
        "budget_q": query,
    }
    request = Request({
        "type": "http", "method": "GET", "path": "/budget",
        "query_string": urlencode(params).encode("ascii"), "headers": [],
    })
    body = clean.budget_page(request)
    return body, calls


def _group(body, label):
    match = re.search(
        r'<optgroup label="' + re.escape(label) + r'">(.*?)</optgroup>',
        body, flags=re.S,
    )
    assert match is not None, label
    return match.group(1)


def test_budget_selector_groups_official_types_and_preserves_stored_legacy(monkeypatch):
    options = [
        _option("충청남도", "충청남도 본청", "PROVINCE"),
        _option("천안시", "충청남도 천안시"),
        _option("제주시", "제주특별자치도 제주시", "ADMINISTRATIVE_CITY"),
        _option("충청남도교육청", "충청남도교육청", "EDUCATION_OFFICE"),
        _option("충남천안고등학교", "충남천안고등학교", "STORED",
                stored=True, layers=("EDUCATION",), official=False),
        _option("기존 <기관>", "기존 <기관>", "LEGACY", official=False),
    ]
    body, calls = _render(monkeypatch, options)
    assert calls["options"] == [{"fiscal_year": 2026, "region": "충청남도"}]
    assert "충청남도 본청 (2026년 저장자료 없음)" in _group(body, "시·도 본청")
    assert "충청남도 천안시 (2026년 저장자료 없음)" in _group(body, "시·군·구")
    assert "제주특별자치도 제주시" in _group(body, "행정시")
    assert "충청남도교육청" in _group(body, "시·도 교육청")
    assert "충남천안고등학교" not in _group(body, "시·도 교육청")
    assert "충남천안고등학교" in _group(body, "저장 기관")
    assert "2026년 저장자료 없음" not in _group(body, "저장 기관")
    assert "기존 &lt;기관&gt;" in _group(body, "기존 기관")
    assert "2026년 저장자료 없음" not in _group(body, "기존 기관")
    assert [call["source_layers"] for call in calls["rows"]] == [
        ("DETAIL_EXECUTION", "EDUCATION"), ("APPROPRIATION",),
    ]


def test_official_unstored_selection_keeps_filter_and_explains_empty_result(monkeypatch):
    body, calls = _render(
        monkeypatch, [_option("천안시", "충청남도 천안시")],
        selected="org:충남천안시",
    )
    assert '<option value="org:천안시" selected>' in body
    assert 'data-server-institution="org:천안시"' in body
    assert body.count("2026년 저장 예산자료 없음 · 조회 결과 없음") == 3
    assert 'class="muted budget-institution-evidence"' in body
    assert 'href="https://www.mois.go.kr/"' in body
    assert all(call["institution_name"] == "천안시" for call in calls["rows"])
    assert calls["summary"][0]["institution_name"] == "천안시"
    assert calls["summary"][0]["source_layers"] == ("DETAIL_EXECUTION", "EDUCATION")
    assert "%EC%B2%9C%EC%95%88%EC%8B%9C" in body


def test_appropriation_only_institution_explains_missing_current_detail(monkeypatch):
    body, _calls = _render(
        monkeypatch, [_option("공주시", "충청남도 공주시", stored=True,
                              layers=("APPROPRIATION",))],
        selected="org:공주시",
    )
    assert "2026년 편성자료만 저장되어 있습니다." in body
    assert body.count("편성자료만 저장 · 현재 세부사업 조회 결과 없음") == 2
    assert "2026년 저장자료 없음" not in _group(body, "시·군·구")
    assert "2026년 저장 예산자료 없음" not in body


@pytest.mark.parametrize("layers", [("DETAIL_EXECUTION",), ("EDUCATION",),
                                    ("APPROPRIATION", "DETAIL_EXECUTION")])
def test_stored_institution_with_filtered_zero_keeps_condition_empty_message(
    monkeypatch, layers,
):
    body, calls = _render(
        monkeypatch, [_option("천안시", "충청남도 천안시", stored=True,
                              layers=layers)],
        selected="org:천안시", query="해당없음",
    )
    assert body.count("현재 조건의 세부사업 자료 없음") == 2
    assert 'class="muted budget-institution-evidence"' not in body
    assert "2026년 저장 예산자료 없음" not in body
    assert "편성자료만 저장되어 있습니다." not in body
    assert calls["summary"][0]["query"] == "해당없음"
    assert calls["rows"][0]["query"] == "해당없음"


def test_incheon_keeps_existing_scopes_and_appends_official_and_stored_orgs(monkeypatch):
    body, calls = _render(
        monkeypatch, [
            _option("제물포구", "인천광역시 제물포구"),
            _option("인천특수저장기관", "인천특수저장기관", "STORED",
                    stored=True, layers=("DETAIL_EXECUTION",), official=False),
        ],
        region="인천광역시", selected="scope:INCHEON_GENERAL_CONSTRUCTION",
    )
    for group in incheon_budget_scope_vnext.grouped_options():
        for option in group["options"]:
            if option["code"] != incheon_budget_scope_vnext.DEFAULT_SCOPE:
                assert 'value="scope:' + option["code"] + '"' in body
    assert 'value="scope:INCHEON_GENERAL_CONSTRUCTION" selected' in body
    assert 'value="org:제물포구"' in _group(body, "시·군·구")
    assert 'value="org:인천특수저장기관"' in _group(body, "저장 기관")
    assert calls["summary"][0]["institution_scope"] == "INCHEON_GENERAL_CONSTRUCTION"
    assert calls["summary"][0]["institution_name"] == ""
    assert len(calls["options"]) == 1


def test_official_institution_options_fail_soft_before_any_project_read(monkeypatch):
    body, calls = _render(
        monkeypatch, [], region="인천광역시",
        selected="org:제물포구", option_error=RuntimeError("fixture unavailable"),
    )
    assert "예산 저장소 준비 중 (RuntimeError)" in body
    assert "인천 본청" in body
    assert 'class="muted budget-institution-evidence"' not in body
    assert len(calls["options"]) == 1
    assert calls["summary"] == []
    assert calls["rows"] == []


def test_stale_cross_region_institution_is_cleared_with_rich_options(monkeypatch):
    body, calls = _render(
        monkeypatch, [_option("천안시", "충청남도 천안시")],
        selected="org:청주시",
    )
    assert 'data-server-institution=""' in body
    assert calls["summary"][0]["institution_name"] == ""
    assert all(call["institution_name"] == "" for call in calls["rows"])
    assert 'class="muted budget-institution-evidence"' not in body
    assert len(calls["options"]) == 1


def test_official_source_link_rejects_non_https_scheme(monkeypatch):
    body, _calls = _render(
        monkeypatch, [_option("천안시", "충청남도 천안시",
                              source_url="javascript:alert(1)")],
        selected="org:천안시",
    )
    assert 'href="javascript:' not in body
    assert "기관 명부 출처" not in body



@pytest.fixture
def empty_postgres_read_adapter(monkeypatch, tmp_path):
    """An empty, disposable SQL adapter; audit only after schema initialization."""
    import socket
    from sqlalchemy import event, func, select
    import budget_pg_store

    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'official-empty-budget.sqlite3'}",
    )
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    engine, tables = budget_pg_store._engine_and_tables()
    sql = []
    source_io = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql.append(statement)

    def forbid_source_io(*args, **kwargs):
        source_io.append((args, kwargs))
        raise AssertionError("SOURCE_IO_FORBIDDEN_IN_OFFICIAL_INSTITUTION_READ")

    def row_counts():
        with engine.connect() as conn:
            return {
                name: conn.execute(select(func.count()).select_from(table)).scalar_one()
                for name, table in tables.items() if name != "metadata"
            }

    before = row_counts()
    assert before["projects"] == before["states"] == 0
    event.listen(engine, "before_cursor_execute", capture)
    monkeypatch.setattr(socket.socket, "connect", forbid_source_io)
    monkeypatch.setattr(socket, "create_connection", forbid_source_io)
    try:
        yield {"sql": sql, "source_io": source_io}
        assert row_counts() == before
        assert source_io == []
        assert sql
        assert all(
            statement.lstrip().split(None, 1)[0].upper() in {"SELECT", "PRAGMA"}
            for statement in sql
        ), sql
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        budget_pg_store.reset_engine_cache()


def _real_budget_page(monkeypatch, *, region, selected):
    """Stub only authentication/layout, leaving registry and budget reads intact."""
    clean = importlib.import_module("vnext_clean_app")
    monkeypatch.setattr(clean, "TEST_MODE", True)
    monkeypatch.setattr(clean, "require_user", lambda _request: {"username": "audit"})
    monkeypatch.setattr(clean, "is_result_server", lambda: False)
    monkeypatch.setattr(clean, "layout", lambda _title, body, _section, _user: body)
    params = {"year": "2026", "region": region, "institution_filter": "org:" + selected}
    request = Request({
        "type": "http", "method": "GET", "path": "/budget",
        "query_string": urlencode(params).encode("ascii"), "headers": [],
    })
    return clean, clean.budget_page(request)


@pytest.mark.parametrize("region,institution", [
    ("충청남도", "천안시"), ("충청북도", "청주시"),
    ("경상남도", "창원시"), ("경상북도", "포항시"),
])
def test_real_official_uncollected_institution_stays_selected_with_zero_ui_and_excel(
    monkeypatch, empty_postgres_read_adapter, region, institution,
):
    import html
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    from urllib.parse import parse_qs, urlsplit

    options = budget_read_vnext.budget_institution_options(fiscal_year=2026, region=region)
    actual = next(option for option in options if option["name"] == institution)
    assert actual["official"] is True
    assert actual["kind"] == "LOCAL_GOVERNMENT"
    assert actual["has_stored"] is False
    assert actual["source_layers"] == ()

    captured = empty_postgres_read_adapter["sql"]
    page_start = len(captured)
    clean, body = _real_budget_page(monkeypatch, region=region, selected=institution)
    page_sql = captured[page_start:]
    name_reads = [
        statement for statement in page_sql
        if statement.lstrip().upper().startswith("SELECT DISTINCT")
        and all(
            "." + column in statement.split("FROM", 1)[0]
            for column in ("org_name", "institution_name", "source_layer")
        )
    ]
    assert len(name_reads) == 1
    assert "예산 저장소 준비 중" not in body
    assert f'<option value="org:{institution}" selected>' in _group(body, "시·군·구")
    assert f'data-server-institution="org:{institution}"' in body
    assert body.count("2026년 저장 예산자료 없음 · 조회 결과 없음") == 3

    scope = {
        "fiscal_year": 2026, "region": region, "institution_name": institution,
        "source_layers": ("DETAIL_EXECUTION", "EDUCATION"),
    }
    summary = budget_read_vnext.screen_budget_summary(**scope)
    assert summary["project_count"] == 0
    assert summary["budget_total"] == summary["executed_total"] == summary["remaining_total"] == 0
    assert summary["source_io_performed"] is False
    assert budget_read_vnext.screen_budget_rows(**scope) == []

    export_url = html.unescape(
        re.search(r'href="(/budget/export\.xlsx[^"]+)"', body).group(1)
    )
    parts = urlsplit(export_url)
    assert parse_qs(parts.query)["institution_filter"] == ["org:" + institution]
    export_request = Request({
        "type": "http", "method": "GET", "path": parts.path,
        "query_string": parts.query.encode("ascii"), "headers": [],
    })
    response = clean.budget_export_xlsx(export_request)
    assert response.status_code == 200
    assert response.headers["X-G2B-Export-Rows"] == "0"
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    assert len(sheet.findall("m:sheetData/m:row", namespace)) == 1
    assert empty_postgres_read_adapter["source_io"] == []


def test_real_moe_registry_kind_is_rendered_in_education_office_group(
    monkeypatch, empty_postgres_read_adapter,
):
    import html
    import budget_institution_registry_vnext

    options = budget_read_vnext.budget_institution_options(
        fiscal_year=2026, region="충청남도",
    )
    office = next(option for option in options if option["name"] == "충청남도교육청")
    assert office["official"] is True
    assert office["kind"] == "EDUCATION_OFFICE"
    assert office["source_url"] == budget_institution_registry_vnext.MOE_SOURCE_URL
    _clean, body = _real_budget_page(
        monkeypatch, region="충청남도", selected=office["name"],
    )
    assert "예산 저장소 준비 중" not in body
    assert '<option value="org:충청남도교육청" selected>' in _group(body, "시·도 교육청")
    assert "충청남도교육청" not in _group(body, "시·군·구")
    assert "충청남도교육청 (2026년 저장자료 없음)" in _group(body, "시·도 교육청")
    assert 'href="' + html.escape(office["source_url"], quote=True) + '"' in body
    assert body.count("2026년 저장 예산자료 없음 · 조회 결과 없음") == 3
    assert empty_postgres_read_adapter["source_io"] == []
