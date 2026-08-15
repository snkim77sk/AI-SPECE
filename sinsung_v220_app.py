"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.2.2.

Keeps the proven 2.2 startup/collection path unchanged and hardens only the
login/session surface: login CSRF, server-side session epoch, password change,
and logout invalidation.
"""
import hashlib
import hmac
import os
import time
from urllib.parse import parse_qs, quote, urlparse

import app as legacy_app

APP_VERSION = "2.2.2"

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


def _apply_login_hardening(server):
    """Patch only authentication helpers/handlers on the already loaded server."""
    if getattr(server, "_v222_login_hardening", False):
        return

    original_login_html = server.login_html
    original_setup_admin_html = server.setup_admin_html
    original_base_html = server.base_html
    original_do_get = server.Handler.do_GET
    original_do_post = server.Handler.do_POST

    def epoch_key(username):
        digest = hashlib.sha256(str(username).encode("utf-8")).hexdigest()[:32]
        return "auth_session_epoch_" + digest

    def session_epoch(username):
        try:
            return int(server.get_setting(epoch_key(username), "0") or 0)
        except Exception:
            return 0

    def bump_session_epoch(username):
        value = session_epoch(username) + 1
        server.set_setting(epoch_key(username), str(value))
        return value

    def make_session_v222(user):
        exp = int(time.time()) + server.SESSION_TTL
        epoch = session_epoch(user)
        payload = f"{user}|{exp}|{epoch}".encode("utf-8")
        sig = hmac.new(server.SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
        return server._b64e(payload) + "." + server._b64e(sig)

    def session_user(token):
        try:
            p64, s64 = str(token or "").split(".", 1)
            payload = server._b64d(p64)
            sig = server._b64d(s64)
            expected = hmac.new(server.SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
            if not hmac.compare_digest(sig, expected):
                return ""
            user, exp, epoch = payload.decode("utf-8").rsplit("|", 2)
            if int(exp) < int(time.time()) or int(epoch) != session_epoch(user):
                return ""
            with server.connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM users WHERE username=? AND status='active'", (user,)
                ).fetchone()
            return user if row else ""
        except Exception:
            # Old 2.2 two-field cookies are intentionally rejected once so every
            # browser receives the hardened session format after this upgrade.
            return ""

    def valid_session_v222(token):
        return bool(session_user(token))

    def login_html_v222(error=""):
        page = original_login_html(error)
        marker = '<form method="post" action="/login">'
        if marker in page and 'name="_csrf"' not in page:
            page = page.replace(marker, marker + server.csrf_input("/login"), 1)
        page = page.replace(
            "<p>조달 데이터 관리 시스템</p>",
            "<p>조달 데이터 관리 시스템 · 보안 로그인</p>",
            1,
        )
        return page

    def setup_admin_html_v222(error=""):
        page = original_setup_admin_html(error)
        marker = '<form method="post" action="/setup-admin">'
        if marker in page and 'name="_csrf"' not in page:
            page = page.replace(marker, marker + server.csrf_input("/setup-admin"), 1)
        return page

    def base_html_v222(content, active="대시보드", flash="", flash_error=False):
        page = original_base_html(content, active, flash, flash_error)
        needle = '<a href="/settings">설정</a> <span>/</span>'
        if needle in page and 'href="/account"' not in page:
            page = page.replace(
                needle,
                '<a href="/settings">설정</a> <span>/</span> '
                '<a href="/account">비밀번호 변경</a> <span>/</span>',
                1,
            )
        return page

    def account_html(username, message="", error=False):
        flash = ""
        if message:
            flash = (
                f'<div class="flash {"error" if error else "ok"}">'
                f'{server.esc(message)}</div>'
            )
        body = f'''{server.pathbar('/account','lighting-sketch / security')}
<section class="card page" style="max-width:720px">
<h2>로그인 보안 · 비밀번호 변경</h2>
<p>현재 계정: <b>{server.esc(username)}</b></p>{flash}
<div class="notice">비밀번호를 변경하면 다른 브라우저와 기기의 기존 로그인 세션은 즉시 종료됩니다.</div>
<form method="post" action="/account" style="max-width:520px">
{server.csrf_input('/account')}
<label>현재 비밀번호<input type="password" name="current_password" autocomplete="current-password" required></label>
<label>새 비밀번호<input type="password" name="new_password" minlength="10" autocomplete="new-password" required></label>
<label>새 비밀번호 확인<input type="password" name="new_password_confirm" minlength="10" autocomplete="new-password" required></label>
<button class="primary" type="submit">비밀번호 변경</button>
</form></section>'''
        return server.base_html(body, "")

    def secure_cookie(user):
        token = server.make_session(user)
        return (
            f"ls_session={token}; Path=/; Max-Age={server.SESSION_TTL}; HttpOnly; SameSite=Strict"
            + ("; Secure" if server.COOKIE_SECURE else "")
        )

    def clear_cookie():
        return (
            "ls_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            + ("; Secure" if server.COOKIE_SECURE else "")
        )

    def do_get_v222(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/logout":
                user = session_user(self.cookie("ls_session"))
                if user:
                    bump_session_epoch(user)
                return self.redirect("/login", {"Set-Cookie": clear_cookie()})
            if parsed.path == "/account":
                if self.require_auth(parsed.path):
                    return
                user = session_user(self.cookie("ls_session"))
                if not user:
                    return self.redirect("/login")
                qs = parse_qs(parsed.query)
                message = (qs.get("msg") or [""])[0]
                error = (qs.get("error") or ["0"])[0] == "1"
                return self.send_bytes(account_html(user, message, error))
            return original_do_get(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("로그인 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)

    def do_post_v222(self):
        try:
            path = urlparse(self.path).path
            if path == "/setup-admin":
                form = self.parse_post()
                if not server.valid_csrf("/setup-admin", form):
                    return self.send_bytes(setup_admin_html_v222("요청이 만료되었습니다. 화면을 새로고침해 주세요."), status=403)
                if not server.users_empty():
                    return self.redirect("/login")
                user = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                confirm = (form.get("password_confirm") or [""])[0]
                if len(user) < 4 or len(password) < 10 or password != confirm:
                    return self.redirect(
                        "/setup-admin?error="
                        + quote("아이디는 4자 이상, 비밀번호는 10자 이상이며 확인값과 같아야 합니다.")
                    )
                with server.connect() as conn:
                    conn.execute(
                        "INSERT INTO users(username,password_hash,role,status) VALUES (?,?, 'admin','active')",
                        (user, server._password_hash(password)),
                    )
                return self.redirect("/login")

            if path == "/login":
                form = self.parse_post()
                if not server.valid_csrf("/login", form):
                    return self.send_bytes(login_html_v222("요청이 만료되었습니다. 화면을 새로고침해 주세요."), status=403)
                if server.users_empty():
                    return self.redirect("/setup-admin")
                ip = self.client_ip()
                if server._login_limited(ip):
                    return self.send_bytes(
                        login_html_v222("로그인 실패가 반복되어 10분간 잠시 제한됩니다."),
                        status=429,
                    )
                user = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                with server.connect() as conn:
                    row = conn.execute(
                        "SELECT password_hash FROM users WHERE username=? AND status='active'",
                        (user,),
                    ).fetchone()
                if row and server._password_valid(password, row["password_hash"]):
                    server._login_success(ip)
                    return self.redirect("/dashboard", {"Set-Cookie": secure_cookie(user)})
                server._login_failed(ip)
                return self.redirect(
                    "/login?error=" + quote("아이디 또는 비밀번호가 올바르지 않습니다.")
                )

            if path == "/account":
                if self.require_auth(path):
                    return
                form = self.parse_post()
                if not server.valid_csrf("/account", form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)
                user = session_user(self.cookie("ls_session"))
                if not user:
                    return self.redirect("/login")
                current = (form.get("current_password") or [""])[0]
                new_password = (form.get("new_password") or [""])[0]
                confirm = (form.get("new_password_confirm") or [""])[0]
                with server.connect() as conn:
                    row = conn.execute(
                        "SELECT password_hash FROM users WHERE username=? AND status='active'",
                        (user,),
                    ).fetchone()
                if not row or not server._password_valid(current, row["password_hash"]):
                    return self.redirect(
                        "/account?error=1&msg=" + quote("현재 비밀번호가 올바르지 않습니다.")
                    )
                if len(new_password) < 10:
                    return self.redirect(
                        "/account?error=1&msg=" + quote("새 비밀번호는 10자 이상이어야 합니다.")
                    )
                if new_password != confirm:
                    return self.redirect(
                        "/account?error=1&msg=" + quote("새 비밀번호 확인값이 일치하지 않습니다.")
                    )
                if server._password_valid(new_password, row["password_hash"]):
                    return self.redirect(
                        "/account?error=1&msg=" + quote("현재 비밀번호와 다른 비밀번호를 사용해 주세요.")
                    )
                with server.connect() as conn:
                    conn.execute(
                        "UPDATE users SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE username=?",
                        (server._password_hash(new_password), user),
                    )
                bump_session_epoch(user)
                return self.redirect(
                    "/account?msg=" + quote("비밀번호가 변경되었습니다."),
                    {"Set-Cookie": secure_cookie(user)},
                )

            return original_do_post(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("로그인 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)

    server.make_session = make_session_v222
    server.valid_session = valid_session_v222
    server.login_html = login_html_v222
    server.setup_admin_html = setup_admin_html_v222
    server.base_html = base_html_v222
    server.Handler.do_GET = do_get_v222
    server.Handler.do_POST = do_post_v222
    server._session_user_v222 = session_user
    server._bump_session_epoch_v222 = bump_session_epoch
    server._v222_login_hardening = True


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
        _apply_login_hardening(server)
        server.APP_VERSION = APP_VERSION
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
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]