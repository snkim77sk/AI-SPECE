"""SINSUNG G2B v2.4.1 runtime stabilization.

This module keeps the flattened AI SPACE deployment layout while correcting
production shopping API aliases, exact detail-item groups, branding, and the
one-time cleanup required after the earlier duplicated-amount import.
"""
import hashlib

VERSION = "2.4.1-sinsung-stable"

LED_DETAIL_ITEM_NOS = frozenset({
    "3911151502",  # LED다운라이트
    "3911160302",  # LED가로등기구
    "3911160304",  # LED터널등기구
    "3911160501",  # LED경관조명기구
    "3911160802",  # LED보안등기구
    "3911161102",  # LED투광등기구
    "3911210201",  # LED실내조명등
})
SOLAR_PANEL_DETAIL_ITEM_NOS = frozenset({
    "2611160701",  # 태양광발전장치
    "3912110101",  # 분전반
})
POLE_DETAIL_ITEM_NOS = frozenset({
    "3911152601",  # 철제가로등주
    "3911152602",  # 스테인리스가로등주
    "3911152607",  # 가로등주부속자재
})
SHOP_DETAIL_ITEM_NOS = LED_DETAIL_ITEM_NOS | SOLAR_PANEL_DETAIL_ITEM_NOS | POLE_DETAIL_ITEM_NOS

GROUPS = {
    "led": ("LED 조명 조달내역", tuple(sorted(LED_DETAIL_ITEM_NOS))),
    "solar": ("태양광/분전함 조달내역", tuple(sorted(SOLAR_PANEL_DETAIL_ITEM_NOS))),
    "pole": ("등주 조달내역", tuple(sorted(POLE_DETAIL_ITEM_NOS))),
}


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def prepare_database_once():
    """Initialize schema, clear known-bad shopping rows once, enable auto sync once.

    Only shopping_contracts is cleared. API keys, settings, users, bids/services,
    budgets and sync history are preserved.
    """
    from db import connect, get_setting, init_db, set_setting

    init_db()
    reset_marker = "v241_bad_shopping_rows_reset"
    if get_setting(reset_marker, "") != "1":
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM shopping_contracts").fetchone()
            removed = int(row[0] if row else 0)
            conn.execute("DELETE FROM shopping_contracts")
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name='shopping_contracts'")
            except Exception:
                pass
        for key, value in {
            "last_sync": "",
            "last_shop_raw_count": "0",
            "last_shop_matched_count": "0",
            "last_shop_saved_count": "0",
            "last_shop_skipped_count": "0",
            "last_shop_first_fields": "",
            "last_shop_error": "",
            "last_sync_result": f"v2.4.1 데이터 구조 정리: 기존 쇼핑몰 {removed:,}건 초기화 완료 · 재수집 필요",
        }.items():
            set_setting(key, value)
        set_setting(reset_marker, "1")

    auto_marker = "v241_auto_sync_initialized"
    if get_setting(auto_marker, "") != "1":
        set_setting("auto_sync_enabled", "1")
        set_setting(auto_marker, "1")


