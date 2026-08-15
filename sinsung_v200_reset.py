"""One-time 2.0 data reset.

Deletes collected business data and collection runtime state exactly once while
preserving users, company configuration, API keys and the persistent session secret.
"""
from db import connect, get_setting, set_setting

RESET_MARKER = "v200_clean_data_reset_completed"


def reset_data_once():
    if get_setting(RESET_MARKER, "") == "1":
        return False, get_setting("v200_reset_summary", "2.0 데이터 초기화 완료")

    with connect() as conn:
        counts = {}
        for table in ("shopping_contracts", "bids", "budget_items", "sync_logs"):
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = int(row[0] if row else 0)
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                counts[table] = 0

        try:
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('shopping_contracts','bids','budget_items','sync_logs')"
            )
        except Exception:
            pass

        # Remove collection/runtime diagnostics only. Security, users, company,
        # API credentials and the internal session secret are preserved.
        legacy_prefixes = (
            "last_",
            "backfill_",
            "bg_sync_",
            "shop_snapshot_",
            "shop_history_",
            "shop_probe_",
            "shop_date_",
            "shop_param_",
            "shop_specific_",
            "shop_2h_",
            "api_calls_",
            "budget_sync_",
        )
        rows = conn.execute("SELECT key FROM app_settings").fetchall()
        for row in rows:
            key = str(row["key"])
            if key.startswith(legacy_prefixes):
                conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
                continue
            if key.startswith(("v253_", "v254_", "v255_", "v256_", "v257_", "v258_", "v259_", "v260_", "v261_", "v262_", "v263_")):
                conn.execute("DELETE FROM app_settings WHERE key=?", (key,))

    # Clean official endpoints. API credentials remain untouched.
    set_setting("shop_api_base_url", "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService")
    set_setting("shop_api_operation", "getDlvrReqDtlInfoList")
    set_setting("bid_api_base_url", "https://apis.data.go.kr/1230000/ad/BidPublicInfoService")

    # 2.0 starts with manual verification only; prevent immediate repopulation.
    set_setting("auto_sync_enabled", "0")
    set_setting("budget_auto_sync_enabled", "0")
    set_setting("shop_request_profile", "")

    for key, value in {
        "last_sync": "",
        "last_sync_result": "2.0 초기화 완료 · 최근 쇼핑몰 실데이터 수집 검증 대기",
        "last_shop_raw_count": "0",
        "last_shop_matched_count": "0",
        "last_shop_saved_count": "0",
        "last_shop_skipped_count": "0",
        "last_shop_first_fields": "",
        "last_shop_error": "",
        "last_bid_sync": "",
        "last_bid_sync_result": "2.0 초기화 완료 · 물품 입찰공고 수집 대기",
        "last_service_sync": "",
        "last_service_sync_result": "2.0 초기화 완료 · 용역공고 수집 대기",
        "backfill_status": "비활성",
        "backfill_progress": "0",
        "backfill_message": "2.0에서는 과거자료 구축을 비활성화합니다.",
        "api_calls_shop_date": "",
        "api_calls_shop_count": "0",
        "api_calls_bid_date": "",
        "api_calls_bid_count": "0",
        "budget_sync_status": "대기",
        "last_budget_sync_result": "2.0 초기화 완료 · 예산 실데이터 수집 대기",
    }.items():
        set_setting(key, value)

    summary = (
        f"2.0 1회 초기화 · 쇼핑몰 {counts.get('shopping_contracts', 0):,}건 / "
        f"입찰·용역 {counts.get('bids', 0):,}건 / 예산 {counts.get('budget_items', 0):,}건 / "
        f"수집로그 {counts.get('sync_logs', 0):,}건 삭제"
    )
    set_setting("v200_reset_summary", summary)
    set_setting(RESET_MARKER, "1")
    return True, summary
