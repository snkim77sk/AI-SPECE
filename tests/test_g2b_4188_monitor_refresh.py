import inspect

import vnext_clean_app


def test_collection_monitor_refresh_is_fast_only_while_work_is_active():
    assert vnext_clean_app._collection_monitor_refresh_seconds() == 30
    assert vnext_clean_app._collection_monitor_refresh_seconds(
        shopping_running=True
    ) == 5
    assert vnext_clean_app._collection_monitor_refresh_seconds(
        budget_running=True
    ) == 5
    assert vnext_clean_app._collection_monitor_refresh_seconds(
        match_backfill_running=True
    ) == 5


def test_collection_monitor_page_uses_dynamic_refresh_cadence():
    source = inspect.getsource(vnext_clean_app.collection_monitor_page)
    assert "monitor_refresh_seconds = _collection_monitor_refresh_seconds(" in source
    assert "refresh_seconds=monitor_refresh_seconds" in source
    assert "대기·완료·오류 상태에서는 30초" in source
