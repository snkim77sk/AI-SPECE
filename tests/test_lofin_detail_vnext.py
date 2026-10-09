import datetime as dt

import pytest

import lofin_detail_vnext
import vnext_live_gate
import vnext_source_guard


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    def __init__(self, url, text):
        self._url = url
        self._raw = text.encode("utf-8")
        self.headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._raw if size is None or size < 0 else self._raw[:size]

    def geturl(self):
        return self._url


def _context(monkeypatch, *, date="2026-10-07"):
    monkeypatch.setattr(
        vnext_live_gate,
        "runtime_source_sha",
        lambda: "e" * 40,
    )
    monkeypatch.setattr(
        vnext_source_guard,
        "_today_kst",
        lambda: dt.date(2026, 10, 10),
    )
    return vnext_source_guard.lofin_project_detail_source_context(
        snapshot_date=date,
        max_requests=1,
    )


def _url():
    return lofin_detail_vnext.project_detail_url(
        project_code="6280000201930030",
        org_code="6280000",
        fiscal_year=2026,
        snapshot_date="2026-10-07",
    )


def test_project_detail_url_is_exact_public_lofin_identity():
    url = _url()
    assert url.startswith(
        "https://www.lofin365.go.kr/portal/LF3120204.do?"
    )
    assert "dbizCd=6280000201930030" in url
    assert "lafCd=6280000" in url
    assert "fyr=2026" in url
    assert "inqYmd=20261007" in url


def test_project_detail_parser_extracts_compact_organization_evidence():
    html = """
    <script>
    var list2 = {"x": 1};
    var list3 = {
      "slngkNm": "도시철도건설본부",
      "deptNm": "도시철도건설본부_공사시설부",
      "dbizNm": "서울도시철도7호선 청라국제도시 연장",
      "enfcSujCn": "인천광역시 도시철도건설본부 공사시설부",
      "nested": {"text": "brace }; in string"}
    };
    var list4 = {};
    </script>
    """
    result = lofin_detail_vnext.parse_project_detail_html(html)
    assert result["department_name"] == "도시철도건설본부 > 공사시설부"
    assert result["department_source_name"] == "도시철도건설본부_공사시설부"
    assert result["bureau_name"] == "도시철도건설본부"
    assert result["executor_name"].endswith("공사시설부")
    assert result["project_name"] == "서울도시철도7호선 청라국제도시 연장"
    assert len(result["source_payload_sha256"]) == 64


def test_project_detail_direct_request_is_blocked_before_network(monkeypatch):
    monkeypatch.setattr(
        lofin_detail_vnext.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("network must not be touched")
        ),
    )
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="CONTEXT_REQUIRED",
    ):
        lofin_detail_vnext.fetch_project_detail(
            project_code="6280000201930030",
            org_code="6280000",
            fiscal_year=2026,
            snapshot_date="2026-10-07",
        )


def test_project_detail_context_allows_one_attested_exact_request(monkeypatch):
    url = _url()
    html = (
        '<script>var list3 = {'
        '"slngkNm":"도시철도건설본부",'
        '"deptNm":"도시철도건설본부_공사시설부",'
        '"dbizNm":"서울도시철도7호선 청라국제도시 연장"'
        '};</script>'
    )
    calls = []

    def fake_open(request, timeout):
        calls.append((request.full_url, timeout))
        return _Response(url, html)

    monkeypatch.setattr(
        lofin_detail_vnext.urllib.request,
        "urlopen",
        fake_open,
    )

    with _context(monkeypatch):
        result = lofin_detail_vnext.fetch_project_detail(
            project_code="6280000201930030",
            org_code="6280000",
            fiscal_year=2026,
            snapshot_date="2026-10-07",
        )
        state = vnext_source_guard.current_source_request_context()
        assert result["department_name"].endswith("공사시설부")
        assert state["requests_used"] == 1
        assert state["transport_successes_used"] == 1
        assert state["permits_used"] == 1

        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="BUDGET_EXHAUSTED",
        ):
            lofin_detail_vnext.fetch_project_detail(
                project_code="6280000201930030",
                org_code="6280000",
                fiscal_year=2026,
                snapshot_date="2026-10-07",
            )
    assert len(calls) == 1


def test_project_detail_guard_rejects_wrong_target_before_permit(monkeypatch):
    with _context(monkeypatch):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="TARGET_INVALID",
        ):
            vnext_source_guard.require_source_request_context(
                lofin_detail_url=(
                    "https://example.com/portal/LF3120204.do"
                    "?dbizCd=P1&lafCd=6280000&fyr=2026&inqYmd=20261007"
                )
            )
        state = vnext_source_guard.current_source_request_context()
        assert state["permits_used"] == 0
        assert state["requests_used"] == 0


def test_project_detail_parser_rejects_missing_department():
    with pytest.raises(
        lofin_detail_vnext.LofinProjectDetailError,
        match="DEPARTMENT_MISSING",
    ):
        lofin_detail_vnext.parse_project_detail_html(
            '<script>var list3={"dbizNm":"사업"};</script>'
        )
