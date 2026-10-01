"""Pure budget normalization for G2B 4.1.

Source JSON is used only in memory. Persisted rows contain canonical fields and
source hashes, never the original source payload.
"""
from __future__ import annotations


def _norm_key(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    normalized = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = normalized.get(_norm_key(name))
        if value not in (None, ""):
            return value
    return ""


def _num(value, default=0):
    try:
        text = str(value or "").replace(",", "").replace("원", "").strip()
        return int(round(float(text))) if text else int(default)
    except (TypeError, ValueError):
        return int(default)


def _year(row, fallback=""):
    return _num(_pick(row, "fyr", "YMQ", "year", "fiscalYear", "회계연도"), _num(fallback, 0))


def _date_text(value):
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value or "").strip()


def normalize_record(dataset, payload, *, source_date=""):
    row = payload if isinstance(payload, dict) else {}
    if dataset == "budget":
        budget_source = _pick(row, "bdg_cash_amt", "budget_amount", "amount", "예산현액")
        appropriation_source = _pick(row, "cpl_amt", "compile_amt", "편성액")
        budget = _num(budget_source)
        appropriation = _num(appropriation_source)
        executed = _num(_pick(row, "ep_amt", "executed_amount", "지출액", "집행액"))
        basis = budget if budget_source not in (None, "") else appropriation
        return {
            "source_layer": "DETAIL_EXECUTION",
            "fiscal_year": _year(row, str(source_date)[:4]),
            "snapshot_date": _date_text(source_date or _pick(row, "exe_ymd")),
            "region_code": str(_pick(row, "wa_laf_cd") or ""),
            "region_name": str(_pick(row, "wa_laf_hg_nm") or ""),
            "org_code": str(_pick(row, "laf_cd") or ""),
            "org_name": str(_pick(row, "laf_hg_nm") or _pick(row, "wa_laf_hg_nm") or ""),
            "dept_code": str(_pick(row, "dept_cd") or ""),
            "dept_name": str(_pick(row, "dept_nm") or ""),
            "institution_code": "",
            "institution_name": "",
            "project_code": str(_pick(row, "dbiz_cd") or ""),
            "project_name": str(_pick(row, "dbiz_nm") or ""),
            "field_code": str(_pick(row, "fld_cd") or ""),
            "field_name": str(_pick(row, "fld_nm") or ""),
            "section_code": str(_pick(row, "ane_part_cd", "part_cd", "sect_cd") or ""),
            "section_name": str(_pick(row, "part_nm", "sect_nm") or ""),
            "account_code": str(_pick(row, "acnt_dv_cd") or ""),
            "account_name": str(_pick(row, "acnt_dv_nm") or ""),
            "budget_amount": budget,
            "appropriation_amount": appropriation,
            "executed_amount": executed,
            "remaining_amount": basis - executed if (budget_source not in (None, "") or appropriation_source not in (None, "")) else 0,
            "national_amount": _num(_pick(row, "bdg_ntep")),
            "province_amount": _num(_pick(row, "capep")),
            "local_amount": _num(_pick(row, "sggep")),
            "other_amount": _num(_pick(row, "etc_amt")),
        }
    if dataset == "budget_appropriation":
        amount = _num(_pick(row, "biz_bdg_tott_amt", "biz_bdg_prsm_amt"))
        field_name = str(_pick(row, "fld_nm", "field_name") or "")
        section_name = str(_pick(row, "sect_nm", "part_nm", "section_name") or "")
        return {
            "source_layer": "APPROPRIATION",
            "fiscal_year": _year(row, str(source_date)[:4]),
            "snapshot_date": "",
            "region_code": str(_pick(row, "wa_laf_cd") or ""),
            "region_name": str(_pick(row, "wa_laf_hg_nm") or ""),
            "org_code": str(_pick(row, "laf_cd") or _pick(row, "wa_laf_cd") or ""),
            "org_name": str(_pick(row, "laf_hg_nm") or _pick(row, "wa_laf_hg_nm") or ""),
            "dept_code": "", "dept_name": "", "institution_code": "", "institution_name": "",
            "project_code": "",
            "project_name": " > ".join(part for part in (field_name, section_name) if part),
            "field_code": str(_pick(row, "fld_cd") or ""),
            "field_name": field_name,
            "section_code": str(_pick(row, "sect_cd", "part_cd") or ""),
            "section_name": section_name,
            "account_code": str(_pick(row, "acnt_dv_cd") or ""),
            "account_name": str(_pick(row, "acnt_dv_nm") or ""),
            "budget_amount": amount, "appropriation_amount": amount,
            "executed_amount": 0, "remaining_amount": amount,
            "national_amount": 0, "province_amount": 0, "local_amount": 0, "other_amount": 0,
        }
    if dataset == "education_budget":
        budget_source = _pick(
            row, "budget_amount", "budgetAmount", "bdgAmt", "BUDGET_AMT",
            "예산액", "예산현액", "본예산액", "최종예산액",
        )
        budget = _num(budget_source)
        executed = _num(_pick(
            row, "executed_amount", "executedAmount", "expenseAmt", "EXPENDITURE_AMT",
            "집행액", "지출액", "결산액",
        ))
        office = str(_pick(
            row, "office_name", "officeName", "eduOfficeNm", "ATPT_OFCDC_SC_NM",
            "시도교육청명", "교육청명", "기관명", "org_name",
        ) or "")
        return {
            "source_layer": "EDUCATION",
            "fiscal_year": _year(row, str(source_date)[:4]),
            "snapshot_date": _date_text(source_date),
            "region_code": "", "region_name": office,
            "org_code": str(_pick(row, "officeCode", "eduOfficeCode", "ATPT_OFCDC_SC_CODE", "교육청코드") or ""),
            "org_name": office,
            "dept_code": str(_pick(row, "departmentCode", "deptCode", "부서코드") or ""),
            "dept_name": str(_pick(row, "departmentName", "deptName", "부서명") or ""),
            "institution_code": str(_pick(row, "schoolCode", "institutionCode", "학교코드", "기관코드") or ""),
            "institution_name": str(_pick(row, "schoolName", "institutionName", "학교명", "기관명") or ""),
            "project_code": str(_pick(row, "projectCode", "businessCode", "사업코드", "세부사업코드") or ""),
            "project_name": str(_pick(
                row, "project_name", "projectName", "business_name", "businessName", "bizNm", "bsnsNm",
                "dbiz_nm", "SAUP_NM", "사업명", "세부사업명", "단위사업명", "정책사업명",
            ) or ""),
            "field_code": "", "field_name": "교육비특별회계",
            "section_code": str(_pick(row, "programCode", "policyBusinessCode", "정책사업코드", "단위사업코드") or ""),
            "section_name": str(_pick(row, "programName", "policyBusinessName", "정책사업명", "단위사업명") or ""),
            "account_code": str(_pick(row, "accountCode", "itemCode", "세목코드", "과목코드") or ""),
            "account_name": str(_pick(row, "accountName", "itemName", "세목명", "과목명") or ""),
            "budget_amount": budget, "appropriation_amount": budget,
            "executed_amount": executed,
            "remaining_amount": budget - executed if budget_source not in (None, "") else 0,
            "national_amount": 0, "province_amount": 0, "local_amount": 0, "other_amount": 0,
        }
    raise ValueError(f"unsupported budget dataset: {dataset}")


