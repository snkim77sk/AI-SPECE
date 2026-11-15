"""Strict shared source-shape helpers. No I/O or persisted parser state."""
import re
import xml.etree.ElementTree as ET


def parse_count(value):
    """None is unknown, not len(page); invalid numeric text is an error."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean count")
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("invalid nonnegative count")
    return int(text)


def xml_root(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations not allowed")
    root = ET.fromstring(text)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root
