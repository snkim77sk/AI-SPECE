"""Budget keyword expansion and expected G2B detail-item mapping.

This patch is intentionally isolated from the procurement collector/scheduler.
It enriches 지방재정365 budget rows with expected G2B detail-item numbers based
on project-name terminology. Generic terms such as '조명' remain candidates,
not false certainty.
"""


def apply_budget_item_mapping():
    import budget_sync as bs
    import server as s
    import sinsung_budget_monitor as bm

    if getattr(bs, "_budget_item_mapping_applied", False):
        return

    # Keep API search terms compact. Internal matching below handles wider aliases.
    bs.TARGET_KEYWORDS = (
        "조명", "LED", "가로등", "보안등", "터널등", "실내조명",
        "경관조명", "투광등", "다운라이트", "태양광", "분전반", "가로등주",
    )

    # 10-digit G2B detail-item numbers already used by this program.
    LED_CANDIDATES = (
        ("3911151502", "LED다운라이트"),
        ("3911160302", "LED가로등기구"),
        ("3911160304", "LED터널등기구"),
        ("3911160501", "LED경관조명기구"),
        ("3911160802", "LED보안등기구"),
        ("3911161102", "LED투광등기구"),
        ("3911210201", "LED실내조명등"),
    )
    POLE_CANDIDATES = (
        ("3911152601", "철제가로등주"),
        ("3911152602", "스테인리스가로등주"),
        ("3911152607", "가로등주부속자재"),
    )

    def _candidate_text(items):
        return ",".join(code for code, _ in items), "/".join(name for _, name in items)

    def map_project(project_name):
        raw = str(project_name or "").strip()
        text = raw.casefold()
        compact = "".join(text.split())

        # Pole material can be identified more specifically than a generic pole project.
        if ("가로등주" in compact or "등주" in compact) and any(x in compact for x in ("스테인리스", "sus", "스텐")):
            return {"category": "가로등주", "detail_item_no": "3911152602", "detail_item_name": "스테인리스가로등주", "match_confidence": "높음", "matched_term": "스테인리스 가로등주"}
        if ("가로등주" in compact or "등주" in compact) and any(x in compact for x in ("철제", "steel")):
            return {"category": "가로등주", "detail_item_no": "3911152601", "detail_item_name": "철제가로등주", "match_confidence": "높음", "matched_term": "철제 가로등주"}
        if any(x in compact for x in ("가로등주부속", "등주부속", "암대", "브라켓")) and ("등주" in compact or "가로등" in compact):
            return {"category": "가로등주", "detail_item_no": "3911152607", "detail_item_name": "가로등주부속자재", "match_confidence": "높음", "matched_term": "가로등주 부속"}

        rules = (
            ("다운라이트", "3911151502", "LED다운라이트", "높음", ("다운라이트", "다운라이트등", "downlight")),
            ("터널등", "3911160304", "LED터널등기구", "높음", ("터널등", "터널조명", "터널조명등")),
            ("보안등", "3911160802", "LED보안등기구", "높음", ("보안등", "방범등", "방범조명")),
            ("경관조명", "3911160501", "LED경관조명기구", "높음", ("경관조명", "경관등", "경관조명등")),
            ("투광등", "3911161102", "LED투광등기구", "높음", ("투광등", "투광조명", "투광조명등")),
            ("실내조명", "3911210201", "LED실내조명등", "높음", ("실내조명", "실내등", "면조명", "평판등", "평판조명", "패널조명")),
            ("가로등", "3911160302", "LED가로등기구", "높음", ("가로등", "가로등기구")),
            ("가로등", "3911160302", "LED가로등기구", "중간", ("도로조명", "도로등")),
            ("분전반", "3912110101", "분전반", "높음", ("분전반", "분전함")),
            ("태양광", "2611160701", "태양광발전장치", "높음", ("태양광발전", "태양광")),
        )
        for category, code, name, confidence, aliases in rules:
            for alias in aliases:
                if "".join(alias.casefold().split()) in compact:
                    return {"category": category, "detail_item_no": code, "detail_item_name": name, "match_confidence": confidence, "matched_term": alias}

        if "가로등주" in compact or "등주" in compact:
            codes, names = _candidate_text(POLE_CANDIDATES)
            return {"category": "가로등주", "detail_item_no": codes, "detail_item_name": names, "match_confidence": "낮음", "matched_term": "가로등주/등주"}

        # Generic lighting language is useful for discovery, but not enough to
        # claim one specific G2B item number. Keep all seven lighting candidates.
        if "조명" in compact or "led" in compact or "등기구" in compact:
            codes, names = _candidate_text(LED_CANDIDATES)
            return {"category": "조명", "detail_item_no": codes, "detail_item_name": names, "match_confidence": "낮음", "matched_term": "조명/LED/등기구"}
        return None

    bs.map_budget_project = map_project

    original_ensure = bs.ensure_budget_schema

    def ensure_budget_schema():
        original_ensure()
        additions = {
            "detail_item_no": "TEXT NOT NULL DEFAULT ''",
            "detail_item_name": "TEXT NOT NULL DEFAULT ''",
            "match_confidence": "TEXT NOT NULL DEFAULT ''",
            "matched_term": "TEXT NOT NULL DEFAULT ''",
        }
        with bs.connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(budget_items)")}
            for name, definition in additions.items():
                if name not in cols:
                    conn.execute(f"ALTER TABLE budget_items ADD COLUMN {name} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_budget_detail_item_no ON budget_items(detail_item_no)")
        return True

    bs.ensure_budget_schema = ensure_budget_schema

    def classify_project(project_name):
        mapped = map_project(project_name)
        return mapped["category"] if mapped else ""

    bs.classify_project = classify_project
    original_normalize = bs.normalize_budget_row

    def normalize_budget_row(raw, matched_keyword, snapshot_date):
        item = original_normalize(raw, matched_keyword, snapshot_date)
        if not item:
            return None
        mapped = map_project(item.get("project_name", ""))
        if not mapped:
            return None
        item["category"] = mapped["category"]
        item.update({k: mapped[k] for k in ("detail_item_no", "detail_item_name", "match_confidence", "matched_term")})
        return item

    bs.normalize_budget_row = normalize_budget_row

    def upsert_budget_rows(rows, matched_keyword, snapshot_date, seen_keys=None):
        seen_keys = seen_keys if seen_keys is not None else set()
        saved = 0
        with bs.connect() as conn:
            for raw in rows:
                item = bs.normalize_budget_row(raw, matched_keyword, snapshot_date)
                if not item or not item["fiscal_year"] or not item["org_name"] or not item["project_name"]:
                    continue
                if item["source_key"] in seen_keys:
                    continue
                seen_keys.add(item["source_key"])
                conn.execute(
                    """
                    INSERT INTO budget_items(
                        fiscal_year,region,org_name,project_name,category,budget_amount,status,source,is_sample,
                        executed_amount,remaining_amount,source_date,source_key,matched_keyword,field_name,
                        account_name,org_code,project_code,detail_item_no,detail_item_name,match_confidence,
                        matched_term,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(source_key) WHERE source_key <> '' DO UPDATE SET
                        fiscal_year=excluded.fiscal_year,region=excluded.region,org_name=excluded.org_name,
                        project_name=excluded.project_name,category=excluded.category,budget_amount=excluded.budget_amount,
                        status=excluded.status,source=excluded.source,is_sample=0,
                        executed_amount=excluded.executed_amount,remaining_amount=excluded.remaining_amount,
                        source_date=excluded.source_date,matched_keyword=excluded.matched_keyword,
                        field_name=excluded.field_name,account_name=excluded.account_name,
                        org_code=excluded.org_code,project_code=excluded.project_code,
                        detail_item_no=excluded.detail_item_no,detail_item_name=excluded.detail_item_name,
                        match_confidence=excluded.match_confidence,matched_term=excluded.matched_term,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        item["fiscal_year"], item["region"], item["org_name"], item["project_name"], item["category"],
                        item["budget_amount"], item["status"], item["source"], item["executed_amount"],
                        item["remaining_amount"], item["source_date"], item["source_key"], item["matched_keyword"],
                        item["field_name"], item["account_name"], item["org_code"], item["project_code"],
                        item["detail_item_no"], item["detail_item_name"], item["match_confidence"], item["matched_term"],
                    ),
                )
                saved += 1
        return saved

    bs.upsert_budget_rows = upsert_budget_rows
    ensure_budget_schema()

    # Expose the additional categories in the existing budget filter.
    bm.CATEGORIES = (
        "", "LED조명", "조명", "다운라이트", "가로등", "가로등주", "터널등",
        "보안등", "경관조명", "투광등", "실내조명", "태양광", "분전반",
    )

    def budget_where(year, region, category, status, q):
        clauses = ["fiscal_year=?", "is_sample=0"]
        vals = [year]
        if region:
            clauses.append("region=?")
            vals.append(region)
        if category:
            clauses.append("category=?")
            vals.append(category)
        if status != "all":
            clauses.append("status=?")
            vals.append(status)
        if q:
            like = f"%{q}%"
            clauses.append(
                "(org_name LIKE ? OR project_name LIKE ? OR category LIKE ? OR field_name LIKE ? "
                "OR detail_item_no LIKE ? OR detail_item_name LIKE ? OR matched_term LIKE ?)"
            )
            vals += [like, like, like, like, like, like, like]
        return " AND ".join(clauses), vals

    bm._budget_where = budget_where

    # Add the expected item number to the existing budget table without replacing
    # the stable page implementation.
    original_budgets_html = s.budgets_html

    def budgets_html(qs):
        page = original_budgets_html(qs)
        year, region, category, status, q = bm._budget_filters(s, qs)
        where, vals = bm._budget_where(year, region, category, status, q)
        rows = s.qrows(
            f"SELECT * FROM budget_items WHERE {where} ORDER BY remaining_amount DESC,budget_amount DESC,id DESC LIMIT 2000",
            vals,
        )
        page = page.replace(
            "<th>분류</th><th>예산현액</th>",
            "<th>분류</th><th>예상 세부품명번호</th><th>예산현액</th>",
            1,
        )
        for row in rows:
            old = f'<td>{s.esc(row["category"])}</td><td class="num">{s.money(row["budget_amount"])}</td>'
            code = s.esc(row["detail_item_no"] or "-")
            name = s.esc(row["detail_item_name"] or "품목 미확정")
            confidence = s.esc(row["match_confidence"] or "-")
            new = (
                f'<td>{s.esc(row["category"])}</td>'
                f'<td><b>{code}</b><small>{name} · 신뢰도 {confidence}</small></td>'
                f'<td class="num">{s.money(row["budget_amount"])}</td>'
            )
            page = page.replace(old, new, 1)
        page = page.replace('colspan="10" class="empty"', 'colspan="11" class="empty"', 1)
        old_target = "대상: LED, 조명, 가로등, 보안등, 경관조명, 투광등, 실내조명, 태양광, 분전반, 가로등주."
        new_target = "대상: 조명, LED, 다운라이트, 가로등, 보안등, 터널등, 경관조명, 투광등, 실내조명, 태양광, 분전반, 가로등주 및 유사 명칭."
        page = page.replace(old_target, new_target, 1)
        return page

    s.budgets_html = budgets_html
    bs._budget_item_mapping_applied = True
    return bs
