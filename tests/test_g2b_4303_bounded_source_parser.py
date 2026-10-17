"""Bound source expansion before attestation/transaction; never trim page rows."""
import ctypes
import gc
import hashlib
import io
import json
import tracemalloc

import pytest

import lofin_vnext_http as lofin
import memory_guard
import g2b_dynamic_source_budget as dynamic
import source_response_parser as parser
import vnext_http as g2b
from vnext_source_guard import source_transport_result_sha256

REAL_ALLOCATION_CHECKPOINT = dynamic.allocation_checkpoint
REAL_DYNAMIC_ENABLED = memory_guard.source_dynamic_budget_enabled


@pytest.fixture(autouse=True)
def bounded_parser_runtime(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setattr(memory_guard, "source_dynamic_budget_enabled",
                        lambda: dynamic.enabled("budget"))
    # Functional parser tests do not depend on this host's cgroup. Separate tests
    # exercise the real helper with scripted byte-exact cgroup snapshots.
    monkeypatch.setattr(dynamic, "allocation_checkpoint", lambda reserve_bytes=0, **kwargs: None)
    monkeypatch.setattr(dynamic, "assert_worker_phase", lambda *args, **kwargs: None)


def _release_parser_fixture_heap():
    gc.collect()
    try:
        trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


@pytest.fixture(autouse=True)
def release_large_parser_fixture_heap():
    # Adversarial fixtures deliberately allocate 16MiB documents and exercise
    # Unicode widening. Run after monkeypatch.undo(), so response closures no
    # longer retain those documents when pages are returned to the allocator.
    yield
    _release_parser_fixture_heap()


@pytest.fixture
def monkeypatch(release_large_parser_fixture_heap):
    # This module-only equivalent of pytest's fixture makes teardown ordering
    # explicit: undo captured transport callbacks before collecting/trimming.
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.fixture(scope="module", autouse=True)
def release_parser_module_heap():
    # Also run once after all function-scoped fixtures have been torn down.
    yield
    _release_parser_fixture_heap()


def _g2b_json(rows):
    return json.dumps({"response": {"header": {"resultCode": "00"},
        "body": {"items": rows, "totalCount": len(rows)}}}, ensure_ascii=False).encode()


def _lofin_json(rows, service="QWGJK"):
    return json.dumps({service: [{"head": [{"list_total_count": len(rows)},
        {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상"}}]}, {"row": rows}]},
        ensure_ascii=False).encode()


def _xml(rows, source="g2b"):
    tag = "item" if source == "g2b" else "row"
    body = b"".join((f"<{tag}><name>{row['name']}</name></{tag}>").encode() for row in rows)
    if source == "g2b":
        return (b"<response><header><resultCode>00</resultCode></header><body><items>" +
                body + b"</items><totalCount>" + str(len(rows)).encode() + b"</totalCount></body></response>")
    return (b"<QWGJK><head><list_total_count>" + str(len(rows)).encode() +
            b"</list_total_count><RESULT><CODE>INFO-000</CODE><MESSAGE>OK</MESSAGE></RESULT></head>" +
            body + b"</QWGJK>")


def _digest(rows, total):
    return hashlib.sha256(json.dumps({"items": rows, "reported_total": total},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@pytest.fixture
def checkpoints(monkeypatch):
    calls = []
    monkeypatch.setattr(dynamic, "allocation_checkpoint",
                        lambda reserve_bytes=0, phase="allocation": calls.append(reserve_bytes))
    return calls


@pytest.mark.parametrize("source", ["g2b", "lofin"])
@pytest.mark.parametrize("form", ["json", "xml"])
def test_full_official_page_preserves_rows_and_canonical_attestation(source, form, checkpoints):
    # Official LOFIN/G2B page sizes stay 1,000/999; no low-memory downsizing.
    rows = [{"name": f"나라장터 😀 {i}"} for i in range(1000)]
    raw = (_g2b_json(rows) if source == "g2b" else _lofin_json(rows)) if form == "json" else _xml(rows, source)
    module = g2b if source == "g2b" else lofin
    parsed, total, *_ = module.parse_response(module._read_response_limited(io.BytesIO(raw)))
    assert parsed == rows
    assert total == 1000
    assert source_transport_result_sha256(parsed, total) == _digest(rows, total)
    assert checkpoints and max(checkpoints) >= parser.PARSE_RESERVE_BYTES


@pytest.mark.parametrize("form", ["json", "xml"])
def test_near_16mib_page_avoids_document_unicode_and_xml_tree_copies(form, monkeypatch):
    # Only one non-BMP character: formerly this widened the ENTIRE 16 MiB JSON
    # Unicode document to 64 MiB, in addition to bytes and final field objects.
    rows = [{"name": "A" * 65_000} for _ in range(256)]
    rows[-1]["name"] = "A" * 64_995 + "😀"
    raw = _g2b_json(rows) if form == "json" else _xml(rows)
    assert 15 * 1024 * 1024 < len(raw) <= g2b.MAX_RESPONSE_BYTES
    del rows
    monkeypatch.setattr(g2b, "_record_result", lambda *a: None)
    tracemalloc.start()
    try:
        parsed, total = g2b.parse_response(raw)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert total == len(parsed) == 256
    assert parsed[-1]["name"].endswith("😀")
    # Includes retained output. The former whole-document decode alone was 64MiB.
    assert peak < 24 * 1024 * 1024


@pytest.mark.parametrize("form", ["json", "xml"])
def test_16mib_single_field_is_rejected_before_native_parser_expansion(form, checkpoints):
    raw = _g2b_json([{"name": "x" * (15 * 1024 * 1024) + "😀"}]) if form == "json" else _xml([{"name": "x" * (15 * 1024 * 1024)}])
    tracemalloc.start()
    try:
        with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT:field bytes"):
            g2b.parse_response(raw)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize("raw", [
    b'{"response":{"body":' + b'[' * 17 + b']' * 17 + b'}}',
    _g2b_json([{"name": "x"}] * 1001),
    _g2b_json([{str(i): "x" for i in range(257)}]),
    b'{"response":' + b'1' * 257 + b'}',
    b'<response>' + b'<x>' * 17 + b'</x>' * 17 + b'</response>',
    b'<response><!--' + b'>x' * 40_000 + b'--></response>',
    b'<response x="' + b'>' * 70_000 + b'"/>',
    b'<response><![CDATA[' + b'>' * 70_000 + b']]></response>',
], ids=["depth-json", "rows", "fields", "number", "depth-xml", "comment", "attribute", "cdata"])
def test_expansion_limits_hold_before_partial_result(raw, checkpoints):
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT"):
        g2b.parse_response(raw)


@pytest.mark.parametrize("raw", [b'{"response":', b'<response><x></response>',
    b'<!DOCTYPE response [<!ENTITY x "secret">]><response>&x;</response>',
    b'{"a":"\xff"}', b'{"a":1} trailing', b'<response/> trailing'])
def test_invalid_input_never_becomes_empty_page(raw):
    with pytest.raises(g2b.VNextApiError):
        g2b.parse_response(raw)
    with pytest.raises(lofin.LofinVNextApiError):
        lofin.parse_response(raw)


def test_retained_limit_is_fail_closed(monkeypatch, checkpoints):
    monkeypatch.setattr(parser, "MAX_RETAINED_BYTES", 1024)
    with pytest.raises(memory_guard.MemoryPressureError, match="retained bytes"):
        g2b.parse_response(_g2b_json([{"name": "a" * 512}] * 5))


def test_budget_checkpoint_precedes_each_bounded_transport_read(monkeypatch, checkpoints):
    events = []
    monkeypatch.setattr(dynamic, "allocation_checkpoint", lambda reserve_bytes=0, phase="allocation": events.append(("check", reserve_bytes)))
    class Response(io.BytesIO):
        def read(self, size=-1):
            assert events[-1][0] == "check"
            assert 0 < size <= parser.CHUNK_BYTES
            events.append(("read", size))
            return super().read(size)
    raw = b"x" * 100_000
    assert g2b._read_response_limited(Response(raw)) == raw
    assert len([e for e in events if e[0] == "read"]) > 1


def test_no_unbounded_fallback_in_dynamic_mode(checkpoints):
    class Response:
        def read(self):
            raise AssertionError("must never call unbounded read")
    with pytest.raises(memory_guard.MemoryPressureError, match="unbounded transport read"):
        g2b._read_response_limited(Response())


@pytest.mark.parametrize("module", [g2b, lofin])
def test_low_headroom_never_reads_or_attests_a_page(module, monkeypatch, checkpoints):
    def hold(reserve_bytes=0, phase="allocation"):
        raise memory_guard.MemoryPressureError("SOURCE_DYNAMIC_BUDGET")
    monkeypatch.setattr(dynamic, "allocation_checkpoint", hold)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): raise AssertionError("cannot allocate response")
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(module, "require_source_request_context", lambda **k: None)
    monkeypatch.setattr(module, "_quota_take", lambda *a: None)
    def no_attestation(*a, **k):
        raise AssertionError("no successful transport/transaction after hold")
    monkeypatch.setattr(module, "record_source_transport_success", no_attestation)
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_DYNAMIC_BUDGET"):
        if module is g2b:
            module.request("https://example.invalid", "shopping", retries=1)
        else:
            module._request({"Key": "test"}, service_code="AIDFA", retries=1)


def test_json_number_types_and_negative_zero_preserve_digest():
    rows = [{"n": [1, 2**80, 0.125, -0.0, True, False, None]}]
    got, total = g2b.parse_response(_g2b_json(rows))
    assert source_transport_result_sha256(got, total) == _digest(rows, 1)


def test_namespace_xml_and_no_data_semantics():
    raw = b'<response xmlns="urn:test"><header><resultCode>00</resultCode></header><body><totalCount>0</totalCount></body></response>'
    assert g2b.parse_response(raw) == ([], 0)
    assert lofin.parse_response(b'<RESULT><CODE>INFO-200</CODE><MESSAGE>empty</MESSAGE></RESULT>') == ([], 0, "INFO-200", "empty")


def test_lofin_multiple_row_arrays_cannot_evade_page_limit(checkpoints):
    raw = json.dumps({"QWGJK": [{"head": [{"RESULT": {"CODE": "INFO-000"}}]},
        {"row": [{}] * 600}, {"row": [{}] * 600}]}).encode()
    with pytest.raises(lofin.LofinVNextApiError, match="duplicate row container"):
        lofin.parse_response(raw)


def test_fragmented_dynamic_reads_do_not_truncate_source(checkpoints):
    raw = _g2b_json([{"name": "fragmented 😀"}])
    class Fragmented(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 7))
    parsed, total = g2b.parse_response(g2b._read_response_limited(Fragmented(raw)))
    assert parsed == [{"name": "fragmented 😀"}]
    assert total == 1


def test_utf16_cannot_bypass_xml_byte_guard():
    with pytest.raises(g2b.VNextResponseError, match="PARSE"):
        g2b.parse_response('<response><x>text</x></response>'.encode('utf-16'))


def test_leading_bom_and_whitespace_xml_declaration_stays_accepted():
    assert g2b.parse_response(b'\xef\xbb\xbf \n<?xml version="1.0"?><response><header><resultCode>00</resultCode></header><body><totalCount>0</totalCount></body></response>') == ([], 0)


def test_large_legacy_fixed_response_double_stays_compatible(monkeypatch):
    monkeypatch.delenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", raising=False)
    raw = _g2b_json([{"name": "x" * 20_000}])
    class Response:
        def read(self, *args): return raw
    assert g2b._read_response_limited(Response()) == raw


def test_dynamic_transport_must_honor_requested_read_size(checkpoints):
    class Response:
        def read(self, size): return b"x" * (size + 1)
    with pytest.raises(memory_guard.MemoryPressureError, match="transport read size"):
        g2b._read_response_limited(Response())


def test_xml_late_unicode_widening_counts_against_retained_limit(monkeypatch, checkpoints):
    # Each field's final emoji widens its preceding ASCII to 4-byte storage.
    monkeypatch.setattr(parser, "MAX_RETAINED_BYTES", 500_000)
    raw = _xml([{"name": "x" * 60_000 + "😀"}] * 3)
    with pytest.raises(memory_guard.MemoryPressureError, match="retained bytes"):
        g2b.parse_response(raw)


@pytest.mark.parametrize("source", ["g2b", "lofin"])
@pytest.mark.parametrize("form", ["json", "xml"])
def test_many_fields_cannot_create_unsafe_downstream_row_dump(source, form, checkpoints):
    row = {f"field{i}": "x" * 20_000 for i in range(8)}
    row["field7"] += "😀"
    if form == "json":
        raw = _g2b_json([row]) if source == "g2b" else _lofin_json([row])
    else:
        fields = b"".join(f"<{key}>{value}</{key}>".encode() for key, value in row.items())
        if source == "g2b":
            raw = b'<response><header><resultCode>00</resultCode></header><body><items><item>' + fields + b'</item></items><totalCount>1</totalCount></body></response>'
        else:
            raw = b'<QWGJK><head><RESULT><CODE>INFO-000</CODE></RESULT></head><row>' + fields + b'</row></QWGJK>'
    module = g2b if source == "g2b" else lofin
    with pytest.raises(memory_guard.MemoryPressureError, match="row UTF-8 bytes"):
        module.parse_response(raw)


def test_row_bound_includes_json_escaping(checkpoints):
    # The decoded field is <64KiB, but encoding controls multiplies its size.
    raw = _xml([{"name": "\t" * 60_000 + "\n" * 5_535}])
    # 131070 escaped bytes plus object/key syntax exceeds the exact 128KiB cap.
    with pytest.raises(memory_guard.MemoryPressureError, match="row UTF-8 bytes"):
        g2b.parse_response(raw)


@pytest.mark.parametrize("module", [g2b, lofin])
def test_final_headroom_check_precedes_transport_attestation(module, monkeypatch, checkpoints):
    raw = _g2b_json([{"name": "ok"}]) if module is g2b else _lofin_json([{"name": "ok"}], "AIDFA")
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(raw))
    monkeypatch.setattr(module, "require_source_request_context", lambda **k: None)
    monkeypatch.setattr(module, "_quota_take", lambda *a: None)
    monkeypatch.setattr(module, "_record_connection_probe", lambda *a: None)
    real_parse = module.parse_response
    def parse_then_hold(*a, **k):
        result = real_parse(*a, **k)
        def hold(reserve_bytes=0, phase="allocation"):
            raise memory_guard.MemoryPressureError("PRE_ATTESTATION_HOLD")
        monkeypatch.setattr(dynamic, "allocation_checkpoint", hold)
        return result
    monkeypatch.setattr(module, "parse_response", parse_then_hold)
    def forbidden(*a, **k):
        raise AssertionError("hash must not allocate after a hold")
    monkeypatch.setattr(module, "record_source_transport_success", forbidden)
    with pytest.raises(memory_guard.MemoryPressureError, match="PRE_ATTESTATION_HOLD"):
        if module is g2b:
            module.request("https://example.invalid", "shopping", retries=1)
        else:
            module._request({"Key": "test"}, service_code="AIDFA", retries=1)


@pytest.mark.parametrize("field", ["resultCode", "totalCount"])
def test_nested_g2b_metadata_cannot_trigger_whole_document_str(field):
    obj = {"response": {"header": {"resultCode": "00"}, "body": {"items": [], "totalCount": 0}}}
    target = obj["response"]["header" if field == "resultCode" else "body"]
    target[field] = [{"name": "x" * 60_000}] * 20
    with pytest.raises(g2b.VNextApiError, match="PARSE"):
        g2b.parse_response(json.dumps(obj).encode())


@pytest.mark.parametrize("field", ["CODE", "MESSAGE", "list_total_count"])
def test_nested_lofin_metadata_is_rejected_before_str_conversion(field):
    result = {"CODE": "INFO-000", "MESSAGE": "OK"}
    head = {"RESULT": result, "list_total_count": 0}
    target = head if field == "list_total_count" else result
    target[field] = [{"name": "x" * 60_000}] * 20
    raw = json.dumps({"QWGJK": [{"head": [head]}, {"row": []}]}).encode()
    with pytest.raises(lofin.LofinVNextApiError, match="PARSE"):
        lofin.parse_response(raw)


@pytest.mark.parametrize("module", [g2b, lofin])
def test_pre_request_hold_consumes_no_quota_or_http(module, monkeypatch, checkpoints):
    calls = []
    monkeypatch.setattr(module, "require_source_request_context", lambda **k: calls.append("context"))
    monkeypatch.setattr(module, "_quota_take", lambda *a: calls.append("quota"))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: calls.append("http"))
    def hold(reserve_bytes=0, phase="allocation"):
        assert reserve_bytes == parser.PARSE_RESERVE_BYTES
        calls.append("checkpoint")
        raise memory_guard.MemoryPressureError("BEFORE_REQUEST_HOLD")
    monkeypatch.setattr(dynamic, "allocation_checkpoint", hold)
    with pytest.raises(memory_guard.MemoryPressureError, match="BEFORE_REQUEST_HOLD"):
        if module is g2b:
            module.request("https://example.invalid", "shopping", retries=3)
        else:
            module._request({"Key": "test"}, service_code="AIDFA", retries=3)
    assert calls == ["context", "checkpoint"]


@pytest.mark.parametrize("module", [g2b, lofin])
def test_parse_hold_consumes_quota_once_without_transport_retry(module, monkeypatch, checkpoints):
    calls = []
    raw = _g2b_json([{"name": "ok"}]) if module is g2b else _lofin_json([{"name": "ok"}])
    monkeypatch.setattr(module, "require_source_request_context", lambda **k: calls.append("context"))
    monkeypatch.setattr(dynamic, "allocation_checkpoint", lambda reserve_bytes=0, phase="allocation": calls.append("checkpoint"))
    monkeypatch.setattr(module, "_quota_take", lambda *a: calls.append("quota"))
    def http(*args, **kwargs):
        calls.append("http")
        return io.BytesIO(raw)
    monkeypatch.setattr(module.urllib.request, "urlopen", http)
    def hold(*args, **kwargs):
        raise memory_guard.MemoryPressureError("DURING_PARSE_HOLD")
    monkeypatch.setattr(module, "parse_response", hold)
    with pytest.raises(memory_guard.MemoryPressureError, match="DURING_PARSE_HOLD"):
        if module is g2b:
            module.request("https://example.invalid", "shopping", retries=3)
        else:
            module._request({"Key": "test"}, service_code="AIDFA", retries=3)
    assert calls[:4] == ["context", "checkpoint", "quota", "http"]
    assert calls.count("quota") == calls.count("http") == 1


def test_lofin_metadata_fanout_is_bounded_before_status_sets(checkpoints):
    raw = json.dumps({"QWGJK": [{"head": [{"RESULT": {"CODE": f"error{i}"}} for i in range(257)]}, {"row": []}]}).encode()
    with pytest.raises(memory_guard.MemoryPressureError, match="metadata record count"):
        lofin.parse_response(raw)


@pytest.mark.parametrize("duplicate", ["header", "body", "items"])
def test_g2b_duplicate_xml_envelope_never_merges_source_rows(duplicate):
    header = b'<header><resultCode>00</resultCode></header>'
    item_a = b'<items><item><name>A</name></item></items>'
    item_b = b'<items><item><name>B</name></item></items>'
    body = b'<body>' + item_a + (item_b if duplicate == 'items' else b'') + b'<totalCount>2</totalCount></body>'
    if duplicate == 'body':
        body += b'<body>' + item_b + b'<totalCount>999</totalCount></body>'
    raw = b'<response>' + header * (2 if duplicate == 'header' else 1) + body + b'</response>'
    with pytest.raises(g2b.VNextResponseError, match='PARSE'):
        g2b.parse_response(raw)


@pytest.mark.parametrize("duplicate", ["header", "body", "items"])
def test_g2b_duplicate_json_envelope_members_are_rejected(duplicate):
    header = '"header":{"resultCode":"00"}'
    items = '"items":[{"name":"A"}]'
    if duplicate == 'items':
        items += ',"items":[{"name":"B"}]'
    body = '"body":{' + items + ',"totalCount":2}'
    if duplicate == 'body':
        body += ',"body":{"items":[{"name":"B"}],"totalCount":999}'
    raw = ('{"response":{' + header + (',' + header if duplicate == 'header' else '') + ',' + body + '}}').encode()
    with pytest.raises(g2b.VNextResponseError, match='PARSE'):
        g2b.parse_response(raw)


@pytest.mark.parametrize("duplicate", ["head", "RESULT", "list_total_count"])
def test_lofin_duplicate_xml_metadata_is_rejected(duplicate):
    result = b'<RESULT><CODE>INFO-000</CODE></RESULT>'
    total = b'<list_total_count>1</list_total_count>'
    head = b'<head>' + result * (2 if duplicate == 'RESULT' else 1) + total * (2 if duplicate == 'list_total_count' else 1) + b'</head>'
    raw = b'<QWGJK>' + head * (2 if duplicate == 'head' else 1) + b'<row><name>A</name></row></QWGJK>'
    with pytest.raises(lofin.LofinVNextApiError, match='PARSE'):
        lofin.parse_response(raw)


@pytest.mark.parametrize("raw", [
    b'{"QWGJK":[],"QWGJK":[{"head":[{"RESULT":{"CODE":"INFO-000"}}],"row":[]}]}',
    b'{"QWGJK":[{"head":[],"head":[{"RESULT":{"CODE":"INFO-000"}}],"row":[]}]}',
    b'{"QWGJK":[{"head":[{"RESULT":{"CODE":"INFO-000"}}],"row":[{"name":"A"}],"row":[{"name":"B"}]}]}',
])
def test_lofin_duplicate_json_members_are_rejected(raw):
    with pytest.raises(lofin.LofinVNextApiError, match='PARSE'):
        lofin.parse_response(raw)


@pytest.mark.parametrize("duplicate", ["head", "row"])
def test_lofin_split_duplicate_json_containers_are_rejected(duplicate):
    head = {"head": [{"list_total_count": 1}, {"RESULT": {"CODE": "INFO-000"}}]}
    row = {"row": [{"name": "A"}]}
    entries = [head, row, head if duplicate == 'head' else {"row": [{"name": "B"}]}]
    with pytest.raises(lofin.LofinVNextApiError, match=f'duplicate {duplicate} container'):
        lofin.parse_response(json.dumps({"QWGJK": entries}).encode())


@pytest.mark.parametrize("source", ["g2b", "lofin"])
@pytest.mark.parametrize("form", ["json", "xml"])
def test_exact_16mib_body_is_accepted_without_downsizing(source, form):
    module = g2b if source == "g2b" else lofin
    rows = [{"name": "A" * 65_000} for _ in range(256)]
    rows[-1]["name"] = "A" * 64_995 + "😀"
    raw = (_g2b_json(rows) if source == "g2b" else _lofin_json(rows)) if form == "json" else _xml(rows, source)
    raw += b" " * (module.MAX_RESPONSE_BYTES - len(raw))
    assert len(raw) == 16 * 1024 * 1024
    read = module._read_response_limited(io.BytesIO(raw))
    assert isinstance(read, bytearray)  # no final full-body bytes copy
    parsed, total, *_ = module.parse_response(read)
    assert parsed == rows
    assert total == len(rows)


@pytest.mark.parametrize("module", [g2b, lofin])
@pytest.mark.parametrize("form", ["json", "xml"])
def test_cap_plus_one_rejected_before_any_partial_page(module, form):
    source = "g2b" if module is g2b else "lofin"
    raw = (_g2b_json([]) if module is g2b else _lofin_json([])) if form == "json" else _xml([], source)
    raw += b" " * (module.MAX_RESPONSE_BYTES + 1 - len(raw))
    for parse in (module._read_response_limited, module.parse_response):
        value = io.BytesIO(raw) if parse is module._read_response_limited else raw
        with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT:response bytes"):
            parse(value)


@pytest.mark.parametrize("form", ["json", "xml"])
def test_many_tiny_nodes_hit_retained_bound_before_return(form):
    fields = {f"f{i}": "x" for i in range(128)}
    if form == "json":
        raw = _g2b_json([fields] * 1000)
    else:
        row = b"<item>" + b"".join(f"<{key}>x</{key}>".encode() for key in fields) + b"</item>"
        raw = (b"<response><header><resultCode>00</resultCode></header><body><items>" +
               row * 1000 + b"</items><totalCount>1000</totalCount></body></response>")
    assert len(raw) < 3 * 1024 * 1024
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT:retained bytes"):
        g2b.parse_response(raw)


@pytest.mark.parametrize("form", ["json", "xml"])
def test_node_counter_is_independently_fail_closed(form, monkeypatch):
    monkeypatch.setattr(parser, "MAX_NODES", 40)
    raw = _g2b_json([{"name": "x"}] * 20) if form == "json" else _xml([{"name": "x"}] * 20)
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_RESPONSE_LIMIT:node count"):
        g2b.parse_response(raw)


@pytest.mark.parametrize("form", ["json", "xml"])
def test_utf8_split_inside_multibyte_character_is_preserved(form, monkeypatch):
    monkeypatch.setattr(parser, "CHUNK_BYTES", 7)
    rows = [{"name": "가😀나다😀" * 6}]
    raw = _g2b_json(rows) if form == "json" else _xml(rows)
    parsed, total = g2b.parse_response(g2b._read_response_limited(io.BytesIO(raw)))
    assert parsed == rows and total == 1


@pytest.mark.parametrize("module", [g2b, lofin])
@pytest.mark.parametrize("failure", ["depth", "duplicate", "bytes"])
def test_rejected_page_charges_one_real_quota_and_never_attests(module, failure, monkeypatch):
    import db
    raw = (_g2b_json([{"name": "x"}]) if module is g2b else
           _lofin_json([{"name": "x"}], "AIDFA"))
    if failure == "depth":
        raw = b'{"x":' + b'[' * 17 + b']' * 17 + b'}'
    elif failure == "duplicate":
        raw = raw[:-1] + b',"duplicate":1,"duplicate":2}'
    else:
        raw += b" " * (module.MAX_RESPONSE_BYTES + 1 - len(raw))
    events = []
    monkeypatch.setattr(module, "require_source_request_context", lambda **kwargs: None)
    monkeypatch.setattr(module, "_quota_today", lambda: "2026-10-17")
    monkeypatch.setattr(module, "_record_connection_probe", lambda *args: None)
    monkeypatch.setattr(module, "record_source_transport_success", lambda *args: events.append("attest"))
    monkeypatch.setattr(module.time, "sleep", lambda *args: events.append("retry"))
    def http(*args, **kwargs):
        events.append("http")
        return io.BytesIO(raw)
    monkeypatch.setattr(module.urllib.request, "urlopen", http)
    expected = (memory_guard.MemoryPressureError, module.VNextResponseError) if module is g2b else (memory_guard.MemoryPressureError, module.LofinVNextApiError)
    with pytest.raises(expected):
        if module is g2b:
            module.request("https://example.invalid", "shopping", retries=3)
        else:
            module._request({"Key": "test"}, service_code="AIDFA", retries=3)
    quota_key = "vnext_api_calls_total" if module is g2b else "lofin_vnext_calls_count"
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM app_settings WHERE key=?", (quota_key,)).fetchone()["value"] == "1"
    assert events == ["http"]


@pytest.mark.parametrize("module", [g2b, lofin])
def test_flag_off_uses_unchanged_legacy_json_xml_and_read_behavior(module, monkeypatch):
    monkeypatch.delenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("experimental parser must stay inactive")
    monkeypatch.setattr(parser, "parse_json", forbidden)
    monkeypatch.setattr(parser, "parse_xml", forbidden)
    monkeypatch.setattr(parser, "read_response_limited", forbidden)
    monkeypatch.setattr(dynamic, "allocation_checkpoint", forbidden)
    source = "g2b" if module is g2b else "lofin"
    # Legacy permits pages beyond new object limits, and keeps last duplicate key.
    rows = [{"name": "x"}] * 1001
    raw = _g2b_json(rows) if module is g2b else _lofin_json(rows)
    assert module.parse_response(raw)[0] == rows
    assert module.parse_response(_xml([{"name": "legacy"}], source))[0] == [{"name": "legacy"}]
    duplicate = raw[:-1] + b',"extra":1,"extra":2}'
    assert module.parse_response(duplicate)[0] == rows
    class FixedLegacyResponse:
        def read(self, *args):
            return raw
    assert module._read_response_limited(FixedLegacyResponse()) == raw


def test_parser_checkpoint_enforces_real_decimal_raw_reserve(monkeypatch):
    monkeypatch.setattr(dynamic, "allocation_checkpoint", REAL_ALLOCATION_CHECKPOINT)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112)
    reserve = parser.PARSE_RESERVE_BYTES
    raw = dynamic.SOURCE_CEILING_BYTES - reserve - dynamic.ALLOCATION_DRIFT_BYTES
    state = {"source": "cgroup-v2", "limit_bytes": 256 * dynamic.MIB,
             "current_bytes": raw - 1, "oom_group": 0}
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: dict(state))
    parser.allocation_checkpoint(reserve)
    state["current_bytes"] = raw
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_ALLOCATION_HOLD"):
        parser.allocation_checkpoint(reserve)
    assert dynamic.START_RESERVE_BYTES == 52 * dynamic.MIB


@pytest.mark.parametrize("form", ["json", "xml"])
def test_raw_headroom_is_resampled_before_next_native_parser_feed(form, monkeypatch):
    monkeypatch.setattr(dynamic, "allocation_checkpoint", REAL_ALLOCATION_CHECKPOINT)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112)
    samples = []
    def sample():
        samples.append(len(samples) + 1)
        return {"source": "cgroup-v2", "limit_bytes": 256 * dynamic.MIB,
                "current_bytes": 200_000_000 if len(samples) < 3 else 219_000_000,
                "oom_group": 0}
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", sample)
    raw = _g2b_json([{"name": "A" * 60_000}]) if form == "json" else _xml([{"name": "A" * 60_000}])
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_ALLOCATION_HOLD"):
        g2b.parse_response(raw)
    assert len(samples) == 3


def test_raw_headroom_is_resampled_after_blocking_read_before_body_growth(monkeypatch):
    monkeypatch.setattr(dynamic, "allocation_checkpoint", REAL_ALLOCATION_CHECKPOINT)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112)
    state = {"source": "cgroup-v2", "limit_bytes": 256 * dynamic.MIB,
             "current_bytes": 200_000_000, "oom_group": 0}
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: dict(state))
    reads = []
    class Response:
        def read(self, size):
            reads.append(size)
            state["current_bytes"] = 219_000_000
            return b"x" * size
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_ALLOCATION_HOLD"):
        g2b._read_response_limited(Response())
    assert reads == [parser.CHUNK_BYTES]


