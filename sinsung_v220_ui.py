"""2.2.1 automatic collection stability UI + shopping-history manual upload."""
import hashlib
import hmac
import os
import re

VERSION = "2.2.1"


def _upload_token():
    secret = os.getenv("DASHBOARD_SECRET", "")
    if not secret:
        return ""
    return hmac.new(
        secret.encode("utf-8"),
        b"sinsung-shop-history-upload-2.2.1",
        hashlib.sha256,
    ).hexdigest()


def apply_v220_ui():
    import server as s

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        failures = s.get_setting('last_auto_sync_consecutive_failures', '0') or '0'
        current = s.get_setting('last_auto_sync_current_source', '') or '-'
        heartbeat = s.get_setting('scheduler_heartbeat', '') or '-'
        next_due = s.get_setting('next_auto_sync_due', '') or '-'
        effective_days = s.get_setting('last_auto_sync_effective_days', '14') or '14'
        reserve = s.get_setting('auto_sync_api_reserve', '100') or '100'
        manual = '진행중' if s.get_setting('manual_sync_active', '0') == '1' else '대기'

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.2.1 자동수집 안정판 + 쇼핑몰 과거자료 업로드</b><br>
  쇼핑몰 · 물품입찰 · 용역의 <b>현재자료 자동수집은 2시간마다</b> 기존 단일 수집엔진으로 계속 갱신합니다.<br>
  상태: {s.esc(s.get_setting('last_auto_sync_status','대기'))}
  · 현재 작업: {s.esc(current)}
  · 수동작업: {s.esc(manual)}<br>
  최근 시작: {s.esc(s.get_setting('last_auto_sync_started') or '-')}
  · 최근 완료: {s.esc(s.get_setting('last_auto_sync_finished') or '-')}
  · 다음 예정: {s.esc(next_due)}<br>
  Scheduler heartbeat: {s.esc(heartbeat)}
  · 연속 오류: {s.esc(failures)}회
  · 최근 재조회: {s.esc(effective_days)}일<br>
  API 안전여유: 종류별 {s.esc(reserve)}회
  · 장시간 서버 중단 시 최대 30일까지 자동 보충조회<br>
  {s.esc(s.get_setting('last_auto_sync_result') or '자동수집 대기')}<br>
  자동수집과 수동작업은 동시에 실행되지 않습니다.
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.2.1 자동수집 안정판" not in page:
            page = page.replace(marker, notice + marker, 1)

        upload_token = _upload_token()
        last_upload = s.get_setting('last_shop_upload_result', '') or '아직 업로드한 과거자료가 없습니다.'
        last_upload_at = s.get_setting('last_shop_upload_at', '') or '-'
        last_upload_error = s.get_setting('last_shop_upload_error', '') or ''

        upload_box = f"""
<hr>
<h3>쇼핑몰 과거자료 수동 업로드</h3>
<div class="notice" style="margin:10px 0 12px;background:#f8fafc">
  <b>대상 기간: 2025-01-01 ~ 2026-07-31</b><br>
  쇼핑몰 납품요구/계약실적만 업로드합니다. <b>입찰 · 용역 · 예산 자료 업로드는 사용하지 않습니다.</b><br>
  CSV 또는 XLSX 파일을 사용할 수 있으며, 세부품명번호는 지정된 12개 대상 품목만 저장됩니다.<br>
  같은 자료를 다시 올려도 source_key 기준 UPSERT로 중복 행을 만들지 않고 기존 행을 갱신합니다.<br>
  XLS 파일은 XLSX 또는 CSV로 저장 후 올려 주세요. 한 파일 최대 50MB입니다.
</div>
<form action="/upload-shop-history" method="post" enctype="multipart/form-data"
      style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0 14px">
  <input type="hidden" name="upload_token" value="{upload_token}">
  <input type="file" name="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required>
  <button class="btn" type="submit" onclick="return confirm('2025-01-01 ~ 2026-07-31 쇼핑몰 과거자료를 업로드할까요?');">
    쇼핑몰 과거자료 업로드
  </button>
</form>
<div style="font-size:13px;line-height:1.7;color:#475569">
  <b>최근 업로드:</b> {s.esc(last_upload_at)}<br>
  <b>결과:</b> {s.esc(last_upload)}
  {('<br><b>최근 오류:</b> ' + s.esc(last_upload_error)) if last_upload_error else ''}
</div>
"""
        if marker in page and "쇼핑몰 과거자료 수동 업로드" not in page:
            page = page.replace(marker, upload_box + marker, 1)

        page = re.sub(
            r'(<input type="number" min="1" name="auto_sync_hours" value="[^"]*")',
            r'\1 readonly title="2.2.1 안정판은 2시간 고정"',
            page,
            count=1,
        )

        page = re.sub(
            r'<button class="btn danger-lite"[^>]*>최근 3년 구축 시작</button>',
            '<button class="btn danger-lite" type="button" disabled>API 과거구축 사용 안 함 · 쇼핑몰 파일 업로드 방식</button>',
            page,
            count=1,
        )
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
