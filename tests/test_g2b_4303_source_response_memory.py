"""Source-free transport allocation bounds; canonical attestation is unchanged."""
import ctypes
import gc
import datetime as dt
import hashlib
import io
import json
import tracemalloc

import pytest

import lofin_vnext_http as lofin
from vnext_source_guard import source_transport_result_sha256


@pytest.fixture(scope="module", autouse=True)
def release_transport_fixture_heap():
    """Release synthetic allocation peaks after all per-test fixtures unwind."""
    yield
    # These tests deliberately allocate historical rows/canonical page copies.
    # Function-scoped transport and monkeypatch closures have already been released;
    # do not carry their allocator high-water mark into real-RSS runtime tests.
    gc.collect()
    try:
        trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def _legacy_digest(rows, total):
    return hashlib.sha256(json.dumps(
        {"items": rows, "reported_total": total}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("total", [None, 0, 999])
def test_streaming_attestation_preserves_canonical_digest(total):
    rows = [{
        "한글": "조명 나라장터 😀 𐐷", "escape": '\\"\n\t',
        "nested": {"b": [True, False, None, 1, -2, 0.125, -0.0], "a": []},
        "date": dt.date(2026, 10, 16),
    }]
    expected = _legacy_digest(rows, total)
    assert source_transport_result_sha256(rows, total) == expected
    assert source_transport_result_sha256(
        [dict(reversed(list(rows[0].items())))], total
    ) == expected
    assert source_transport_result_sha256([], total) == _legacy_digest([], total)


def _peak(call):
    tracemalloc.start()
    try:
        result = call()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_page_hash_avoids_whole_page_temporary_copies():
    # Inputs exist before tracing: measure transient hash allocations only,
    # not process RSS, cgroup occupancy or a promise of production admission.
    rows = [{"id": index, "name": f"나라장터 조명 수집 {index} " * 180}
            for index in range(999)]
    old_digest, old_peak = _peak(lambda: _legacy_digest(rows, 999))
    digest, peak = _peak(lambda: source_transport_result_sha256(rows, 999))
    assert digest == old_digest
    assert peak < old_peak / 4


def test_lofin_response_read_has_exact_byte_ceiling(monkeypatch):
    monkeypatch.setattr(lofin, "MAX_RESPONSE_BYTES", 32)
    response = io.BytesIO(b"x" * 32)
    assert lofin._read_response_limited(response) == b"x" * 32
    oversized = io.BytesIO(b"x" * 4096)
    with pytest.raises(lofin.LofinVNextApiError, match="^SOURCE_RESPONSE_TOO_LARGE$"):
        lofin._read_response_limited(oversized)
    assert oversized.tell() == 33


def test_oversized_lofin_page_never_parses_or_attests_success(monkeypatch):
    calls = []
    class Response(io.BytesIO):
        def read(self, size=-1):
            calls.append(("read", size))
            assert size == 33
            return super().read(size)
    monkeypatch.setattr(lofin, "MAX_RESPONSE_BYTES", 32)
    monkeypatch.setattr(lofin, "require_source_request_context", lambda: None)
    monkeypatch.setattr(lofin, "_quota_take", lambda: calls.append(("quota", 1)))
    monkeypatch.setattr(lofin.urllib.request, "urlopen", lambda *a, **k: Response(b"x" * 4096))
    monkeypatch.setattr(lofin, "_record_connection_probe", lambda *a: calls.append(a))
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized response cannot become a parsed/attested page")
    monkeypatch.setattr(lofin, "parse_response", forbidden)
    monkeypatch.setattr(lofin, "record_source_transport_success", forbidden)
    with pytest.raises(lofin.LofinVNextApiError, match="^SOURCE_RESPONSE_TOO_LARGE$"):
        lofin._request({"Key": "test-secret"}, retries=3)
    assert calls == [("quota", 1), ("read", 33), ("FAILED", "SOURCE_RESPONSE_TOO_LARGE")]
