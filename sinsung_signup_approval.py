"""Minimal signup + admin approval and role guard for the proven 2.2 runtime.

Scope:
- public signup request -> users.role='user', users.status='pending'
- pending users cannot log in because the existing login already requires status='active'
- admin-only member page can approve pending users -> status='active'
- collection/settings mutation endpoints are admin-only

No collector/scheduler/database-schema behavior is changed here.
"""
from urllib.parse import parse_qs, quote, urlparse


def apply_signup_approval():
    import server

    if getattr(server, "_signup_approval_minimal_applied", False):
        return

    original_login_html = server.login_html
    original_base_html = server.base_html
    original_require_auth = server.Handler.require_auth
    original_do_get = server.Handler.do_GET
    original_do_post = server.Handler.do_POST
    request_state = server.threading.local()

    admin_only_get = {"/settings"}
    admin_only_post = {
        "/settings",
        "/sync-shop",
        "/sync-bids",
        "/sync-services",
        "/api-test",
        "/backfill",
        "/clear-samples",
        "/reset-shopping-data",
        "/budget-settings",
        "/sync-budget",
        "/budget-api-test",
    }

    def _current_username(handler):
        token = handler.cookie("ls_session")
        if not token or not server.valid_session(token):
            return ""
        try:
            p64, _ = token.split(".", 1)
            payload = server._b64d(p64).decode("utf-8")
            username, _ = payload.rsplit("|", 1)
            return username
        except Exception:
            return ""

    def _is_admin(handler):
        username = _current_username(handler)
        if not username:
            return False
        with server.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE username=? AND role='admin' AND status='active'",
                (username,),
            ).fetchone()
        return bool(row)

    def _valid_username(username):
        return 4 <= len(username) <= 50 and all(ch.isalnum() or ch in "._-" for ch in username)

    def signup_html(message="", error=False, submitted=False):
        flash = ""
        if message:
            flash = f'<div class="flash {"error" if error else "ok"}">{server.esc(message)}</div>'
        if submitted:
            body = f'''{flash}<p>회원가입 신청이 접수되었습니다.</p><p>관리자가 승인한 후 로그인할 수 있습니다.</p><p><a href="/login">로그인 화면으로 돌아가기</a></p>'''
        else:
            body = f'''{flash}<form method="post" action="/signup">{server.csrf_input('/signup')}<label>아이디<input name="username" minlength="4" maxlength="50" autocomplete="username" required></label><label>비밀번호<input type="password" name="password" minlength="10" autocomplete="new-password" required></label><label>비밀번호 확인<input type="password" name="password_confirm" minlength="10" autocomplete="new-password" required></label><button class="primary" type="submit">회원가입 신청</button></form><p style="margin-top:16px"><a href="/login">로그인으로 돌아가기</a></p>'''
        return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SINSUNG 회원가입</title><link rel="stylesheet" href="/static/style.css"></head><body><main class="authpage"><section class="card authcard"><div class="authbrand">SINSUNG</div><h2>회원가입 신청</h2><p>관리자 승인 후 사용 가능합니다.</p>{body}</section></main></body></html>'''

    def login_html_minimal(error=""):
        page = original_login_html(error)
        if 'href="/signup"' not in page and "</form>" in page:
            signup_button = '''<div style="margin-top:14px"><a href="/signup" style="display:block;text-align:center;padding:13px 16px;border:1px solid #2a6fb8;border-radius:10px;color:#7cc7ff;text-decoration:none;font-weight:800">회원가입 신청</a></div>'''
            page = page.replace("</form>", "</form>" + signup_button, 1)
        return page

    def base_html_minimal(content, active="대시보드", flash="", flash_error=False):
        page = original_base_html(content, active, flash, flash_error)
        is_admin = bool(getattr(request_state, "is_admin", False))
        needle = '<a href="/settings">설정</a> <span>/</span> <a href="/dashboard">새로고침</a>'
        if is_admin:
            if needle in page and 'href="/admin/users"' not in page:
                page = page.replace(
                    needle,
                    '<a href="/settings">설정</a> <span>/</span> <a href="/admin/users">회원관리</a> <span>/</span> <a href="/dashboard">새로고침</a>',
                    1,
                )
        else:
            page = page.replace('<a href="/settings">설정</a> <span>/</span> ', "", 1)
            # Budget data remains readable, but collection/API controls are admin-only.
            hide_admin_forms = '''<style>form[action="/budget-settings"],form[action="/sync-budget"],form[action="/budget-api-test"]{display:none!important}</style>'''
            if "</head>" in page:
                page = page.replace("</head>", hide_admin_forms + "</head>", 1)
        return page

    def admin_users_html(message="", error=False):
        with server.connect() as conn:
            rows = conn.execute(
                "SELECT username,role,status,created_at,updated_at FROM users ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, id DESC"
            ).fetchall()
        status_labels = {"pending": "승인대기", "active": "사용중"}
        body_rows = []
        for row in rows:
            role_label = "관리자" if row["role"] == "admin" else "일반사용자"
            status_label = status_labels.get(row["status"], row["status"])
            action = "-"
            if row["role"] != "admin" and row["status"] == "pending":
                action = f'''<form method="post" action="/admin/users" style="display:inline">{server.csrf_input('/admin/users')}<input type="hidden" name="username" value="{server.esc(row['username'])}"><button class="primary" type="submit">승인</button></form>'''
            body_rows.append(
                f'<tr><td>{server.esc(row["username"])}</td><td>{role_label}</td><td>{server.esc(status_label)}</td><td>{server.esc(row["created_at"])}</td><td>{action}</td></tr>'
            )
        flash = ""
        if message:
            flash = f'<div class="flash {"error" if error else "ok"}">{server.esc(message)}</div>'
        body = f'''{server.pathbar('/admin/users','sinsung / members')}<section class="card page"><h2>회원관리</h2>{flash}<div class="notice">회원가입 신청은 승인대기로 저장됩니다. 관리자가 승인한 일반사용자만 로그인할 수 있습니다.</div><div class="tablewrap"><table><thead><tr><th>아이디</th><th>권한</th><th>상태</th><th>가입일</th><th>관리</th></tr></thead><tbody>{''.join(body_rows) or '<tr><td colspan="5" class="empty">회원 없음</td></tr>'}</tbody></table></div></section>'''
        return server.base_html(body, "")

    def require_auth_minimal(self, path):
        if path == "/signup":
            return False
        return original_require_auth(self, path)

    def do_get_minimal(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            request_state.is_admin = _is_admin(self)
            if path == "/signup":
                if server.users_empty():
                    return self.redirect("/setup-admin")
                if self.authenticated():
                    return self.redirect("/dashboard")
                qs = parse_qs(parsed.query)
                return self.send_bytes(
                    signup_html(
                        (qs.get("msg") or [""])[0],
                        (qs.get("error") or ["0"])[0] == "1",
                        (qs.get("submitted") or ["0"])[0] == "1",
                    )
                )
            if path == "/admin/users":
                if self.require_auth(path):
                    return
                if not request_state.is_admin:
                    return self.send_bytes("관리자만 접근할 수 있습니다.", "text/plain; charset=utf-8", 403)
                qs = parse_qs(parsed.query)
                return self.send_bytes(
                    admin_users_html(
                        (qs.get("msg") or [""])[0],
                        (qs.get("error") or ["0"])[0] == "1",
                    )
                )
            if path in admin_only_get:
                if self.require_auth(path):
                    return
                if not request_state.is_admin:
                    return self.send_bytes("관리자만 접근할 수 있습니다.", "text/plain; charset=utf-8", 403)
            return original_do_get(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("회원 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)
        finally:
            request_state.is_admin = False

    def do_post_minimal(self):
        try:
            path = urlparse(self.path).path
            if path == "/signup":
                form = self.parse_post()
                if not server.valid_csrf("/signup", form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)
                if server.users_empty():
                    return self.redirect("/setup-admin")
                username = (form.get("username") or [""])[0].strip()
                password = (form.get("password") or [""])[0]
                confirm = (form.get("password_confirm") or [""])[0]
                if not _valid_username(username):
                    return self.redirect("/signup?error=1&msg=" + quote("아이디는 4~50자의 한글·영문·숫자·._-만 사용할 수 있습니다."))
                if len(password) < 10:
                    return self.redirect("/signup?error=1&msg=" + quote("비밀번호는 10자 이상이어야 합니다."))
                if password != confirm:
                    return self.redirect("/signup?error=1&msg=" + quote("비밀번호 확인값이 일치하지 않습니다."))
                with server.connect() as conn:
                    exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
                    if exists:
                        return self.redirect("/signup?error=1&msg=" + quote("이미 사용 중이거나 승인대기 중인 아이디입니다."))
                    conn.execute(
                        "INSERT INTO users(username,password_hash,role,status) VALUES (?,?, 'user','pending')",
                        (username, server._password_hash(password)),
                    )
                return self.redirect("/signup?submitted=1&msg=" + quote("회원가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."))

            if path == "/admin/users":
                if self.require_auth(path):
                    return
                if not _is_admin(self):
                    return self.send_bytes("관리자만 접근할 수 있습니다.", "text/plain; charset=utf-8", 403)
                form = self.parse_post()
                if not server.valid_csrf("/admin/users", form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)
                username = (form.get("username") or [""])[0].strip()
                with server.connect() as conn:
                    row = conn.execute(
                        "SELECT role,status FROM users WHERE username=?",
                        (username,),
                    ).fetchone()
                    if not row or row["role"] == "admin":
                        return self.redirect("/admin/users?error=1&msg=" + quote("승인할 수 없는 계정입니다."))
                    if row["status"] != "pending":
                        return self.redirect("/admin/users?error=1&msg=" + quote("이미 처리된 계정입니다."))
                    conn.execute(
                        "UPDATE users SET status='active',updated_at=CURRENT_TIMESTAMP WHERE username=? AND role='user' AND status='pending'",
                        (username,),
                    )
                return self.redirect("/admin/users?msg=" + quote("회원 승인이 완료되었습니다."))

            if path in admin_only_post:
                if self.require_auth(path):
                    return
                if not _is_admin(self):
                    return self.send_bytes("관리자만 접근할 수 있습니다.", "text/plain; charset=utf-8", 403)

            return original_do_post(self)
        except Exception:
            server.traceback.print_exc()
            return self.send_bytes("회원 처리 중 오류가 발생했습니다.", "text/plain; charset=utf-8", 500)

    server.login_html = login_html_minimal
    server.base_html = base_html_minimal
    server.Handler.require_auth = require_auth_minimal
    server.Handler.do_GET = do_get_minimal
    server.Handler.do_POST = do_post_minimal
    server._signup_approval_minimal_applied = True
