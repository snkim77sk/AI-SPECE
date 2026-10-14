"""G2B 4.1.300 UI memory protection wording contract.

A reduced 25MiB launch-only reserve MUST NOT be described as weakening
the 64MiB in-flight web protection boundary.
"""
from pathlib import Path


def test_collection_monitor_explains_distinct_256mib_start_and_running_reserves():
    source = (Path(__file__).resolve().parents[1] / "vnext_clean_app.py").read_text(encoding="utf-8")
    assert "수집 시작 제한(112MiB Worker + 64MiB 여유)은 유지됩니다." not in source
    assert "256MiB 수집 시작 시 기본 25MiB 여유를 적용하고" in source
    assert "수집 중에는 웹 보호 여유 64MiB를 유지합니다." in source
