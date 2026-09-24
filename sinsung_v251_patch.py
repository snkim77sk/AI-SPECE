"""v2.5.1 stability patch: explicit nationwide sentinel and region normalization.

The former budget API connection-box HTML injection was removed in Phase 3.
Budget admin controls are rendered by the canonical budget page path so role
filtering cannot be bypassed by a later HTML wrapper.
"""
import datetime as dt
import urllib.parse

VERSION = "2.5.1-sinsung-budget-region-stable"
ALL_REGION = "__ALL__"


def _normalize_region(value):
    text = str(value or "").strip()
    if text in ("", ALL_REGION, "전국"):
        return ""
    return text


def apply_v251_patch():
    import server as s
    import sinsung_budget_monitor as bm

    # Keep blank query values if an older link still contains region=.
    def parse_qs_stable(qs, **kwargs):
        kwargs.setdefault("keep_blank_values", True)
        return urllib.parse.parse_qs(qs, **kwargs)

    s.parse_qs = parse_qs_stable

    # New forms use an explicit sentinel instead of an empty value. This prevents
    # reverse proxies/frameworks from dropping region= and falling back to the
    # configured default region (e.g. 인천광역시).
    def regopts(region):
        current = _normalize_region(region)
        options = [
            f'<option value="{ALL_REGION}"{" selected" if current == "" else ""}>전국</option>'
        ]
        for r in s.REGIONS:
            if not r:
                continue
            selected = " selected" if current == r else ""
            options.append(f'<option value="{s.esc(r)}"{selected}>{s.esc(r)}</option>')
        return "".join(options)

    s.regopts = regopts

    def date_params(qs, days=14):
        end = (qs.get("end") or [s.TODAY.isoformat()])[0]
        start = (qs.get("start") or [(s.TODAY - dt.timedelta(days=days)).isoformat()])[0]
        if "region" in qs:
            region = _normalize_region((qs.get("region") or [ALL_REGION])[0])
        else:
            region = _normalize_region(s.get_setting("default_region", "인천광역시"))
        return start, end, region

    s.date_params = date_params

    # Preserve 전국 across links/tabs/CSV URLs as __ALL__ while keeping the
    # internal SQL filter value as an empty string (= no regional restriction).
    def link(path, **kw):
        params = dict(kw)
        if "region" in params and _normalize_region(params.get("region")) == "":
            params["region"] = ALL_REGION
        return path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")

    def urlencode_stable(query, doseq=False, **kwargs):
        if isinstance(query, dict):
            query = dict(query)
            if "region" in query and _normalize_region(query.get("region")) == "":
                query["region"] = ALL_REGION
        return urllib.parse.urlencode(query, doseq=doseq, **kwargs)

    s.link = link
    s.urlencode = urlencode_stable

    # The budget monitor reads region directly from qs, so normalize the same
    # explicit nationwide sentinel there as well.
    def budget_filters(server, qs):
        try:
            year = int((qs.get("year") or [str(server.TODAY.year)])[0])
        except Exception:
            year = server.TODAY.year
        if "region" in qs:
            region = _normalize_region((qs.get("region") or [ALL_REGION])[0])
        else:
            region = _normalize_region(server.get_setting("default_region", "인천광역시"))
        category = (qs.get("category") or [""])[0].strip()
        status = (qs.get("status") or ["all"])[0].strip() or "all"
        q = (qs.get("q") or [""])[0].strip()
        return year, region, category, status, q

    bm._budget_filters = budget_filters

    # Phase 3: do not wrap s.budgets_html here. The old wrapper inserted admin
    # forms after base_html role filtering and could expose those controls to a
    # normal member. Region/query stabilization above remains active.
    return s