def _normalize_shop_item(d):
    """Normalize the live ShoppingMallPrdctInfoService delivery-detail payload."""
    import g2b_sync as g

    contract_date = g._date(g._pick(
        d,
        "cntrctDt", "contractDt", "contractDate",
        "IntlCntrctDlvrReqDate", "intlCntrctDlvrReqDate", "intllCntrctDlvrReqDate",
    ))
    delivery_req_date = g._date(g._pick(
        d,
        "dlvrReqRcptDate", "dlvrReqDt", "deliveryReqDt", "reqDt", "dlvrReqDate",
        "IntlCntrctDlvrReqDate", "intlCntrctDlvrReqDate", "intllCntrctDlvrReqDate",
    ))
    base_date = delivery_req_date or contract_date or g._date(g._pick(d, "baseDt"))

    demand_org = g._pick(d, "dminsttNm", "demandInsttNm", "demandOrgNm", "orderInsttNm", "insttNm")
    direct_region = g._pick(d, "dminsttRgnNm", "demandRegionNm", "rgnNm", "regionName")
    address = g._pick(d, "dminsttAddr", "demandInsttAddr", "dlvrDstnAddr", "addr")
    vendor = g._pick(d, "corpNm", "cntrctCorpNm", "entrpsNm", "vendorNm", "supplierNm", "cntrctCorpName")

    detail_item_no = _digits(g._pick(
        d, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo", "prdctClsfcNo"
    ))
    detail_item_name = g._pick(
        d, "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm",
        "detailItemName", "prdctClsfcNoNm", "prdctClsfcNm"
    )
    item_id = _digits(g._pick(d, "prdctIdntNo", "goodsIdntNo", "itemId", "identificationNo"))
    item_name = g._pick(d, "prdctIdntNoNm", "prdctIdntNm", "goodsIdntNm", "itemName", "prdctNm")
    model_name = g._pick(d, "modelNm", "modelName", "goodsModelNm", "prdctSpecNm", "specNm")
    unit = g._pick(d, "prdctUnit", "unit", "unitNm", "dlvrUnit")

    unit_price = int(round(g._num(g._pick(
        d, "prdctUprc", "unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc"
    ))))
    quantity = g._num(g._pick(d, "prdctQty", "dlvrReqQty", "reqQty", "quantity", "qty"))

    # Never treat repeated delivery-request total as the line amount when the
    # actual line price and quantity are available.
    calculated_amount = int(round(unit_price * quantity)) if unit_price and quantity else 0
    line_amount = int(round(g._num(g._pick(
        d, "prdctAmt", "supplyAmount", "amount", "dlvrAmt", "dlvrReqAmt", "reqAmt"
    ))))
    amount = calculated_amount or line_amount

    contract_name = g._pick(d, "dlvrReqNm", "cntrctNm", "contractNm", "deliveryReqNm", "bizNm", "dlvrReqSj")
    contract_no = str(g._pick(d, "cntrctNo", "contractNo"))
    delivery_req_no = str(g._pick(d, "dlvrReqNo", "deliveryReqNo", "reqNo"))
    detail_seq = str(g._pick(
        d, "prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "dlvrReqSeq", "dlvrReqChgOrd", "detailSeq", "seq"
    ))
    bizno = str(g._pick(d, "cntrctCorpBizno", "corpBizno", "bizno", "bizrno"))
    final_yn = str(g._pick(d, "fnlDlvrReqYn", "lastDlvrReqYn", "finalDlvrReqYn", "finalYn", "lastYn", "fnlYn"))
    contract_method = g._pick(
        d, "cntrctCnclsStleNm", "cntrctMthdNm", "contractMthdNm", "contractMethodNm", "cntrctMthd"
    )
    delivery_deadline = g._date(g._pick(
        d, "dlvrTmlmtDate", "dlvrTmlmtDt", "deliveryDeadline", "dlvrDueDt", "deliveryDueDate"
    ))

    if delivery_req_no:
        rawkey = "|".join(["DLVR", delivery_req_no, detail_seq, item_id, contract_no, vendor])
    else:
        rawkey = "|".join(["FALLBACK", base_date, demand_org, vendor, item_id, contract_no, contract_name])

    return {
        "base_date": base_date,
        "contract_date": contract_date,
        "delivery_req_date": delivery_req_date,
        "final_yn": final_yn,
        "demand_org": demand_org,
        "demand_region": g.infer_region(demand_org, address, direct_region),
        "top_org": g.normalize_top_org(demand_org),
        "contract_name": contract_name,
        "contract_method": contract_method,
        "delivery_deadline": delivery_deadline,
        "detail_item_no": detail_item_no,
        "detail_item_name": detail_item_name,
        "item_id": item_id,
        "item_name": item_name,
        "model_name": model_name,
        "unit": unit,
        "unit_price": unit_price,
        "quantity": quantity,
        "supply_amount": amount,
        "vendor_name": vendor,
        "vendor_bizno": bizno,
        "contract_no": contract_no,
        "delivery_req_no": delivery_req_no,
        "delivery_req_detail_seq": detail_seq,
        "source_key": hashlib.sha1(rawkey.encode("utf-8")).hexdigest(),
    }


