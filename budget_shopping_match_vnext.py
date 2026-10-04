"""Explainable historical QWGJK -> G2B shopping match analysis.

The matcher is read-only. It compares stored normalized QWGJK detail projects with
stored LED/pole shopping delivery rows. It never claims that a budget row directly
funded a procurement row; results are evidence-ranked candidates for sales analysis.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

import budget_read_vnext
import procurement_read_vnext

DEFAULT_CATEGORIES = ("LIGHTING", "POLE")
FULL_BUDGET_PAGE_SIZE = 1000
FULL_SHOPPING_PAGE_SIZE = 5000
MAX_FULL_BUDGET_SOURCE_ROWS = 20000
MAX_FULL_SHOPPING_ROWS = 50000
KST = ZoneInfo("Asia/Seoul")


def _kst_today():
    return dt.datetime.now(KST).date()
MIN_CANDIDATE_SCORE = 65
MIN_HIGH_SCORE = 80
MIN_PROJECT_SAMPLE = 30
MIN_HIGH_MATCHED_PROJECTS = 10

_GENERIC_TOKENS = frozenset({
    "사업", "공사", "구매", "납품", "설치", "교체", "개선", "정비", "유지",
    "유지보수", "신규", "노후", "시설", "물품", "관급", "관급자재", "일원",
    "일식", "제작", "공급", "추진", "시행", "관련", "관리",
})

_SIGNAL_TERMS = {
    "LED": ("led", "엘이디"),
    "STREET_LIGHT": ("가로등", "도로조명", "도로 조명"),
    "SECURITY_LIGHT": ("보안등", "방범등"),
    "LANDSCAPE_LIGHT": ("경관조명", "경관등"),
    "PATH_LIGHT": ("산책로조명", "산책로 조명", "보행등", "공원등"),
    "FLOOD_LIGHT": ("투광등", "투광조명"),
    "SOLAR_LIGHT": ("태양광조명", "태양광 조명", "태양광등"),
    "POLE": ("가로등주", "보안등주", "등주", "조명주", "폴"),
}


def _norm(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _org_aliases(name, region=""):
    """Return organization-specific aliases without treating a region as an org.

    Region is only a normalization hint. A bare regional name such as 인천 must
    never make two different institutions in the same region match.
    """
    text = _norm(name)
    if not text:
        return set()

    aliases = {text}
    region_tokens = (
        "특별자치시", "특별자치도", "광역시", "특별시",
    )

    # Normalize long-form regional labels inside the organization name itself.
    for token in region_tokens:
        if token in text:
            aliases.add(text.replace(token, ""))

    region_text = _norm(region)
    if region_text:
        region_short = region_text
        for token in (*region_tokens, "도"):
            region_short = region_short.replace(token, "")

        # Only top-level regional governments may use the bare region alias.
        # Subordinate organizations never inherit it.
        top_level_aliases = {region_text}
        if region_short:
            top_level_aliases.add(region_short)
            if region_text.endswith(("특별자치시", "광역시", "특별시")):
                top_level_aliases.add(region_short + "시")
        if text in top_level_aliases:
            aliases.update(top_level_aliases)

        # Strip the region prefix only to expose the institution-specific part.
        # Prefer the full regional label, then use the short form for source rows
        # such as 인천옹진군. Never add the prefix itself as an alias.
        candidates = list(aliases)
        for candidate in candidates:
            matched_full = bool(
                region_text
                and candidate.startswith(region_text)
                and len(candidate) > len(region_text)
            )
            if matched_full:
                aliases.add(candidate[len(region_text):])
                continue
            if (
                region_short
                and candidate.startswith(region_short)
                and len(candidate) > len(region_short)
            ):
                remainder = candidate[len(region_short):]
                if len(remainder) >= 2:
                    aliases.add(remainder)

    return {value for value in aliases if len(value) >= 2}

def _tokens(value):
    result = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", str(value or "").casefold()):
        token = token.strip()
        if len(token) < 2 or token in _GENERIC_TOKENS:
            continue
        if len(token) == 4 and token.isdigit() and token.startswith("20"):
            continue
        result.add(token)
    return result


def _signals(value):
    text = str(value or "").casefold()
    return {
        key
        for key, terms in _SIGNAL_TERMS.items()
        if any(term in text for term in terms)
    }


def _budget_text(row):
    return " ".join(str(row.get(name) or "") for name in (
        "project_name", "field_name", "section_name", "account_name",
    ))


def _shopping_text(row):
    return " ".join(str(row.get(name) or "") for name in (
        "delivery_req_name", "detail_item_name", "item_name", "model_name",
    ))


def _organization_basis(budget, shopping):
    budget_aliases = _org_aliases(
        budget.get("org_name"),
        budget.get("region_name"),
    )
    shopping_aliases = _org_aliases(
        shopping.get("demand_org"),
        shopping.get("demand_region"),
    )
    shared = budget_aliases & shopping_aliases
    if not shared:
        return "", []
    budget_full = _norm(budget.get("org_name"))
    shopping_full = _norm(shopping.get("demand_org"))
    if budget_full and shopping_full and budget_full == shopping_full:
        return "EXACT_ORG_NAME", sorted(shared)
    return "ORG_ALIAS_MATCH", sorted(shared)


def _date_score(budget, shopping):
    budget_day = str(
        budget.get("source_date")
        or budget.get("snapshot_date")
        or ""
    )[:10]
    shopping_day = str(shopping.get("source_date") or "")[:10]
    try:
        bday = dt.date.fromisoformat(budget_day)
        sday = dt.date.fromisoformat(shopping_day)
    except ValueError:
        return 0, None
    if bday.year != sday.year:
        return 0, (sday - bday).days
    lag = (sday - bday).days
    # Snapshot date is observation evidence, not necessarily the first budget
    # approval date. Same-year rows are valid; later procurement gets a small bonus.
    return (8 if lag >= 0 else 4), lag


def _amount_score(budget, shopping):
    budget_amount = int(
        budget.get("budget_amount")
        or budget.get("appropriation_amount")
        or 0
    )
    shopping_amount = int(shopping.get("amount") or 0)
    if budget_amount <= 0 or shopping_amount <= 0:
        return 0
    ratio = shopping_amount / budget_amount
    if ratio <= 1.10:
        return 7
    if ratio <= 1.50:
        return 4
    return 0


def _shopping_categories(shopping):
    values = shopping.get("primary_categories")
    if isinstance(values, (list, tuple, set)):
        selected = {
            str(value or "").upper()
            for value in values
            if str(value or "").upper() in DEFAULT_CATEGORIES
        }
        if selected:
            return selected
    category = str(shopping.get("primary_category") or "").upper()
    return {category} if category in DEFAULT_CATEGORIES else set()


def score_pair(budget, shopping):
    """Return one conservative explainable candidate score or None."""
    org_basis, shared_org = _organization_basis(budget, shopping)
    if not org_basis:
        return None

    budget_category = str(budget.get("primary_category") or "").upper()
    shopping_categories = _shopping_categories(shopping)
    if not shopping_categories:
        return None

    budget_text = _budget_text(budget)
    shopping_text = _shopping_text(shopping)
    budget_tokens = _tokens(budget_text)
    shopping_tokens = _tokens(shopping_text)
    shared_tokens = sorted(budget_tokens & shopping_tokens)
    shared_signals = sorted(_signals(budget_text) & _signals(shopping_text))

    # Require at least one lighting/pole semantic signal or distinctive shared token.
    if not shared_tokens and not shared_signals:
        return None

    score = 48 if org_basis == "EXACT_ORG_NAME" else 42
    evidence = [org_basis]

    if budget_category in shopping_categories:
        score += 20
        evidence.append("CATEGORY_EXACT")
    elif (
        budget_category in DEFAULT_CATEGORIES
        and shopping_categories <= set(DEFAULT_CATEGORIES)
    ):
        score += 10
        evidence.append("CATEGORY_RELATED")
    else:
        return None

    if shared_signals:
        signal_points = min(20, 10 + 5 * (len(shared_signals) - 1))
        score += signal_points
        evidence.append("SHARED_SIGNAL:" + ",".join(shared_signals))
    if shared_tokens:
        token_points = min(18, 8 + 4 * (len(shared_tokens) - 1))
        score += token_points
        evidence.append("SHARED_TOKEN:" + ",".join(shared_tokens[:5]))

    date_points, lag_days = _date_score(budget, shopping)
    score += date_points
    if date_points:
        evidence.append("SAME_FISCAL_YEAR")
    amount_points = _amount_score(budget, shopping)
    score += amount_points
    if amount_points:
        evidence.append("AMOUNT_PLAUSIBLE")

    score = min(100, int(score))
    if score < MIN_CANDIDATE_SCORE:
        return None

    level = "HIGH" if score >= MIN_HIGH_SCORE else "CANDIDATE"
    return {
        "score": score,
        "level": level,
        "evidence": evidence,
        "organization_basis": org_basis,
        "shared_org_aliases": shared_org,
        "shared_tokens": shared_tokens,
        "shared_signals": shared_signals,
        "lag_days": lag_days,
    }


def _shopping_index(rows):
    index = {}
    for row in rows:
        aliases = _org_aliases(
            row.get("demand_org"),
            row.get("demand_region"),
        )
        for alias in aliases:
            index.setdefault(alias, []).append(row)
    return index


def source_population_coverage(fiscal_year):
    """Return fail-closed source coverage evidence for exact institution rates."""
    from db import connect
    import budget_storage

    year = int(fiscal_year)
    today = _kst_today()

    if year == 2025:
        import budget_match_backfill_vnext
        plan = budget_match_backfill_vnext.build_2025_backfill_plan(
            {"expand_2025_recommended": True}
        )
        shopping_complete = bool(plan.get("shopping_complete"))
        budget_complete = bool(plan.get("budget_complete"))
        return {
            "fiscal_year": year,
            "shopping_complete": shopping_complete,
            "budget_complete": budget_complete,
            "source_complete": shopping_complete and budget_complete,
            "basis": "MATCH_BACKFILL_2025_CHECKPOINTS",
        }

    if year != today.year:
        return {
            "fiscal_year": year,
            "shopping_complete": False,
            "budget_complete": False,
            "source_complete": False,
            "basis": "UNSUPPORTED_YEAR",
        }

    start = dt.date(year, 1, 1)
    latest = today - dt.timedelta(days=1)
    expected_days = max(0, (latest - start).days + 1)
    completed_days = set()
    if expected_days:
        with connect() as conn:
            rows = conn.execute(
                """SELECT scope_key,status
                   FROM collection_checkpoints
                   WHERE dataset='shopping_delivery'
                     AND status='COMPLETE'
                   ORDER BY scope_key"""
            ).fetchall()
        for row in rows:
            scope = str(row["scope_key"] or "")
            parts = scope.split(":")
            if len(parts) != 2 or parts[0] != parts[1]:
                continue
            try:
                day = dt.date.fromisoformat(parts[0])
            except ValueError:
                continue
            if start <= day <= latest:
                completed_days.add(day)

    shopping_complete = bool(
        expected_days > 0 and len(completed_days) == expected_days
    )

    budget_complete = False
    if latest.year == year:
        scope = f"{year}:{latest.isoformat()}"
        try:
            if budget_storage.using_postgres():
                import budget_pg_store
                checkpoint = budget_pg_store.get_checkpoint("budget", scope)
            else:
                from vnext_store import get_checkpoint
                checkpoint = get_checkpoint("budget", scope)
            budget_complete = bool(
                checkpoint
                and str(checkpoint.get("status") or "").upper() == "COMPLETE"
            )
        except Exception:
            budget_complete = False

    return {
        "fiscal_year": year,
        "shopping_complete": shopping_complete,
        "shopping_expected_days": expected_days,
        "shopping_complete_days": len(completed_days),
        "budget_complete": budget_complete,
        "source_complete": shopping_complete and budget_complete,
        "basis": "CURRENT_YEAR_DAILY_SHOPPING_PLUS_QWGJK_CHECKPOINT",
    }


def _classify_budget_population_row(row):
    import budget_normalizer_v41
    import budget_organization_vnext
    import classification_vnext

    item = dict(row)
    payload = budget_normalizer_v41.compat_payload("budget", item)
    classified = classification_vnext.classify_payload("budget", payload)
    item["raw_dataset"] = "budget"
    item["raw_source_key"] = str(item.get("record_key") or "")
    item["primary_category"] = str(
        classified.get("primary_category") or "UNCLASSIFIED"
    )
    item["subcategory"] = str(classified.get("subcategory") or "")
    item["project_identity"] = budget_organization_vnext._identity_from_fact(
        item,
        raw_source_key=item["raw_source_key"],
        source_operation=str(item.get("source_operation") or ""),
        source_system=str(item.get("source_system") or ""),
    )
    return item


def _full_budget_population_for_year(
    year,
    *,
    categories,
    region,
    max_source_rows=MAX_FULL_BUDGET_SOURCE_ROWS,
):
    import budget_storage

    selected = {str(value).upper() for value in categories}
    terms = budget_read_vnext._region_search_terms(region)
    result = []
    seen = set()
    offset = 0
    scanned = 0
    complete = False
    page_size = FULL_BUDGET_PAGE_SIZE

    while scanned < int(max_source_rows):
        request_size = min(page_size, int(max_source_rows) - scanned)
        if int(year) >= _kst_today().year:
            batch = budget_storage.current_normalized_rows(
                ("budget",),
                fiscal_year=int(year),
                source_layers=("DETAIL_EXECUTION",),
                region_terms=terms,
                limit=request_size,
                offset=offset,
            )
        else:
            batch = budget_storage.revision_project_rows(
                "budget",
                start_date=f"{int(year):04d}-01-01",
                end_date=f"{int(year):04d}-12-31",
                region_terms=terms,
                limit=request_size,
                offset=offset,
            )

        batch = list(batch or [])
        if not batch:
            complete = True
            break
        scanned += len(batch)
        offset += len(batch)

        for raw in batch:
            item = _classify_budget_population_row(raw)
            if str(item.get("source_layer") or "") != "DETAIL_EXECUTION":
                continue
            if region and not budget_read_vnext.region_matches(item, region):
                continue
            if str(item.get("primary_category") or "").upper() not in selected:
                continue
            identity = str(
                item.get("project_identity")
                or item.get("raw_source_key")
                or ""
            )
            if not identity or identity in seen:
                continue
            seen.add(identity)
            result.append(item)

        if len(batch) < request_size:
            complete = True
            break

    return result, complete, scanned


def _full_shopping_population_for_year(
    year,
    *,
    categories,
    region,
    max_rows=MAX_FULL_SHOPPING_ROWS,
):
    detail_rows = []
    offset = 0
    complete = False
    while offset < int(max_rows):
        request_size = min(
            FULL_SHOPPING_PAGE_SIZE,
            int(max_rows) - offset,
        )
        batch = procurement_read_vnext.shopping_rows(
            categories=categories,
            region=region,
            start_date=f"{int(year):04d}-01-01",
            end_date=f"{int(year):04d}-12-31",
            limit=request_size,
            offset=offset,
        )
        batch = list(batch or [])
        if not batch:
            complete = True
            break
        detail_rows.extend(batch)
        offset += len(batch)
        if len(batch) < request_size:
            complete = True
            break

    requests = procurement_read_vnext.shopping_request_rows_from_rows(
        detail_rows
    )
    return requests, complete, len(detail_rows)

def _budget_rows_for_year(year, *, categories, region, limit):
    size = max(1, min(int(limit), 1000))
    if int(year) >= dt.date.today().year:
        return budget_read_vnext.screen_budget_rows(
            fiscal_year=int(year),
            source_layers=("DETAIL_EXECUTION",),
            categories=categories,
            region=region,
            limit=size,
        )

    revisions = budget_read_vnext.qwgjk_history_rows(
        start_date=f"{int(year):04d}-01-01",
        end_date=f"{int(year):04d}-12-31",
        region=region,
        categories=categories,
        limit=5000,
        allow_match_backfill=True,
    )
    latest = {}
    for row in revisions:
        identity = str(
            row.get("project_identity")
            or row.get("raw_source_key")
            or row.get("record_key")
            or ""
        )
        if not identity or identity in latest:
            continue
        latest[identity] = row
        if len(latest) >= size:
            break
    return list(latest.values())


def _compact_budget_project(row, fiscal_year):
    return {
        "budget_project_identity": str(row.get("project_identity") or ""),
        "budget_raw_source_key": str(
            row.get("raw_source_key")
            or row.get("record_key")
            or ""
        ),
        "fiscal_year": int(fiscal_year),
        "budget_region": str(row.get("region_name") or ""),
        "budget_org": str(row.get("org_name") or ""),
        "budget_dept": str(row.get("dept_name") or ""),
        "budget_project_code": str(row.get("project_code") or ""),
        "budget_project_name": str(row.get("project_name") or ""),
        "budget_category": str(row.get("primary_category") or ""),
        "budget_amount": int(
            row.get("budget_amount")
            or row.get("appropriation_amount")
            or 0
        ),
        "budget_source_date": str(
            row.get("source_date")
            or row.get("snapshot_date")
            or ""
        ),
    }


def _match_project_key(row):
    return (
        str(row.get("budget_project_identity") or "").strip()
        or str(row.get("budget_raw_source_key") or "").strip()
    )


def _shopping_assignment_key(row):
    source_key = str(row.get("shopping_source_key") or "").strip()
    if source_key:
        return ("SOURCE", source_key)
    return (
        "FALLBACK",
        str(row.get("shopping_date") or ""),
        str(row.get("shopping_org") or ""),
        str(row.get("shopping_category") or ""),
        str(row.get("shopping_item") or ""),
        str(row.get("shopping_delivery_name") or ""),
        str(row.get("shopping_vendor") or ""),
        int(row.get("shopping_amount") or 0),
    )


def _assignment_rank(row):
    budget_amount = int(row.get("budget_amount") or 0)
    shopping_amount = int(row.get("shopping_amount") or 0)
    amount_gap = (
        abs(budget_amount - shopping_amount)
        if budget_amount > 0 and shopping_amount > 0
        else 10**30
    )
    lag = row.get("lag_days")
    lag_value = int(lag) if lag is not None else None
    return (
        -int(row.get("score") or 0),
        -(
            1
            if str(row.get("organization_basis") or "") == "EXACT_ORG_NAME"
            else 0
        ),
        -len(row.get("shared_signals") or []),
        -len(row.get("shared_tokens") or []),
        amount_gap,
        0 if lag_value is not None and lag_value >= 0 else 1,
        abs(lag_value) if lag_value is not None else 10**9,
        _match_project_key(row),
    )


def _unique_shopping_assignments(rows):
    """Conservatively attribute one actual shopping row to one budget project."""
    winners = {}
    for row in rows:
        key = _shopping_assignment_key(row)
        current = winners.get(key)
        if current is None or _assignment_rank(row) < _assignment_rank(current):
            winners[key] = row
    return list(winners.values())


def _limit_assignments_per_project(rows, limit):
    size = max(1, min(int(limit), 5))
    grouped = {}
    for row in rows:
        grouped.setdefault(_match_project_key(row), []).append(row)

    result = []
    for project_rows in grouped.values():
        project_rows.sort(
            key=lambda row: (
                int(row.get("score") or 0),
                int(row.get("shopping_amount") or 0),
                str(row.get("shopping_date") or ""),
            ),
            reverse=True,
        )
        result.extend(project_rows[:size])
    return result


def historical_match_rows(
    *,
    fiscal_year,
    region="",
    categories=DEFAULT_CATEGORIES,
    budget_limit=300,
    shopping_limit=3000,
    candidates_per_project=3,
    full_population=False,
):
    """Compare stored QWGJK projects with stored LED/pole procurement deliveries."""
    year = int(fiscal_year)
    selected = tuple(
        value for value in categories
        if str(value).upper() in DEFAULT_CATEGORIES
    ) or DEFAULT_CATEGORIES

    coverage = source_population_coverage(year)
    use_full_population = bool(
        full_population and coverage.get("source_complete")
    )
    budget_scan_complete = False
    shopping_scan_complete = False
    budget_source_rows_scanned = 0
    shopping_detail_rows_scanned = 0

    if use_full_population:
        budgets, budget_scan_complete, budget_source_rows_scanned = (
            _full_budget_population_for_year(
                year,
                categories=selected,
                region=region,
            )
        )
        (
            shopping,
            shopping_scan_complete,
            shopping_detail_rows_scanned,
        ) = _full_shopping_population_for_year(
            year,
            categories=selected,
            region=region,
        )
    else:
        budgets = _budget_rows_for_year(
            year,
            categories=selected,
            region=region,
            limit=budget_limit,
        )
        shopping_detail_rows = procurement_read_vnext.shopping_rows(
            categories=selected,
            region=region,
            start_date=f"{year:04d}-01-01",
            end_date=f"{year:04d}-12-31",
            limit=max(1, min(int(shopping_limit), 5000)),
        )
        shopping_detail_rows_scanned = len(shopping_detail_rows)
        shopping = procurement_read_vnext.shopping_request_rows_from_rows(
            shopping_detail_rows
        )
        budget_source_rows_scanned = len(budgets)

    match_population_complete = bool(
        use_full_population
        and budget_scan_complete
        and shopping_scan_complete
    )
    by_org = _shopping_index(shopping)

    candidate_rows = []
    for budget in budgets:
        candidates = {}
        for alias in _org_aliases(
            budget.get("org_name"),
            budget.get("region_name"),
        ):
            for shop in by_org.get(alias, ()):
                candidates[str(shop.get("source_key") or id(shop))] = shop

        for shop in candidates.values():
            match = score_pair(budget, shop)
            if match is None:
                continue
            if str(shop.get("delivery_req_no") or "").strip():
                match = dict(match)
                match["evidence"] = list(match.get("evidence") or []) + [
                    "REQUEST_LEVEL_SUM_LATEST_DETAIL_AMOUNTS"
                ]
            candidate_rows.append({
                "budget_raw_source_key": str(
                    budget.get("raw_source_key")
                    or budget.get("record_key")
                    or ""
                ),
                "budget_project_identity": str(
                    budget.get("project_identity") or ""
                ),
                "fiscal_year": year,
                "budget_region": str(
                    budget.get("region_name") or ""
                ),
                "budget_org": str(budget.get("org_name") or ""),
                "budget_dept": str(budget.get("dept_name") or ""),
                "budget_project_code": str(
                    budget.get("project_code") or ""
                ),
                "budget_project_name": str(
                    budget.get("project_name") or ""
                ),
                "budget_category": str(
                    budget.get("primary_category") or ""
                ),
                "budget_amount": int(
                    budget.get("budget_amount")
                    or budget.get("appropriation_amount")
                    or 0
                ),
                "budget_source_date": str(
                    budget.get("source_date")
                    or budget.get("snapshot_date")
                    or ""
                ),
                "shopping_source_key": str(
                    shop.get("source_key") or ""
                ),
                "shopping_delivery_req_no": str(
                    shop.get("delivery_req_no") or ""
                ),
                "shopping_date": str(shop.get("source_date") or ""),
                "shopping_org": str(shop.get("demand_org") or ""),
                "shopping_category": ",".join(
                    sorted(_shopping_categories(shop))
                ),
                "shopping_item": str(
                    shop.get("item_name")
                    or shop.get("detail_item_name")
                    or ""
                ),
                "shopping_delivery_name": str(
                    shop.get("delivery_req_name") or ""
                ),
                "shopping_vendor": str(shop.get("vendor_name") or ""),
                "shopping_amount": int(shop.get("amount") or 0),
                "shopping_detail_rows": int(
                    shop.get("request_detail_rows") or 1
                ),
                "shopping_amount_basis": str(
                    shop.get("amount_basis") or "DETAIL_ITEM_AMOUNT"
                ),
                **match,
            })

    assigned_rows = _unique_shopping_assignments(candidate_rows)
    result = _limit_assignments_per_project(
        assigned_rows,
        candidates_per_project,
    )

    result.sort(
        key=lambda row: (
            int(row["score"]),
            int(row["budget_amount"]),
            str(row["budget_project_name"]),
        ),
        reverse=True,
    )
    return {
        "fiscal_year": year,
        "region": str(region or ""),
        "categories": list(selected),
        "budget_projects_scanned": len(budgets),
        "budget_source_rows_scanned": int(budget_source_rows_scanned),
        "shopping_rows_scanned": len(shopping),
        "shopping_requests_scanned": len(shopping),
        "shopping_detail_rows_scanned": int(shopping_detail_rows_scanned),
        "shopping_rows_assigned": len(result),
        "shopping_requests_assigned": len(result),
        "shopping_assignment_semantics": "ONE_DELIVERY_REQUEST_TO_ONE_BUDGET_PROJECT",
        "shopping_amount_semantics": "SUM_LATEST_TARGET_DETAIL_ITEM_AMOUNT",
        "source_population_coverage": coverage,
        "budget_population_scan_complete": bool(budget_scan_complete),
        "shopping_population_scan_complete": bool(shopping_scan_complete),
        "match_population_complete": match_population_complete,
        "budget_projects": [
            _compact_budget_project(row, year)
            for row in budgets
        ],
        "matches": result,
        "source_traffic": False,
        "relation_semantics": "CANDIDATE_EVIDENCE_NOT_FUNDING_PROOF",
    }


def historical_match_summary(**kwargs):
    payload = historical_match_rows(**kwargs)
    rows = payload["matches"]
    high = [row for row in rows if row["level"] == "HIGH"]
    candidate = [row for row in rows if row["level"] == "CANDIDATE"]
    high_projects = {
        row["budget_project_identity"] or row["budget_raw_source_key"]
        for row in high
    }
    matched_projects = {
        row["budget_project_identity"] or row["budget_raw_source_key"]
        for row in rows
    }
    high_shop_keys = {row["shopping_source_key"] for row in high}
    high_amount = sum(
        max(
            int(row["shopping_amount"])
            for row in high
            if row["shopping_source_key"] == key
        )
        for key in high_shop_keys
    ) if high_shop_keys else 0

    budget_count = int(payload["budget_projects_scanned"])
    enough = (
        budget_count >= MIN_PROJECT_SAMPLE
        and len(high_projects) >= MIN_HIGH_MATCHED_PROJECTS
    )
    reasons = []
    if budget_count < MIN_PROJECT_SAMPLE:
        reasons.append("BUDGET_PROJECT_SAMPLE_SMALL")
    if len(high_projects) < MIN_HIGH_MATCHED_PROJECTS:
        reasons.append("HIGH_MATCH_SAMPLE_SMALL")

    return {
        **payload,
        "high_matches": len(high),
        "candidate_matches": len(candidate),
        "matched_budget_projects": len(matched_projects),
        "high_matched_budget_projects": len(high_projects),
        "high_matched_shopping_amount": high_amount,
        "project_match_rate": (
            round(len(matched_projects) / budget_count, 4)
            if budget_count else 0.0
        ),
        "evidence_sufficient_for_pattern_learning": enough,
        "expand_2025_recommended": not enough,
        "expansion_reasons": reasons,
        "sufficiency_thresholds": {
            "minimum_budget_projects": MIN_PROJECT_SAMPLE,
            "minimum_high_matched_projects": MIN_HIGH_MATCHED_PROJECTS,
        },
    }
