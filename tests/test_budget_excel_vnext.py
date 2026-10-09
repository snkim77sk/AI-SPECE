import io
import zipfile

import budget_excel_vnext


def test_budget_xlsx_contains_search_result_rows_and_excel_parts():
    data = budget_excel_vnext.build_budget_xlsx(
        [{
            "fiscal_year": 2026,
            "region_display": "인천광역시",
            "org_display": "인천광역시",
            "dept_name": "도로과",
            "project_name": "드림로~원당대로간 도로개설",
            "project_code": "P-ROAD-1",
            "field_name": "교통및물류",
            "section_name": "도로",
            "account_name": "일반회계",
            "category_label": "기타",
            "budget_amount": 7831079120,
            "executed_amount": 1680115290,
            "remaining_amount": 6150963830,
            "execution_rate": 1680115290 / 7831079120,
            "snapshot_date": "2026-10-07",
        }],
        sheet_name="2026 예산사업",
    )

    assert data[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        assert "xl/styles.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        styles = archive.read("xl/styles.xml").decode("utf-8")

    assert 'sheet name="2026 예산사업"' in workbook
    assert "드림로~원당대로간 도로개설" in sheet
    assert "담당부서" in sheet
    assert "6150963830" in sheet
    assert "<autoFilter" in sheet
    assert 'state="frozen"' in sheet
    assert '#,##0&quot;원&quot;' in styles


def test_budget_xlsx_escapes_xml_text():
    data = budget_excel_vnext.build_budget_xlsx([{
        "fiscal_year": 2026,
        "project_name": "A&B <조명> 사업",
    }])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "A&amp;B &lt;조명&gt; 사업" in sheet
