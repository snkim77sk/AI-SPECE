import budget_normalizer_v41


def test_qwgjk_department_aliases_are_normalized():
    aliases = (
        ("dept_nm", "도로과"),
        ("dpt_nm", "도로관리과"),
        ("deptName", "시설과"),
        ("departmentName", "건축과"),
        ("department_name", "공원과"),
        ("부서명", "도시계획과"),
        ("담당부서", "종합건설본부"),
        ("담당부서명", "경제자유구역청"),
    )
    for field, expected in aliases:
        fact = budget_normalizer_v41.normalize_record(
            "budget",
            {
                "fyr": "2026",
                "laf_hg_nm": "인천광역시",
                "dbiz_cd": "P1",
                "dbiz_nm": "예산사업",
                field: expected,
                "bdg_cash_amt": "1000",
            },
            source_date="2026-10-07",
        )
        assert fact["dept_name"] == expected


def test_qwgjk_missing_department_remains_explicitly_empty_not_invented():
    fact = budget_normalizer_v41.normalize_record(
        "budget",
        {
            "fyr": "2026",
            "laf_hg_nm": "인천광역시",
            "dbiz_cd": "P1",
            "dbiz_nm": "예산사업",
            "bdg_cash_amt": "1000",
        },
        source_date="2026-10-07",
    )
    assert fact["dept_name"] == ""
