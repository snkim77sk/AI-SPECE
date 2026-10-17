"""Byte-fed, fail-closed source response parsers.

The byte ceiling alone does not bound JSON/XML object expansion.  These parsers
bound token size before handing bytes to native parsers, then bound depth,
containers, rows and retained Python objects.  They never return a partial page.
The dynamic source policy checks raw cgroup headroom before every read/feed;
legacy runtimes keep their existing admission policy.
"""
from __future__ import annotations

from decimal import Decimal
import codecs
import json
import sys
import xml.etree.ElementTree as ET
from xml.parsers import expat

import ijson

CHUNK_BYTES = 16 * 1024
MAX_ROWS = 1000
MAX_FIELDS = 256
MAX_FIELD_BYTES = 64 * 1024
MAX_ROW_UTF8_BYTES = 128 * 1024
MAX_DEPTH = 16
MAX_RETAINED_BYTES = 24 * 1024 * 1024
MAX_NODES = 200_000
# Includes one feed's expansion, an incomplete token, parser and Python overhead.
PARSE_RESERVE_BYTES = 2 * 1024 * 1024


class SourceResponseLimitError(ValueError):
    """Reject the entire source page, never silently truncate it."""


def allocation_checkpoint(reserve_bytes=0):
    import g2b_dynamic_source_budget as dynamic
    dynamic.allocation_checkpoint(reserve_bytes=reserve_bytes)


def raise_limit_hold(error):
    import memory_guard
    raise memory_guard.MemoryPressureError("SOURCE_RESPONSE_LIMIT:" + str(error)) from None


def check_row_count(count):
    if count > MAX_ROWS:
        raise SourceResponseLimitError("row count")


def scalar(value):
    """Reject nested metadata before str()/numeric conversion can copy a page."""
    if isinstance(value, (dict, list)):
        raise ValueError("non-scalar source metadata")
    return value


def validate_rows(rows):
    """Bound downstream per-row canonical dumps without constructing that dump.

    Field/token bounds cap each iterencode chunk; this bound caps the entire
    serialized row, including escaping and nested values. A late emoji can then
    widen at most a 128 KiB row, rather than a multi-megabyte retained page.
    """
    check_row_count(len(rows))
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
    for row in rows:
        allocation_checkpoint(PARSE_RESERVE_BYTES)
        size = 0
        for chunk in encoder.iterencode(row):
            size += len(chunk.encode("utf-8"))
            if size > MAX_ROW_UTF8_BYTES:
                raise SourceResponseLimitError("row UTF-8 bytes")
    return rows