def patch_g2b_sync():
    import g2b_sync as g

    g.LED_DETAIL_ITEM_NOS = LED_DETAIL_ITEM_NOS
    g.SOLAR_PANEL_DETAIL_ITEM_NOS = SOLAR_PANEL_DETAIL_ITEM_NOS
    g.POLE_DETAIL_ITEM_NOS = POLE_DETAIL_ITEM_NOS
    g.SHOP_DETAIL_ITEM_NOS = SHOP_DETAIL_ITEM_NOS
    g.normalize_shop_item = _normalize_shop_item

    original_sync = g.sync_shopping_period

    def sync_shopping_period(start_date, end_date, max_pages=2000):
        result = original_sync(start_date, end_date, max_pages=max_pages)
        text = g.get_setting("last_sync_result", "")
        if "조명 대상" in text:
            g.set_setting("last_sync_result", text.replace("조명 대상", "세부품명번호 대상"))
        return result

    g.sync_shopping_period = sync_shopping_period
    return g


def _auth_css():
    return """
<style>
*{box-sizing:border-box}html,body{min-height:100%}body{margin:0;background:#050b1b;color:#eef4ff;font-family:Arial,'Noto Sans KR','Malgun Gothic',sans-serif}
.auth-shell{min-height:100vh;display:grid;place-items:center;padding:28px;background:radial-gradient(circle at 50% 15%,#10254a 0,#08142d 30%,#050b1b 68%)}
.auth-panel{width:min(480px,100%);padding:46px 42px 38px;border:1px solid #1b2b4b;border-radius:22px;background:linear-gradient(180deg,rgba(10,20,43,.96),rgba(5,12,29,.98));box-shadow:0 28px 80px #0009,0 0 0 1px #ffffff06 inset}
.sinsung-mark{text-align:center;font-size:48px;font-style:italic;font-weight:900;letter-spacing:-.06em;background:linear-gradient(135deg,#5bc8ff,#1578f2);-webkit-background-clip:text;color:transparent;text-shadow:0 0 28px #1686ff33;margin-bottom:5px}
.sinsung-sub{text-align:center;color:#7f98bf;font-size:12px;font-weight:800;letter-spacing:.28em;margin-bottom:34px}.auth-title{text-align:center;margin:0 0 7px;font-size:24px}.auth-desc{text-align:center;color:#8fa3c3;margin:0 0 28px;line-height:1.65}
.auth-form{display:grid;gap:16px}.auth-form label{display:grid;gap:8px;color:#dce7f9;font-size:13px;font-weight:700}.auth-form input{width:100%;height:48px;border:1px solid #263a5c;border-radius:10px;background:#071126;color:#fff;padding:0 14px;font-size:15px;outline:none}.auth-form input:focus{border-color:#278cff;box-shadow:0 0 0 3px #278cff1f}.password-wrap{position:relative}.password-wrap input{padding-right:56px}.eye{position:absolute;right:10px;top:7px;height:34px;border:0;background:transparent;color:#4da5ff;cursor:pointer;font-size:18px}.auth-button{height:52px;margin-top:6px;border:0;border-radius:12px;background:linear-gradient(90deg,#246ff3,#16b8dd);color:#fff;font-size:16px;font-weight:900;cursor:pointer;box-shadow:0 10px 28px #087bdb38}.auth-button:hover{filter:brightness(1.06)}.auth-note{text-align:center;color:#60779d;font-size:12px;margin-top:22px}.auth-error{background:#35141c;border:1px solid #73303d;color:#ffb8c3;padding:11px 13px;border-radius:9px;margin-bottom:16px;font-size:13px}
@media(max-width:560px){.auth-shell{padding:14px}.auth-panel{padding:36px 22px 30px;border-radius:16px}.sinsung-mark{font-size:40px}}
</style>
"""


