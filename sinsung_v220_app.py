"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.2.3.

Keeps the proven 2.2 collection/startup path unchanged and extends only the
authentication/account surface: signup approval workflow, admin member control,
session invalidation, password change, CSRF and secure cookies.
"""
import hashlib
import hmac
import os
import re
import time
from urllib.parse import parse_qs, quote, urlparse

import app as legacy_app

APP_VERSION = "2.2.3"

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

    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    ok, message = _original_manual_start(path, body, request_headers)
    if not ok:
        _set_manual_state(False, "")
    return ok, message


def _apply_auth_management(server):
    """Patch only authentication helpers/pages/handlers on the loaded server."""
    if getattr(server, "_v223_auth_management", False):
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

    def make_session_v223(user):
        exp = int(time.time()) + server.SESSION_TTL
        epoch = session_epoch(user)
        payload = f"{user}|{exp}|{epoch}".encode("utf-8")
        sig = hmac.new(server.SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
        return server._b64e(payload) + "." + server._b64e(sig)

    def user_row(username):
        if not username:
            return None
        with server.connect() as conn:
            return conn.execute(
                "SELECT id,username,password_hash,role,status,created_at,updated_at FROM users WHERE username=?",
                (username,),
            ).fetchone()

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
            row = user_row(user)
            return user if row and row["status"] == "active" else ""
        except Exception:
            return ""

    def valid_session_v223(token):
        return bool(session_user(token))

    def current_role(handler):
        user = session_user(handler.cookie("ls_session"))
        row = user_row(user)
        return (row["role"] if row else ""), user

    def require_admin(handler):
        if handler.require_auth("/admin/users"):
            return True
        role, _ = current_role(handler)
        if role != "admin":
            handler.send_bytes("관리자만 접근할 수 있습니다.", "text/plain; charset=utf-8", 403)
            return True
        return False

    def auth_shell(title, subtitle, body, footer=""):
        return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{server.esc(title)}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#050b1b;color:#eef4ff;font-family:Arial,'Noto Sans KR','Malgun Gothic',sans-serif}}.auth-shell{{min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 50% 15%,#10254a 0,#08142d 30%,#050b1b 68%)}}.auth-panel{{width:min(500px,100%);padding:42px 38px;border:1px solid #1b2b4b;border-radius:20px;background:#08142df5;box-shadow:0 28px 80px #0009}}.brand{{text-align:center;font-size:42px;font-style:italic;font-weight:900;letter-spacing:-.05em;color:#4da5ff}}.sub{{text-align:center;color:#7f98bf;font-size:12px;font-weight:800;letter-spacing:.2em;margin:4px 0 28px}}h1{{text-align:center;font-size:24px;margin:0 0 8px}}p.desc{{text-align:center;color:#8fa3c3;margin:0 0 24px;line-height:1.6}}form{{display:grid;gap:14px}}label{{display:grid;gap:7px;font-size:13px;font-weight:700}}input{{height:48px;border:1px solid #263a5c;border-radius:10px;background:#071126;color:#fff;padding:0 14px;font-size:15px}}button{{height:50px;border:0;border-radius:11px;background:linear-gradient(90deg,#246ff3,#16b8dd);color:white;font-weight:900;font-size:15px;cursor:pointer}}.note{{margin-top:18px;color:#7690b8;font-size:12px;line-height:1.6;text-align:center}}a{{color:#55b9ff}}.error{{background:#35141c;border:1px solid #73303d;color:#ffb8c3;padding:10px 12px;border-radius:9px;margin-bottom:14px}}.ok{{background:#123326;border:1px solid #286947;color:#b7f5d2;padding:10px 12px;border-radius:9px;margin-bottom:14px}}</style></head><body><main class="auth-shell"><section class="auth-panel"><div class="brand">SINSUNG</div><div class="sub">G2B DATA VIEW</div><h1>{server.esc(title)}</h1><p class="desc">{server.esc(subtitle)}</p>{body}{footer}</section></main></body></html>'''

    def login_html_v223(error=""):
        page = original_login_html(error)
        marker = 'action="/login">'
        if marker in page and 'name="_csrf"' not in page:
            page = page.replace(marker, marker + server.csrf_input("/login"), 1)
        if 'href="/signup"' not in page:
            page = page.replace("</form>", '</form><div class="auth-note"><a href="/signup">회원가입 신청</a></div>', 1)
        return page

    def setup_admin_html_v223(error=""):
        page = original_setup_admin_html(error)
        marker = 'action="/setup-admin">'
        if marker in page and 'name="_csrf"' not in page:
            page = page.replace(marker, marker + server.csrf_input("/setup-admin"), 1)
        return page

    def signup_html(message="", error=False, submitted=False):
        flash = f'<div class="{"error" if error else "ok"}">{server.esc(message)}</div>' if message else ""
        if submitted:
            body = flash + '<div class="note"><a href="/login">로그인 화면으로 돌아가기</a></div>'
        else:
            body = f'''{flash}<form method="post" action="/signup">{server.csrf_input('/signup')}<label>아이디<input name="username" minlength="4" maxlength="50" autocomplete="username" required></label><label>비밀번호<input type="password" name="password" minlength="10" autocomplete="new-password" required></label><label>비밀번호 확인<input type="password" name="password_confirm" minlength="10" autocomplete="new-password" required></label><button type="submit">회원가입 신청</button></form>'''
        footer = '<div class="note">가입 신청 후 관리자가 승인해야 로그인할 수 있습니다.<br><a href="/login">로그인으로 돌아가기</a></div>'
        return auth_shell("회원가입 신청", "관리자 승인 후 사용 가능합니다.", body, footer)

    def base_html_v223(content, active="대시보드", flash="", flash_error=False):
        page = original_base_html(content, active, flash, flash_error)
        needle = '<a href="/settings">설정</a> <span>/</span>'
        if needle in page:
            links = '<a href="/settings">설정</a> <span>/</span> '
            if 'href="/admin/users"' not in page:
                links += '<a href="/admin/users">회원관리</a> <span>/</span> '
            if 'href="/account"' not in page:
                links += '<a href="/account">비밀번호 변경</a> <span>/</span> '
            page = page.replace(needle, links, 1)
        return page

    def account_html(username, message="", error=False):
        row = user_row(username)
        role = row["role"] if row else "user"
        flash = f'<div class="flash {"error" if error else "ok"}">{server.esc(message)}</div>' if message else ""
        withdraw = ""
        if role != "admin":
            withdraw = f'''<section class="panel" style="margin-top:24px"><h3>회원 탈퇴</h3><div class="notice">탈퇴하면 즉시 로그아웃되며 다시 사용하려면 새로 가입 신청 후 관리자 승인이 필요합니다.</div><form method="post" action="/account" style="max-width:520px">{server.csrf_input('/account')}<input type="hidden" name="action" value="withdraw"><label>현재 비밀번호<input type="password" name="current_password" autocomplete="current-password" required></label><label>확인문구 <b>회원탈퇴</b><input name="confirmation" required></label><button type="submit">회원 탈퇴</button></form></section>'''
        body = f'''{server.pathbar('/account','sinsung / security')}<section class="card page" style="max-width:760px"><h2>로그인 보안 · 계정관리</h2><p>현재 계정: <b>{server.esc(username)}</b> · 권한: <b>{'관리자' if role == 'admin' else '일반사용자'}</b></p>{flash}<div class="notice">비밀번호를 변경하면 다른 브라우저와 기기의 기존 로그인 세션은 즉시 종료됩니다.</div><form method="post" action="/account" style="max-width:520px">{server.csrf_input('/account')}<input type="hidden" name="action" value="change_password"><label>현재 비밀번호<input type="password" name="current_password" autocomplete="current-password" required></label><label>새 비밀번호<input type="password" name="new_password" minlength="10" autocomplete="new-password" required></label><label>새 비밀번호 확인<input type="password" name="new_password_confirm" minlength="10" autocomplete="new-password" required></label><button class="primary" type="submit">비밀번호 변경</button></form>{withdraw}</section>'''
        return server.base_html(body, "")

    status_labels = {"pending": "승인대기", "active": "사용중", "rejected": "승인거절", "suspended": "사용중지", "withdrawn": "회원탈퇴"}

    def admin_users_html(message="", error=False):
        with server.connect() as conn:
            rows = conn.execute("SELECT id,username,role,status,created_at,updated_at FROM users ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 WHEN 'suspended' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END,id DESC").fetchall()
        pending = sum(1 for r in rows if r["status"] == "pending")
        tr = []
        for r in rows:
            role_label = "관리자" if r["role"] == "admin" else "일반사용자"
            status_label = status_labels.get(r["status"], r["status"])
            actions = "-"
            if r["role"] != "admin":
                buttons = []
                if r["status"] in ("pending", "rejected"):
                    buttons.append(("approve", "승인"))
                if r["status"] == "pending":
                    buttons.append(("reject", "거절"))
                if r["status"] == "active":
                    buttons.append(("suspend", "사용중지"))
                if r["status"] == "suspended":
                    buttons.append(("activate", "사용재개"))
                if buttons:
                    actions = " ".join(f'''<form method="post" action="/admin/users" style="display:inline">{server.csrf_input('/admin/users')}<input type="hidden" name="username" value="{server.esc(r['username'])}"><input type="hidden" name="action" value="{action}"><button class="primary" type="submit">{label}</button></form>''' for action, label in buttons)
            tr.append(f"<tr><td>{server.esc(r['username'])}</td><td>{role_label}</td><td>{status_label}</td><td>{server.esc(r['created_at'])}</td><td>{server.esc(r['updated_at'])}</td><td>{actions}</td></tr>")
        flash = f'<div class="flash {"error" if error else "ok"}">{server.esc(message)}</div>' if message else ""
        body = f'''{server.pathbar('/admin/users','sinsung / members')}<section class="card page"><h2>회원관리</h2>{flash}<div class="kpis"><div><span>전체 회원</span><strong>{len(rows):,} 명</strong></div><div><span>승인대기</span><strong>{pending:,} 명</strong></div></div><div class="notice">회원가입 신청은 승인대기로 등록됩니다. 승인된 일반사용자만 로그인할 수 있습니다. 사용중지·거절 시 기존 세션도 즉시 무효화됩니다.</div><div class="tablewrap"><table><thead><tr><th>아이디</th><th>권한</th><th>상태</th><th>가입신청</th><th>최근변경</th><th>관리</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan="6" class="empty">회원 없음</td></tr>'}</tbody></table></div></section>'''
        return server.base_html(body, "")

    def secure_cookie(user):
        token = server.make_session(user)
        return f"ls_session={token}; Path=/; Max-Age={server.SESSION_TTL}; HttpOnly; SameSite=Strict" + ("; Secure" if server.COOKIE_SECURE else "")

    def clear_cookie():
        return "ls_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" + ("; Secure" if server.COOKIE_SECURE else "")

    def do_get_v223(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/signup":
                if server.users_empty():
                    return self.redirect("/setup-admin")
                if self.authenticated():
                    return self.redirect("/dashboard")
                qs = parse_qs(parsed.query)
                return self.send_bytes(signup_html((qs.get("msg") or [""])[0], (qs.get("error") or ["0"])[0] == "1", (qs.get("submitted") or ["0"])[0] == "1"))
            if path == "/logout":
                user = session_user(self.cookie("ls_session"))
                if user:
                    bump_session_epoch(user)
                return self.redirect("/login", {"Set-Cookie": clear_cookie()})
            if path == "/account":
                if self.require_auth(path):
                    return
                user = session_user(self.cookie("ls_session"))
                if not user:
                    return self.redirect("/login")
                qs = parse_qs(parsed.query)
                return self.send_bytes(account_html(user, (qs.get("msg") or [""])[0], (qs.get("error") or ["0"])[0] == "1"))
            if path == "/admin/users":
                if require_admin(self):
                    return
                qs = parse_qs(parsed.query)
                return self.send_bytes(admin_users_html((qs.get("msg") or [""])[0], (qs.get("error") or ["0"])[0] == "1"))
            return original_do_get(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("계정 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)

    def do_post_v223(self):
        try:
            path = urlparse(self.path).path
            if path == "/setup-admin":
                form = self.parse_post()
                if not server.valid_csrf("/setup-admin", form):
                    return self.send_bytes(setup_admin_html_v223("요청이 만료되었습니다. 화면을 새로고침해 주세요."), status=403)
                if not server.users_empty():
                    return self.redirect("/login")
                user = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                confirm = (form.get("password_confirm") or [""])[0]
                if len(user) < 4 or len(password) < 10 or password != confirm:
                    return self.redirect("/setup-admin?error=" + quote("아이디는 4자 이상, 비밀번호는 10자 이상이며 확인값과 같아야 합니다."))
                with server.connect() as conn:
                    conn.execute("INSERT INTO users(username,password_hash,role,status) VALUES (?,?, 'admin','active')", (user, server._password_hash(password)))
                return self.redirect("/login")

            if path == "/signup":
                form = self.parse_post()
                if not server.valid_csrf("/signup", form):
                    return self.send_bytes(signup_html("요청이 만료되었습니다. 화면을 새로고침해 주세요.", True), status=403)
                if server.users_empty():
                    return self.redirect("/setup-admin")
                user = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                confirm = (form.get("password_confirm") or [""])[0]
                if not (4 <= len(user) <= 50) or not re.fullmatch(r"[0-9A-Za-z가-힣._-]+", user):
                    return self.redirect("/signup?error=1&msg=" + quote("아이디는 4~50자의 한글·영문·숫자·._-만 사용할 수 있습니다."))
                if len(password) < 10:
                    return self.redirect("/signup?error=1&msg=" + quote("비밀번호는 10자 이상이어야 합니다."))
                if password != confirm:
                    return self.redirect("/signup?error=1&msg=" + quote("비밀번호 확인값이 일치하지 않습니다."))
                existing = user_row(user)
                if existing and existing["status"] in ("active", "suspended", "pending"):
                    msg = "이미 승인대기 중인 가입 신청입니다." if existing["status"] == "pending" else "이미 사용 중인 아이디입니다."
                    return self.redirect("/signup?error=1&msg=" + quote(msg))
                with server.connect() as conn:
                    if existing and existing["status"] in ("rejected", "withdrawn"):
                        conn.execute("UPDATE users SET password_hash=?,role='user',status='pending',updated_at=CURRENT_TIMESTAMP WHERE username=?", (server._password_hash(password), user))
                    else:
                        conn.execute("INSERT INTO users(username,password_hash,role,status) VALUES (?,?, 'user','pending')", (user, server._password_hash(password)))
                bump_session_epoch(user)
                return self.redirect("/signup?submitted=1&msg=" + quote("회원가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."))

            if path == "/login":
                form = self.parse_post()
                if not server.valid_csrf("/login", form):
                    return self.send_bytes(login_html_v223("요청이 만료되었습니다. 화면을 새로고침해 주세요."), status=403)
                if server.users_empty():
                    return self.redirect("/setup-admin")
                ip = self.client_ip()
                if server._login_limited(ip):
                    return self.send_bytes(login_html_v223("로그인 실패가 반복되어 10분간 잠시 제한됩니다."), status=429)
                user = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                row = user_row(user)
                if row and server._password_valid(password, row["password_hash"]):
                    if row["status"] == "active":
                        server._login_success(ip)
                        return self.redirect("/dashboard", {"Set-Cookie": secure_cookie(user)})
                    status_messages = {"pending": "관리자 승인 대기 중입니다.", "rejected": "회원가입 신청이 승인되지 않았습니다. 필요하면 다시 가입 신청해 주세요.", "suspended": "관리자에 의해 사용중지된 계정입니다.", "withdrawn": "탈퇴 처리된 계정입니다. 다시 사용하려면 회원가입 신청을 해주세요."}
                    return self.send_bytes(login_html_v223(status_messages.get(row["status"], "로그인할 수 없는 계정입니다.")), status=403)
                server._login_failed(ip)
                return self.redirect("/login?error=" + quote("아이디 또는 비밀번호가 올바르지 않습니다."))

            if path == "/admin/users":
                if require_admin(self):
                    return
                form = self.parse_post()
                if not server.valid_csrf("/admin/users", form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)
                action = (form.get("action") or [""])[0]
                target = (form.get("username") or [""])[0].strip()
                row = user_row(target)
                if not row:
                    return self.redirect("/admin/users?error=1&msg=" + quote("회원을 찾을 수 없습니다."))
                if row["role"] == "admin":
                    return self.redirect("/admin/users?error=1&msg=" + quote("관리자 계정은 이 화면에서 상태를 변경할 수 없습니다."))
                allowed = {"approve": (("pending", "rejected"), "active", "회원 승인이 완료되었습니다."), "reject": (("pending",), "rejected", "회원가입 신청을 거절했습니다."), "suspend": (("active",), "suspended", "회원 사용을 중지했습니다."), "activate": (("suspended",), "active", "회원 사용을 재개했습니다.")}
                if action not in allowed:
                    return self.redirect("/admin/users?error=1&msg=" + quote("허용되지 않은 회원관리 작업입니다."))
                valid_from, new_status, msg = allowed[action]
                if row["status"] not in valid_from:
                    return self.redirect("/admin/users?error=1&msg=" + quote("현재 회원 상태에서는 실행할 수 없는 작업입니다."))
                with server.connect() as conn:
                    conn.execute("UPDATE users SET status=?,updated_at=CURRENT_TIMESTAMP WHERE username=? AND role<>'admin'", (new_status, target))
                bump_session_epoch(target)
                return self.redirect("/admin/users?msg=" + quote(msg))

            if path == "/account":
                if self.require_auth(path):
                    return
                form = self.parse_post()
                if not server.valid_csrf("/account", form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)
                user = session_user(self.cookie("ls_session"))
                if not user:
                    return self.redirect("/login")
                row = user_row(user)
                if not row:
                    return self.redirect("/login")
                action = (form.get("action") or ["change_password"])[0]
                current = (form.get("current_password") or [""])[0]
                if not server._password_valid(current, row["password_hash"]):
                    return self.redirect("/account?error=1&msg=" + quote("현재 비밀번호가 올바르지 않습니다."))
                if action == "withdraw":
                    if row["role"] == "admin":
                        return self.redirect("/account?error=1&msg=" + quote("관리자 계정은 회원 탈퇴할 수 없습니다."))
                    if (form.get("confirmation") or [""])[0] != "회원탈퇴":
                        return self.redirect("/account?error=1&msg=" + quote("확인문구가 일치하지 않습니다."))
                    with server.connect() as conn:
                        conn.execute("UPDATE users SET status='withdrawn',updated_at=CURRENT_TIMESTAMP WHERE username=?", (user,))
                    bump_session_epoch(user)
                    return self.redirect("/login?error=" + quote("회원 탈퇴가 완료되었습니다."), {"Set-Cookie": clear_cookie()})
                if action != "change_password":
                    return self.redirect("/account?error=1&msg=" + quote("허용되지 않은 계정 작업입니다."))
                new_password = (form.get("new_password") or [""])[0]
                confirm = (form.get("new_password_confirm") or [""])[0]
                if len(new_password) < 10:
                    return self.redirect("/account?error=1&msg=" + quote("새 비밀번호는 10자 이상이어야 합니다."))
                if new_password != confirm:
                    return self.redirect("/account?error=1&msg=" + quote("새 비밀번호 확인값이 일치하지 않습니다."))
                if server._password_valid(new_password, row["password_hash"]):
                    return self.redirect("/account?error=1&msg=" + quote("현재 비밀번호와 다른 비밀번호를 사용해 주세요."))
                with server.connect() as conn:
                    conn.execute("UPDATE users SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE username=?", (server._password_hash(new_password), user))
                bump_session_epoch(user)
                return self.redirect("/account?msg=" + quote("비밀번호가 변경되었습니다."), {"Set-Cookie": secure_cookie(user)})

            return original_do_post(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("계정 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)

    server.make_session = make_session_v223
    server.valid_session = valid_session_v223
    server.login_html = login_html_v223
    server.setup_admin_html = setup_admin_html_v223
    server.base_html = base_html_v223
    server.Handler.do_GET = do_get_v223
    server.Handler.do_POST = do_post_v223
    server._session_user_v223 = session_user
    server._bump_session_epoch_v223 = bump_session_epoch
    server._v223_auth_management = True


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
        from sinsung_v200_reset import reset_data_once
        reset_data_once()
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
        _apply_auth_management(server)
        server.APP_VERSION = APP_VERSION
        server.main(open_browser=False)
    except Exception as exc:
        legacy_app._backend_error = f"내부 대시보드 시작 실패: {exc}"


def _fast_backend_wait(timeout: float = 0.5) -> bool:
    return legacy_app._backend_listening()


legacy_app._background_collect = _background_collect_v220
legacy_app._start_background_collect = _start_background_collect_v220
legacy_app._start_backend = _start_backend_v220
legacy_app._wait_for_backend = _fast_backend_wait
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]