@pytest.mark.parametrize("module", [g2b, lofin])
def test_verified_512mib_source_keeps_legacy_parser(module, monkeypatch):
    monkeypatch.setattr(memory_guard, "source_dynamic_budget_enabled", REAL_DYNAMIC_ENABLED)
    monkeypatch.setattr(g2b, "_record_result", lambda *args: None)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: {
        "source": "cgroup-v2", "limit_bytes": 512 * dynamic.MIB,
        "current_bytes": 200_000_000, "oom_group": 0,
    })
    def forbidden(*args, **kwargs):
        raise AssertionError("verified larger tier must use legacy parser")
    monkeypatch.setattr(parser, "parse_json", forbidden)
    monkeypatch.setattr(parser, "parse_xml", forbidden)
    rows = [{"name": "larger-tier legacy"}] * 1001
    raw = _g2b_json(rows) if module is g2b else _lofin_json(rows)
    monkeypatch.setattr(parser, "read_response_limited", forbidden)
    class LegacyResponse:
        def read(self, *args):
            return raw
    assert module._bounded_parser() is None
    assert module.parse_response(module._read_response_limited(LegacyResponse()))[0] == rows


@pytest.mark.parametrize("module", [g2b, lofin])
@pytest.mark.parametrize("sample", [{}, {"limit_bytes": 512 * dynamic.MIB}])
def test_missing_cgroup_proof_selects_bounded_parser_and_holds(module, sample, monkeypatch):
    monkeypatch.setattr(memory_guard, "source_dynamic_budget_enabled", REAL_DYNAMIC_ENABLED)
    monkeypatch.setattr(dynamic, "allocation_checkpoint", REAL_ALLOCATION_CHECKPOINT)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: dict(sample))
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112)
    assert module._bounded_parser() is parser
    raw = _g2b_json([]) if module is g2b else _lofin_json([])
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_PROBE_UNAVAILABLE"):
        module.parse_response(raw)