def patch_server():
    import server as s

    s.APP_VERSION = VERSION
    s.GROUPS = GROUPS
    s.NAVS = [
        ("대시보드", "/dashboard"),
        ("LED 조명 조달내역", "/g2b/shopping/prdct_detail.php?group=led"),
        ("태양광/분전함 조달내역", "/g2b/shopping/prdct_detail.php?group=solar"),
        ("등주 조달내역", "/g2b/shopping/prdct_detail.php?group=pole"),
        ("용역현황", "/services"), ("업체별수주조회", "/vendors"),
        ("시장예측", "/market"), ("순위조회", "/ranking"),
        ("매출현황", "/sales"), ("우리제품", "/products"),
        ("입찰", "/bids"), ("예산", "/budgets"), ("연차관리", "/annual"),
    ]

    def login_html(error=""):
        err = f'<div class="auth-error">{s.esc(error)}</div>' if error else ""
        return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SINSUNG 로그인</title>{_auth_css()}</head><body><main class="auth-shell"><section class="auth-panel"><div class="sinsung-mark">SINSUNG</div><div class="sinsung-sub">G2B DATA VIEW</div><h1 class="auth-title">신성라이텍</h1><p class="auth-desc">관급조달 데이터 통합관리 시스템</p>{err}<form class="auth-form" method="post" action="/login"><label>아이디<input name="username" autocomplete="username" required></label><label>비밀번호<div class="password-wrap"><input id="login-password" type="password" name="password" autocomplete="current-password" required><button class="eye" type="button" aria-label="비밀번호 보기" onclick="var p=document.getElementById('login-password');p.type=p.type==='password'?'text':'password';this.textContent=p.type==='password'?'◉':'◎'">◉</button></div></label><button class="auth-button" type="submit">로그인</button></form><div class="auth-note">SINSUNG PROCUREMENT DATA SYSTEM</div></section></main></body></html>'''

    def setup_admin_html(error=""):
        err = f'<div class="auth-error">{s.esc(error)}</div>' if error else ""
        return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SINSUNG 최초 관리자 설정</title>{_auth_css()}</head><body><main class="auth-shell"><section class="auth-panel"><div class="sinsung-mark">SINSUNG</div><div class="sinsung-sub">FIRST ADMIN SETUP</div><h1 class="auth-title">최초 관리자 설정</h1><p class="auth-desc">기본 admin / 1234 계정은 사용하지 않습니다.<br>처음 사용할 관리자 계정을 직접 설정해 주세요.</p>{err}<form class="auth-form" method="post" action="/setup-admin"><label>관리자 아이디<input name="username" minlength="4" maxlength="50" autocomplete="username" required></label><label>비밀번호<input type="password" name="password" minlength="10" autocomplete="new-password" required></label><label>비밀번호 확인<input type="password" name="password_confirm" minlength="10" autocomplete="new-password" required></label><button class="auth-button" type="submit">관리자 계정 생성</button></form><div class="auth-note">비밀번호는 PBKDF2-SHA256 해시로 저장됩니다.</div></section></main></body></html>'''

    s.login_html = login_html
    s.setup_admin_html = setup_admin_html

    original_base = s.base_html
    def base_html(content, active="대시보드", flash="", flash_error=False):
        text = original_base(content, active, flash, flash_error)
        text = text.replace("LIGHTING SKETCH G2B DATA VIEW v2.3 REVIEWED", "SINSUNG · 신성라이텍 G2B DATA VIEW")
        text = text.replace("LIGHTING SKETCH / G2B DATA VIEW", "SINSUNG / G2B DATA VIEW")
        text = text.replace("lighting-sketch", "sinsung")
        return text
    s.base_html = base_html

    original_settings = s.settings_html
    def settings_html(msg="", error=False):
        text = original_settings(msg, error)
        text = text.replace("조명 대상", "세부품명번호 대상")
        text = text.replace("shopping_contracts 실데이터만 삭제합니다.", "shopping_contracts 조달데이터를 전부 삭제합니다.")
        return text
    s.settings_html = settings_html

    return s


def apply_runtime_fixes():
    patch_g2b_sync()
    return patch_server()
