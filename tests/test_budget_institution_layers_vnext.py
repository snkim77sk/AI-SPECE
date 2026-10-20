"""Read-only stored institution/layer coverage on both supported adapters."""
import re

import pytest
from sqlalchemy import event, insert, update

import budget_pg_store
import budget_storage


COMMON = "충청남도 CI 공통기관"
SCHOOL = "CI 합성학교"
ALL_LAYERS = ("APPROPRIATION", "DETAIL_EXECUTION", "EDUCATION")


@pytest.fixture(params=("postgresql_adapter", "sqlite"))
def institution_backend(request, monkeypatch, tmp_path):
    budget_pg_store.reset_engine_cache()
    if request.param == "postgresql_adapter":
        monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
        monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'institution-layers.sqlite3'}")
        for key in ("G2B_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
            monkeypatch.delenv(key, raising=False)
    yield request.param
    budget_pg_store.reset_engine_cache()


def _save(dataset, key, organization, *, region="충남", year=2026, school="", day="2026-10-07"):
    if dataset == "education_budget":
        payload = {
            "fiscalYear": str(year), "officeName": organization, "schoolName": school,
            "departmentName": "CI 부서", "projectCode": key,
            "projectName": f"CI synthetic {key}", "budget_amount": 300,
        }
    else:
        payload = {
            "fyr": str(year), "wa_laf_hg_nm": region, "laf_hg_nm": organization,
            "dept_nm": "CI 부서", "dbiz_cd": key, "dbiz_nm": f"CI synthetic {key}",
            "exe_ymd": day.replace("-", ""), "bdg_cash_amt": "100",
            "fld_nm": "CI 분야", "sect_nm": key, "biz_bdg_tott_amt": "200",
        }
    budget_storage.preserve_raw(
        dataset, key, payload, source_system="CI_INSTITUTION_FIXTURE",
        source_operation="CI_SYNTHETIC", source_date=day,
    )


def _seed():
    for dataset in ("budget", "budget_appropriation", "education_budget"):
        _save(dataset, "shared-key", "  " + COMMON + "  ", school="  " + SCHOOL + "  ")
    _save("budget", "old-year", "충청남도 CI 과거연도", year=2025)
    _save("budget", "other-region", "충청북도 CI 다른지역", region="충북")
    _save("budget", "changed", "충청남도 CI 과거기관", day="2026-10-01")
    _save("budget", "changed", "충청남도 CI 현재기관", day="2026-10-08")


def _coverage(**kwargs):
    return {item["name"]: item["source_layers"]
            for item in budget_storage.current_institution_layers(**kwargs)}


def test_current_institution_layers_groups_both_names_and_all_current_layers(institution_backend):
    _seed()
    scope = dict(fiscal_year=2026, region_terms=("충청남도", "충남"))
    result = _coverage(**scope)
    assert result == {
        COMMON: ALL_LAYERS,
        SCHOOL: ("EDUCATION",),
        "충청남도 CI 현재기관": ("DETAIL_EXECUTION",),
    }
    names = budget_storage.current_institution_names(**scope)
    assert set(names) == set(result)
    assert isinstance(names, list) and all(isinstance(name, str) for name in names)
    rows = budget_storage.current_institution_layers(**scope)
    assert rows == sorted(rows, key=lambda row: (row["name"].casefold(), row["name"]))
    assert all(isinstance(row["source_layers"], tuple) for row in rows)


def test_current_institution_layers_filters_before_grouping(institution_backend):
    _seed()
    scope = dict(fiscal_year="2026", region_terms=(" 충남 ",))
    assert _coverage(source_layers=(" APPROPRIATION ",), **scope) == {
        COMMON: ("APPROPRIATION",),
    }
    assert _coverage(datasets=("education_budget",), **scope) == {
        COMMON: ("EDUCATION",), SCHOOL: ("EDUCATION",),
    }
    assert _coverage(fiscal_year=2025, region_terms=("충남",)) == {
        "충청남도 CI 과거연도": ("DETAIL_EXECUTION",),
    }
    assert _coverage(fiscal_year=2026, region_terms=("충북",)) == {
        "충청북도 CI 다른지역": ("DETAIL_EXECUTION",),
    }
    assert _coverage(fiscal_year=2026, region_terms=("invalid-region",)) == {}
    assert _coverage(datasets=(), source_layers=(), **scope) == _coverage(**scope)


def test_pg_layers_uses_one_distinct_three_column_read_without_initialization(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'pg-layer-shape.sqlite3'}")
    for key in ("G2B_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(key, raising=False)
    budget_pg_store.reset_engine_cache()
    _seed()
    engine, tables = budget_pg_store._engine_and_tables()
    # A dangling normalized project is not current without its state pointer.
    with engine.begin() as conn:
        conn.execute(insert(tables["projects"]), {
            "dataset": "budget", "record_key": "orphan", "fiscal_year": 2026,
            "region_name": "충남", "org_name": "충남 CI 비현재기관",
            "source_layer": "DETAIL_EXECUTION", "updated_at": "2026-10-09",
        })
        conn.execute(update(tables["projects"]).where(
            tables["projects"].c.dataset == "budget",
            tables["projects"].c.record_key == "shared-key",
        ).values(org_name="  " + COMMON + "  ", institution_name=" " + COMMON + " "))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("INSTITUTION_LAYER_READ_MUST_NOT_LOAD_FACTS_OR_INITIALIZE")

    monkeypatch.setattr(budget_pg_store, "_read_only_engine_and_tables", lambda: (engine, tables))
    monkeypatch.setattr(budget_pg_store, "_engine_and_tables", forbidden)
    monkeypatch.setattr(budget_pg_store, "current_project_rows", forbidden)
    monkeypatch.setattr(budget_storage, "current_normalized_rows", forbidden)
    deadlines, statements = [], []
    monkeypatch.setattr(budget_pg_store, "_bound_budget_view_query",
                        lambda conn, timeout_ms: deadlines.append(timeout_ms))

    def capture(_conn, _cursor, statement, _params, context, _many):
        assert re.match(r"\s*SELECT\s+DISTINCT\b", statement, re.I), statement
        statements.append(context.compiled.statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = _coverage(fiscal_year=2026, region_terms=("충남",))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        budget_pg_store.reset_engine_cache()
    assert result[COMMON] == ALL_LAYERS
    assert "충남 CI 비현재기관" not in result
    assert len(statements) == 1 and statements[0]._distinct
    assert list(statements[0].selected_columns.keys()) == [
        "org_name", "institution_name", "source_layer",
    ]
    assert deadlines == [4000]


def test_sqlite_layer_names_trim_merge_and_keep_blank_layer_names(monkeypatch):
    calls = []

    def rows(datasets, **kwargs):
        calls.append((datasets, kwargs))
        return [
            {"org_name": " CI same ", "institution_name": "CI same", "source_layer": " DETAIL_EXECUTION "},
            {"org_name": "CI same", "institution_name": " CI school ", "source_layer": "EDUCATION"},
            {"org_name": " ", "institution_name": "", "dept_name": "CI excluded department",
             "source_layer": "APPROPRIATION"},
            {"org_name": "CI unknown layer", "source_layer": ""},
        ]

    monkeypatch.setattr(budget_storage, "current_normalized_rows", rows)
    scope = dict(fiscal_year=2026, source_layers=("DETAIL_EXECUTION",), region_terms=("충남",))
    result = budget_storage.current_institution_layers(("budget",), **scope)
    assert result == [
        {"name": "CI same", "source_layers": ("DETAIL_EXECUTION", "EDUCATION")},
        {"name": "CI school", "source_layers": ("EDUCATION",)},
        {"name": "CI unknown layer", "source_layers": ()},
    ]
    assert calls == [(("budget",), dict(**scope, limit=None, offset=0))]


@pytest.mark.parametrize("helper", (
    budget_storage.current_institution_layers, budget_pg_store.current_institution_layers,
))
def test_institution_layers_rejects_unknown_dataset(helper):
    with pytest.raises(ValueError, match="UNSUPPORTED_BUDGET_DATASET"):
        helper(("not-a-budget-dataset",))
