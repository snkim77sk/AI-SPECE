import budget_shopping_match_store as store


def _summary(matches):
    high = [row for row in matches if row["level"] == "HIGH"]
    projects = {
        row["budget_project_identity"]
        for row in matches
        if row.get("budget_project_identity")
    }
    high_projects = {
        row["budget_project_identity"]
        for row in high
        if row.get("budget_project_identity")
    }
    return {
        "fiscal_year": 2026,
        "region": "",
        "categories": ["LIGHTING", "POLE"],
        "budget_projects_scanned": 20,
        "shopping_rows_scanned": 50,
        "matches": matches,
        "matched_budget_projects": len(projects),
        "high_matched_budget_projects": len(high_projects),
        "high_matches": len(high),
        "candidate_matches": len(matches) - len(high),
        "high_matched_shopping_amount": sum(
            row["shopping_amount"] for row in high
        ),
        "project_match_rate": len(projects) / 20,
        "evidence_sufficient_for_pattern_learning": False,
        "expand_2025_recommended": True,
        "expansion_reasons": ["HIGH_MATCH_SAMPLE_SMALL"],
    }


def _match(
    *,
    project="P1",
    shopping="S1",
    level="HIGH",
    score=90,
    budget_amount=300000000,
    shopping_amount=200000000,
    lag=100,
    signals=None,
):
    return {
        "budget_project_identity": f"DETAIL_EXECUTION|2026|ORG|D|{project}|A",
        "budget_raw_source_key": f"B-{project}",
        "fiscal_year": 2026,
        "budget_region": "인천광역시",
        "budget_org": "인천옹진군",
        "budget_dept": "도로과",
        "budget_project_code": project,
        "budget_project_name": "보안등 LED 교체사업",
        "budget_category": "LIGHTING",
        "budget_amount": budget_amount,
        "budget_source_date": "2026-03-01",
        "shopping_source_key": shopping,
        "shopping_date": "2026-06-15",
        "shopping_org": "인천광역시 옹진군",
        "shopping_category": "LIGHTING",
        "shopping_item": "LED 보안등기구",
        "shopping_delivery_name": "보안등 교체 관급자재",
        "shopping_vendor": "테스트조명",
        "shopping_amount": shopping_amount,
        "score": score,
        "level": level,
        "evidence": ["ORG_ALIAS_MATCH", "CATEGORY_EXACT"],
        "shared_signals": signals or ["LED", "SECURITY_LIGHT"],
        "shared_tokens": ["보안등"],
        "lag_days": lag,
    }


def test_save_match_summary_replaces_same_run_instead_of_accumulating_stale_rows():
    store.ensure_schema()
    first = store.save_match_summary(_summary([
        _match(project="P1", shopping="S1"),
        _match(project="P2", shopping="S2"),
    ]))
    assert first["saved_matches"] == 2

    second = store.save_match_summary(_summary([
        _match(project="P1", shopping="S1", shopping_amount=250000000),
    ]))
    assert second["run_key"] == first["run_key"]
    assert second["saved_matches"] == 1

    with __import__("db").connect() as conn:
        rows = conn.execute(
            "SELECT shopping_source_key,shopping_amount "
            "FROM budget_shopping_match_evidence "
            "WHERE run_key=? ORDER BY shopping_source_key",
            (first["run_key"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["shopping_source_key"] == "S1"
    assert rows[0]["shopping_amount"] == 250000000


def test_organization_patterns_dedupe_projects_and_shopping_amounts():
    store.ensure_schema()
    summary = _summary([
        _match(project="P1", shopping="S1", shopping_amount=100000000, lag=90),
        _match(project="P1", shopping="S2", shopping_amount=50000000, lag=120),
        _match(project="P2", shopping="S2", shopping_amount=50000000, lag=120),
        _match(
            project="P3",
            shopping="S3",
            level="CANDIDATE",
            score=70,
            shopping_amount=80000000,
            lag=60,
        ),
    ])
    store.save_match_summary(summary)

    patterns = store.organization_patterns(fiscal_years=[2026])
    assert len(patterns) == 1
    row = patterns[0]

    assert row["org_name"] == "인천옹진군"
    assert row["high_match_rows"] == 3
    assert row["candidate_match_rows"] == 1
    assert row["high_matched_budget_projects"] == 2
    assert row["high_matched_shopping_rows"] == 2
    assert row["matched_budget_amount"] == 600000000
    assert row["actual_shopping_amount"] == 150000000
    assert row["shopping_to_budget_amount_ratio"] == 0.25
    assert row["average_nonnegative_lag_days"] == 110.0
    assert row["signal_counts"]["LED"] == 3
    assert row["signal_counts"]["SECURITY_LIGHT"] == 3
    assert row["pattern_basis"] == "PERSISTED_HIGH_MATCH_EVIDENCE"

    incheon = store.organization_patterns(
        fiscal_years=[2026],
        region="인천광역시",
    )
    assert len(incheon) == 1
    assert store.organization_patterns(
        fiscal_years=[2026],
        region="서울특별시",
    ) == []


def test_match_read_paths_do_not_run_schema_ddl():
    source = __import__("pathlib").Path(
        "budget_shopping_match_store.py"
    ).read_text(encoding="utf-8")
    run_block = source.split("def match_run_rows", 1)[1].split(
        "def organization_patterns", 1
    )[0]
    pattern_block = source.split("def organization_patterns", 1)[1]
    assert "ensure_schema()" not in run_block
    assert "ensure_schema()" not in pattern_block


def test_match_run_rows_preserve_expansion_diagnostics():
    store.ensure_schema()
    store.save_match_summary(_summary([_match()]))

    runs = store.match_run_rows(fiscal_year=2026, region="")
    assert len(runs) == 1
    row = runs[0]
    assert row["budget_projects_scanned"] == 20
    assert row["expand_2025_recommended"] == 1
    assert "HIGH_MATCH_SAMPLE_SMALL" in row["expansion_reasons_json"]