def read_response_limited(response, max_bytes):
    """Read a bounded binary body without copying it on completion.

    This helper is only used by the opted-in path. A short read is not EOF, and
    transports must honor the requested size; no unbounded fallback is allowed.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("INVALID_SOURCE_RESPONSE_LIMIT")
    raw = bytearray()
    while True:
        size = min(CHUNK_BYTES, max_bytes + 1 - len(raw))
        # bytearray growth can briefly retain both the old and new allocation.
        allocation_checkpoint((len(raw) + size) * 9 // 8 + size + 4096)
        try:
            chunk = response.read(size)
        except TypeError:
            raise SourceResponseLimitError("unbounded transport read") from None
        if not isinstance(chunk, (bytes, bytearray)):
            raise SourceResponseLimitError("non-binary source response")
        if len(raw) + len(chunk) > max_bytes:
            raise SourceResponseLimitError("response bytes")
        if len(chunk) > size:
            raise SourceResponseLimitError("transport read size")
        if not chunk:
            return raw
        # Network reads can block while the web parent grows. Sample again
        # before extending/reallocating the retained body after the read returns.
        allocation_checkpoint((len(raw) + len(chunk)) * 9 // 8 + 4096)
        raw.extend(chunk)


def _chunks(raw, max_bytes):
    """Slice only small byte buffers; never decode a whole response to Unicode."""
    total = 0
    if isinstance(raw, str):
        # Legacy direct parser callers may pass text; encode in bounded pieces.
        if len(raw) > max_bytes:
            raise SourceResponseLimitError("response bytes")
        chunks = (raw[i:i + CHUNK_BYTES // 4].encode("utf-8")
                  for i in range(0, len(raw), CHUNK_BYTES // 4))
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        if len(raw) > max_bytes:
            raise SourceResponseLimitError("response bytes")
        view = memoryview(raw)
        chunks = (bytes(view[i:i + CHUNK_BYTES]) for i in range(0, len(view), CHUNK_BYTES))
    else:
        chunks = iter(()) if raw is None else _chunks(str(raw), max_bytes)
    chunks = iter(chunks)
    while True:
        allocation_checkpoint(PARSE_RESERVE_BYTES)
        try:
            chunk = next(chunks)
        except StopIteration:
            return
        total += len(chunk)
        if total > max_bytes:
            raise SourceResponseLimitError("response bytes")
        yield chunk


def response_format(raw, max_bytes):
    """Inspect a bounded prefix, accepting UTF-8 BOM and JSON/XML whitespace."""
    # Avoid slicing/stripping a whole Unicode or bytes document.
    if isinstance(raw, str):
        if len(raw) > max_bytes:
            raise SourceResponseLimitError("response bytes")
        for ch in raw:
            if ch not in " \t\r\n\ufeff":
                return "json" if ch in "[{" else "xml"
        return "empty"
    if raw is None:
        return "empty"
    if len(raw) > max_bytes:
        raise SourceResponseLimitError("response bytes")
    start = 3 if raw[:3] == b"\xef\xbb\xbf" else 0
    for pos in range(start, len(raw)):
        if raw[pos] not in (32, 9, 13, 10):
            return "json" if raw[pos] in (123, 91) else "xml"
    return "empty"


class _Budget:
    def __init__(self):
        self.retained = 0
        self.nodes = 0

    def add(self, size=0):
        self.nodes += 1
        # Conservative allowance for dict/list slots, keys and allocator overhead.
        self.retained += size + 128
        if self.nodes > MAX_NODES:
            raise SourceResponseLimitError("node count")
        if self.retained > MAX_RETAINED_BYTES:
            raise SourceResponseLimitError("retained bytes")


class _JsonTokenGuard:
    """Only allocation guards; ijson remains the JSON syntax/Unicode parser."""
    def __init__(self):
        self.string = False
        self.escape = False
        self.length = 0
        self.atom = 0
        self.depth = 0

    def feed(self, chunk):
        for ch in chunk:
            if self.string:
                if self.escape:
                    self.escape = False
                elif ch == 92:
                    self.escape = True
                elif ch == 34:
                    self.string = False
                    self.length = 0
                    continue
                self.length += 1
                if self.length > MAX_FIELD_BYTES:
                    raise SourceResponseLimitError("field bytes")
            elif ch == 34:
                self.string, self.length, self.atom = True, 0, 0
            elif ch in (91, 123):
                self.depth += 1
                self.atom = 0
                if self.depth > MAX_DEPTH:
                    raise SourceResponseLimitError("depth")
            elif ch in (93, 125):
                self.depth -= 1
                self.atom = 0
            elif ch in (32, 9, 10, 13, 44, 58):
                self.atom = 0
            else:
                self.atom += 1
                if self.atom > 256:
                    raise SourceResponseLimitError("numeric/literal token bytes")


def parse_json(raw, max_bytes):
    allocation_checkpoint(PARSE_RESERVE_BYTES)
    budget, guard, stack = _Budget(), _JsonTokenGuard(), []
    result = []

    def attach(value):
        if not stack:
            result.append(value)
        else:
            frame = stack[-1]
            node = frame[0]
            if isinstance(node, dict):
                node[frame[1]] = value
                frame[1] = None
            else:
                if len(node) >= MAX_ROWS:
                    raise SourceResponseLimitError("array/row count")
                node.append(value)

    @ijson.coroutine
    def sink():
        while True:
            event, value = yield
            if event == "map_key":
                frame = stack[-1]
                # Duplicate members can replace a header/body/items container or
                # lose a source field silently. Never use last-value-wins here.
                if value in frame[0]:
                    raise ValueError("duplicate JSON object member")
                frame[2] += 1
                if frame[2] > MAX_FIELDS:
                    raise SourceResponseLimitError("field count")
                budget.add(sys.getsizeof(value))
                frame[1] = value
            elif event in ("start_map", "start_array"):
                budget.add(256)
                node = {} if event == "start_map" else []
                attach(node)
                stack.append([node, None, 0])
            elif event in ("end_map", "end_array"):
                stack.pop()
            else:
                if isinstance(value, Decimal):
                    # json.loads uses float for JSON fractions/exponents. Preserve
                    # that representation, including negative zero, for attestation.
                    value = float(value)
                budget.add(sys.getsizeof(value))
                attach(value)

    target = sink()
    parser = ijson.basic_parse_coro(target, use_float=False)
    first = True
    try:
        for chunk in _chunks(raw, max_bytes):
            if first:
                chunk = chunk.removeprefix(b"\xef\xbb\xbf")
                first = False
            guard.feed(chunk)  # before the parser can allocate a giant string
            parser.send(chunk)
        parser.close()  # validate complete document and trailing garbage
    finally:
        target.close()
    if len(result) != 1:
        raise ValueError("missing JSON value")
    return result[0]


class _XmlTokenGuard:
    """Bound incomplete markup before Expat can retain a growing token.

    This is an allocation scanner, not an XML parser. Expat still validates all
    syntax. Comment/CDATA/PI terminators and quoted attributes are tracked so a
    '>' inside them cannot hide a multi-megabyte incomplete token.
    """
    def __init__(self):
        self.markup = False
        self.length = 0
        self.prefix = bytearray()
        self.tail = bytearray()
        self.quote = 0

    def feed(self, chunk):
        for ch in chunk:
            if not self.markup:
                if ch != 60:
                    continue
                self.markup, self.length = True, 0
                self.prefix.clear()
                self.tail.clear()
                self.quote = 0
            self.length += 1
            if self.length > MAX_FIELD_BYTES:
                raise SourceResponseLimitError("XML markup bytes")
            if len(self.prefix) < 9:
                self.prefix.append(ch)
            self.tail.append(ch)
            if len(self.tail) > 3:
                del self.tail[0]
            if self.prefix.startswith(b"<!--"):
                done = self.length >= 7 and self.tail == b"-->"
            elif self.prefix.startswith(b"<![CDATA["):
                done = self.length >= 12 and self.tail == b"]]>"
            elif self.prefix.startswith(b"<?"):
                done = self.length >= 4 and self.tail[-2:] == b"?>"
            else:
                if self.quote:
                    if ch == self.quote:
                        self.quote = 0
                    done = False
                else:
                    done = ch == 62
                    if ch in (34, 39):
                        self.quote = ch
            if done:
                self.markup = False


def parse_xml(raw, max_bytes, *, row_path):
    """Keep a small metadata tree and completed row dicts, never a page XML tree.

    ET Elements are built one row at a time from Expat callbacks; completed rows
    are converted and detached before the next feed. Their fields retain the
    original parser's child.text semantics, including namespace stripping.
    """
    allocation_checkpoint(PARSE_RESERVE_BYTES)
    budget, guard, stack, rows = _Budget(), _XmlTokenGuard(), [], []
    root = None
    row_path = tuple(row_path)
    # The previous transport accepted UTF-8 only. Enforce that incrementally so
    # UTF-16 bytes cannot bypass the ASCII markup safety scanner via Expat's
    # encoding autodetection. Each validation string is at most one feed long.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    parser = expat.ParserCreate(encoding="utf-8", namespace_separator="}")
    parser.buffer_text = False

    def forbidden(*args):
        raise ValueError("XML declarations not allowed")

    def start(name, attrs):
        nonlocal root
        if len(stack) >= MAX_DEPTH:
            raise SourceResponseLimitError("depth")
        tag = name.rsplit("}", 1)[-1]
        if len(tag) > MAX_FIELD_BYTES or len(attrs) > MAX_FIELDS:
            raise SourceResponseLimitError("XML fields")
        budget.add(256 + len(tag) * 4)
        node = ET.Element(tag)
        if stack:
            parent = stack[-1]
            if tuple(f[0].tag for f in stack) + (tag,) == row_path:
                check_row_count(len(rows) + 1)
            else:
                # Only the designated source row is repeatable. In particular,
                # two bodies/items/heads must not merge rows while metadata is
                # read from only the first container by the envelope validator.
                if tag in parent[5]:
                    raise ValueError("duplicate XML element")
                parent[5].add(tag)
                parent[3] += 1
                if parent[3] > MAX_FIELDS:
                    raise SourceResponseLimitError("field count")
            parent[0].append(node)
            parent[4] = True
        else:
            root = node
        stack.append([node, [], 0, 0, False, set()])

    def data(text):
        if not stack:
            return
        frame = stack[-1]
        # ElementTree child.text excludes text after a nested child (its tail).
        if frame[4]:
            return
        frame[2] += len(text.encode("utf-8"))
        if frame[2] > MAX_FIELD_BYTES:
            raise SourceResponseLimitError("field bytes")
        budget.add(sys.getsizeof(text))
        frame[1].append(text)

    def end(name):
        frame = stack[-1]
        node = frame[0]
        node.text = "".join(frame[1])
        # A late non-BMP character can widen the complete field to four-byte
        # storage even when previous callbacks were ASCII. Account that retained
        # growth, while the fixed feed reserve covers the bounded join temporary.
        budget.retained += max(0, sys.getsizeof(node.text) -
                               sum(sys.getsizeof(part) for part in frame[1]))
        if budget.retained > MAX_RETAINED_BYTES:
            raise SourceResponseLimitError("retained bytes")
        if tuple(f[0].tag for f in stack) == row_path:
            if len(rows) >= MAX_ROWS:
                raise SourceResponseLimitError("row count")
            # A row's XML tree is removed as soon as its flat raw dict is ready.
            row = {child.tag: child.text or "" for child in node}
            rows.append(row)
            stack[-2][0].remove(node)
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.ProcessingInstructionHandler = lambda *a: None
    first = True
    leading = True
    for chunk in _chunks(raw, max_bytes):
        decoder.decode(chunk, final=False)
        if first:
            chunk = chunk.removeprefix(b"\xef\xbb\xbf")
            first = False
        if leading:
            chunk = chunk.lstrip(b" \t\r\n")
            leading = not bool(chunk)
        guard.feed(chunk)
        parser.Parse(chunk, False)
    decoder.decode(b"", final=True)
    parser.Parse(b"", True)
    if root is None:
        raise ValueError("missing XML root")
    return root, rows


PARSE_ERRORS = (ValueError, UnicodeError, expat.ExpatError, ijson.JSONError)