def compat_payload(dataset, row):
    """Build a minimal transient payload for existing classifier/read adapters."""
    r = row or {}
    common = {
        "project_name": str(r.get("project_name") or ""),
        "projectName": str(r.get("project_name") or ""),
        "사업명": str(r.get("project_name") or ""),
    }
    if dataset == "budget":
        return {
            **common,
            "fyr": str(r.get("fiscal_year") or ""),
            "exe_ymd": str(r.get("snapshot_date") or "").replace("-", ""),
            "wa_laf_cd": str(r.get("region_code") or ""),
            "wa_laf_hg_nm": str(r.get("region_name") or ""),
            "laf_cd": str(r.get("org_code") or ""),
            "laf_hg_nm": str(r.get("org_name") or ""),
            "dept_cd": str(r.get("dept_code") or ""),
            "dept_nm": str(r.get("dept_name") or ""),
            "dbiz_cd": str(r.get("project_code") or ""),
            "dbiz_nm": str(r.get("project_name") or ""),
            "fld_cd": str(r.get("field_code") or ""),
            "fld_nm": str(r.get("field_name") or ""),
            "sect_cd": str(r.get("section_code") or ""),
            "sect_nm": str(r.get("section_name") or ""),
            "acnt_dv_cd": str(r.get("account_code") or ""),
            "acnt_dv_nm": str(r.get("account_name") or ""),
            "bdg_cash_amt": int(r.get("budget_amount") or 0),
            "amount": int(r.get("budget_amount") or 0),
            "cpl_amt": int(r.get("appropriation_amount") or 0),
            "ep_amt": int(r.get("executed_amount") or 0),
            "bdg_ntep": int(r.get("national_amount") or 0),
            "capep": int(r.get("province_amount") or 0),
            "sggep": int(r.get("local_amount") or 0),
            "etc_amt": int(r.get("other_amount") or 0),
        }
    if dataset == "budget_appropriation":
        return {
            **common,
            "fyr": str(r.get("fiscal_year") or ""),
            "wa_laf_cd": str(r.get("region_code") or ""),
            "wa_laf_hg_nm": str(r.get("region_name") or ""),
            "laf_cd": str(r.get("org_code") or ""),
            "laf_hg_nm": str(r.get("org_name") or ""),
            "fld_cd": str(r.get("field_code") or ""),
            "fld_nm": str(r.get("field_name") or ""),
            "sect_cd": str(r.get("section_code") or ""),
            "sect_nm": str(r.get("section_name") or ""),
            "acnt_dv_cd": str(r.get("account_code") or ""),
            "acnt_dv_nm": str(r.get("account_name") or ""),
            "biz_bdg_tott_amt": int(r.get("budget_amount") or 0),
        }
    return {
        **common,
        "fiscalYear": str(r.get("fiscal_year") or ""),
        "officeCode": str(r.get("org_code") or ""),
        "officeName": str(r.get("org_name") or ""),
        "departmentCode": str(r.get("dept_code") or ""),
        "departmentName": str(r.get("dept_name") or ""),
        "schoolCode": str(r.get("institution_code") or ""),
        "schoolName": str(r.get("institution_name") or ""),
        "projectCode": str(r.get("project_code") or ""),
        "programCode": str(r.get("section_code") or ""),
        "programName": str(r.get("section_name") or ""),
        "accountCode": str(r.get("account_code") or ""),
        "accountName": str(r.get("account_name") or ""),
        "budget_amount": int(r.get("budget_amount") or 0),
        "executed_amount": int(r.get("executed_amount") or 0),
    }
