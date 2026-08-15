"""SINSUNG branded login screen patch.

Replaces only the login HTML renderer. Authentication, sessions, rate limiting,
and credential validation remain in server.py.
"""


def apply_patch():
    import html
    import server as s

    def login_html(error=''):
        err = ''
        if error:
            err = f'<div class="errorbox">{html.escape(error)}</div>'

        return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#07111f">
<title>SINSUNG G2B DATA VIEW 로그인</title>
<style>
*{{box-sizing:border-box}}
html,body{{height:100%}}
body{{margin:0;font-family:Arial,"Noto Sans KR",sans-serif;background:
radial-gradient(circle at 20% 10%,rgba(42,112,255,.14),transparent 34%),
radial-gradient(circle at 85% 85%,rgba(0,191,255,.10),transparent 30%),
linear-gradient(135deg,#040b16 0%,#07111f 48%,#030914 100%);color:#eef6ff;display:grid;place-items:center;padding:24px}}
.wrap{{width:min(460px,100%)}}
.card{{background:rgba(6,17,34,.88);border:1px solid rgba(124,170,255,.16);border-radius:26px;padding:42px 38px 34px;box-shadow:0 28px 80px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.03);backdrop-filter:blur(18px)}}
.brand{{text-align:center;margin-bottom:34px}}
.mark{{display:inline-flex;align-items:center;justify-content:center;gap:12px;margin-bottom:8px}}
.word{{font-size:45px;font-weight:900;letter-spacing:3px;line-height:1;background:linear-gradient(90deg,#4ea5ff,#15c5e8);-webkit-background-clip:text;background-clip:text;color:transparent}}
.spark{{width:24px;height:24px;position:relative;display:inline-block}}
.spark:before,.spark:after{{content:"";position:absolute;background:linear-gradient(135deg,#58aaff,#11c4e9);transform:skewY(26deg)}}
.spark:before{{width:13px;height:13px;left:0;top:0}}
.spark:after{{width:10px;height:10px;right:0;bottom:0}}
.subtitle{{font-size:13px;letter-spacing:4px;color:#8ea5c0;font-weight:700}}
.company{{margin-top:8px;font-size:14px;color:#d6e7f7}}
label{{display:block;font-size:13px;font-weight:700;color:#c8d8eb;margin:0 0 8px}}
.field{{position:relative;margin-bottom:20px}}
input{{width:100%;height:52px;border:1px solid #253650;border-radius:13px;background:#07101e;color:#f4f9ff;padding:0 16px;font-size:16px;outline:none;transition:.2s}}
input:focus{{border-color:#2c93ff;box-shadow:0 0 0 3px rgba(44,147,255,.14)}}
.password input{{padding-right:54px}}
.eye{{position:absolute;right:8px;bottom:7px;width:38px;height:38px;border:0;background:transparent;color:#5aa7ff;cursor:pointer;font-size:19px;border-radius:9px}}
.eye:hover{{background:rgba(80,160,255,.08)}}
button.login{{width:100%;height:54px;border:0;border-radius:15px;background:linear-gradient(90deg,#2877f0,#13b9d7);color:#fff;font-size:16px;font-weight:800;letter-spacing:.5px;cursor:pointer;box-shadow:0 10px 30px rgba(27,138,235,.22);transition:.2s;margin-top:6px}}
button.login:hover{{transform:translateY(-1px);filter:brightness(1.06)}}
.errorbox{{background:rgba(239,68,68,.11);border:1px solid rgba(248,113,113,.35);color:#fecaca;padding:11px 13px;border-radius:11px;font-size:13px;margin-bottom:18px}}
.footer{{text-align:center;margin-top:24px;font-size:12px;color:#7188a3;line-height:1.7}}
.secure{{display:inline-flex;align-items:center;gap:6px;color:#8aa0ba}}
@media(max-width:520px){{body{{padding:14px}}.card{{padding:34px 22px 28px;border-radius:22px}}.word{{font-size:38px}}}}
</style>
</head>
<body>
<div class="wrap">
  <section class="card">
    <div class="brand">
      <div class="mark"><span class="word">SINSUNG</span><span class="spark" aria-hidden="true"></span></div>
      <div class="subtitle">G2B DATA VIEW</div>
      <div class="company">신성라이텍 관급조달 통합관리</div>
    </div>
    {err}
    <form method="post" action="/login" autocomplete="on">
      <label for="username">사용자명</label>
      <div class="field"><input id="username" name="username" autocomplete="username" required autofocus></div>
      <label for="password">비밀번호</label>
      <div class="field password">
        <input id="password" type="password" name="password" autocomplete="current-password" required>
        <button class="eye" type="button" aria-label="비밀번호 보기" onclick="var p=document.getElementById('password');p.type=p.type==='password'?'text':'password';this.textContent=p.type==='password'?'◉':'◌'">◉</button>
      </div>
      <button class="login" type="submit">로그인</button>
    </form>
    <div class="footer"><span class="secure">● SINSUNG SECURE ACCESS</span><br>Authorized users only</div>
  </section>
</div>
</body>
</html>'''

    s.login_html = login_html
    return True
