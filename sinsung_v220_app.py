"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.2.

Adds manual/automatic collection coordination while preserving the proven 2.0
FastAPI healthcheck-first startup structure.
"""
import os

import app as legacy_app
from app_version import APP_VERSION

_original_manual_start = legacy_app._start_background_collect
_original_background_collect = legacy_app._background_collect


def _set_manual_state(active, source=""):
    try:
        from db import set_setting
        set_setting("manual_sync_active", "1" if active else "0")
        set_setting("manual_sync_source", source if active else "")
    except Exception:
        pass


def _background_collect_v220(path, body, headers):
    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    try:
        return _original_background_collect(path, body, headers)
    finally:
        _set_manual_state(False, "")


def _start_background_collect_v220(path, body, request_headers):
    try:
        from db import get_setting
        if get_setting("last_auto_sync_status", "") == "수집중":
            source = get_setting("last_auto_sync_current_source", "") or "자동수집"
            return False, f"{source} 자동수집이 진행 중입니다. 완료 후 수동수집을 실행해 주세요."
        if get_setting("manual_sync_active", "0") == "1":
            source = get_setting("manual_sync_source", "") or "수동수집"
            return False, f"이미 {source} 수동수집이 진행 중입니다."
    except Exception:
        pass

    # Mark the narrow start window before the background thread is created so
    # the scheduler cannot begin an automatic run at the same moment.
    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    ok, message = _original_manual_start(path, body, request_headers)
    if not ok:
        _set_manual_state(False, "")
    return ok, message


def _install_shop_rolling_dates(server_module):
    """Keep the shopping-list period rolling unless the user edits the dates."""
    if getattr(server_module, "_shop_rolling_dates_installed", False):
        return

    original_build = server_module.build_shop_params
    original_html = server_module.shopping_html

    def build_shop_params(qs):
        mode = (qs.get("date_mode") or ["auto"])[0]
        if mode != "manual":
            # Old tab/pagination URLs carry yesterday's start/end values. In
            # automatic mode they must not pin the period, so discard them and
            # let the KST-aware date_params calculate the current 14-day range.
            clean = {k: list(v) for k, v in qs.items()}
            clean.pop("start", None)
            clean.pop("end", None)
            qs = clean
            mode = "auto"
        p = original_build(qs)
        p["_date_mode"] = mode
        return p

    def shopping_html(p):
        page = original_html(p)
        mode = "manual" if p.get("_date_mode") == "manual" else "auto"
        script = f"""
<script id="rolling-shop-dates">
(function(){{
  var mode = {mode!r};
  var form = document.querySelector('form.filters');
  if (!form) return;

  var marker = form.querySelector('input[name="date_mode"]');
  if (!marker) {{
    marker = document.createElement('input');
    marker.type = 'hidden';
    marker.name = 'date_mode';
    form.appendChild(marker);
  }}
  marker.value = mode;

  ['start','end'].forEach(function(name){{
    var el = form.querySelector('input[name="' + name + '"]');
    if (!el) return;
    el.addEventListener('input', function(){{
      mode = 'manual';
      marker.value = 'manual';
    }});
  }});

  var actions = form.querySelector('.actions');
  if (actions && !document.getElementById('auto-date-reset')) {{
    var reset = document.createElement('a');
    reset.id = 'auto-date-reset';
    reset.className = 'btn';
    reset.textContent = '자동기간';
    var resetUrl = new URL(window.location.href);
    resetUrl.searchParams.delete('start');
    resetUrl.searchParams.delete('end');
    resetUrl.searchParams.delete('date_mode');
    resetUrl.searchParams.delete('page');
    reset.href = resetUrl.pathname + resetUrl.search;
    actions.insertBefore(reset, actions.children[1] || null);
  }}

  function preserveManualLinks() {{
    if (mode !== 'manual') return;
    document.querySelectorAll('a[href]').forEach(function(a){{
      try {{
        var u = new URL(a.href, window.location.href);
        if (u.pathname.endsWith('/g2b/shopping/prdct_detail.php') ||
            u.pathname.endsWith('/g2b/shopping_prdct_detail.php')) {{
          u.searchParams.set('date_mode', 'manual');
          a.href = u.pathname + u.search + u.hash;
        }}
      }} catch (e) {{}}
    }});
  }}
  preserveManualLinks();

  function kstToday() {{
    var parts = new Intl.DateTimeFormat('en-US', {{
      timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit'
    }}).formatToParts(new Date());
    var out = {{}};
    parts.forEach(function(p){{ out[p.type] = p.value; }});
    return out.year + '-' + out.month + '-' + out.day;
  }}

  if (mode === 'auto') {{
    setInterval(function(){{
      var end = form.querySelector('input[name="end"]');
      if (end && end.value !== kstToday()) {{
        var u = new URL(window.location.href);
        u.searchParams.delete('start');
        u.searchParams.delete('end');
        u.searchParams.delete('page');
        u.searchParams.set('date_mode', 'auto');
        window.location.replace(u.pathname + u.search);
      }}
    }}, 60000);
  }}
}})();
</script>
"""
        if "</body>" in page:
            return page.replace("</body>", script + "</body>", 1)
        return page + script

    server_module.build_shop_params = build_shop_params
    server_module.shopping_html = shopping_html
    server_module._shop_rolling_dates_installed = True


def _start_backend_v220() -> None:
    ok, msg = legacy_app._configured()
    if not ok:
        legacy_app._backend_error = msg
        return

    os.environ["HOST"] = legacy_app.BACKEND_HOST
    os.environ["PORT"] = str(legacy_app.BACKEND_PORT)
    os.environ["G2B_PUBLIC_MODE"] = "0" if legacy_app.TEST_MODE else "1"
    os.environ["G2B_OPEN_BROWSER"] = "0"
    os.environ["G2B_SEED_SAMPLE"] = "0"
    os.environ.setdefault("G2B_COOKIE_SECURE", "1")

    try:
        # Existing one-time cleanup is idempotent and normally already complete.
        from sinsung_v200_reset import reset_data_once
        reset_data_once()

        # Preserve 2.1 initialization for users who jump directly from 2.0.
        from sinsung_v210_auto import initialize_auto_sync
        initialize_auto_sync()

        from sinsung_v220_stability import initialize_auto_stability
        initialize_auto_stability()

        if str(os.getenv("G2B_PURGE_SAMPLE_DATA", "1")).lower() in ("1", "true", "yes", "on"):
            from db import init_db
            from seed import clear_samples
            init_db()
            clear_samples()

        import server
        # main.py runs this v2.2 wrapper rather than app.py's original backend
        # starter. Install the KST date patch here as well so it is active in the
        # actual Cafe24 AI SPACE runtime.
        if hasattr(legacy_app, "_install_dynamic_date_defaults"):
            legacy_app._install_dynamic_date_defaults(server)
        _install_shop_rolling_dates(server)
        server.main(open_browser=False)
    except Exception as exc:
        legacy_app._backend_error = f"내부 대시보드 시작 실패: {exc}"


def _fast_backend_wait(timeout: float = 0.5) -> bool:
    return legacy_app._backend_listening()


# Original manual-start resolves _background_collect from app.py globals when it
# runs, so installing both wrappers here safely coordinates the two directions.
legacy_app._background_collect = _background_collect_v220
legacy_app._start_background_collect = _start_background_collect_v220
legacy_app._start_backend = _start_backend_v220
legacy_app._wait_for_backend = _fast_backend_wait

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]
