"""Fix nationwide region filtering without changing existing regional filters."""
import urllib.parse

VERSION = "2.4.3-sinsung-region-all"


def apply_region_fix():
    """Preserve an explicit empty region= query value as the nationwide filter.

    urllib.parse.parse_qs drops blank values by default. The dashboard uses an
    empty region value to mean '전국', so dropping it incorrectly falls back to
    the configured default region. This wrapper preserves blank values while
    keeping all existing parsing behavior intact.
    """
    import server as s

    def parse_qs_keep_blank_values(qs, **kwargs):
        kwargs.setdefault("keep_blank_values", True)
        return urllib.parse.parse_qs(qs, **kwargs)

    s.parse_qs = parse_qs_keep_blank_values
    s.APP_VERSION = VERSION
    return s
