"""Runtime branding patch: expose the application as 신성라이텍 without changing data logic."""

PROGRAM_NAME = "신성라이텍"
APP_TITLE = "신성라이텍 G2B DATA VIEW"


def apply_patch():
    import server as s

    original_login_html = s.login_html
    original_base_html = s.base_html

    def _brand(text):
        return (text
                .replace("LIGHTING SKETCH G2B", APP_TITLE)
                .replace("LIGHTING SKETCH 내부 대시보드", f"{PROGRAM_NAME} 내부 대시보드")
                .replace("LIGHTING SKETCH", PROGRAM_NAME)
                .replace("lighting-sketch", "sinsung-lightech"))

    def login_html(error=''):
        return _brand(original_login_html(error))

    def base_html(content, active='대시보드', flash='', flash_error=False):
        return _brand(original_base_html(content, active, flash, flash_error))

    s.login_html = login_html
    s.base_html = base_html
    return True
