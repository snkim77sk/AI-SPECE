"""Show budget monitor redirect messages without changing core budget logic."""


def apply_budget_flash_fix():
    import server as s

    original = s.budgets_html

    def budgets_html(qs):
        page = original(qs)
        message = (qs.get("msg") or [""])[0]
        if not message:
            return page
        is_error = (qs.get("error") or ["0"])[0] == "1"
        flash = f'<div class="flash {"error" if is_error else "ok"}">{s.esc(message)}</div>'
        marker = "<main>"
        if marker in page:
            return page.replace(marker, marker + flash, 1)
        return page

    s.budgets_html = budgets_html
    return s
