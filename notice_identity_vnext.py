"""Compatibility helpers for G2B notice/order identities.

Official endpoints can render the same numeric notice order with different zero
padding (for example 00 vs 000). RAW source keys remain untouched; these helpers
are used only when relating already-preserved records.
"""
from __future__ import annotations


def canonical_notice_order(value):
    text = str(value or "").strip()
    if text.isdigit():
        return str(int(text))
    return text.casefold()


def split_notice_key(value):
    text = str(value or "").strip()
    if "|" not in text:
        return text, ""
    return text.split("|", 1)


def notice_parts_equivalent(left_no, left_order, right_no, right_order):
    return (
        str(left_no or "").strip() == str(right_no or "").strip()
        and canonical_notice_order(left_order) == canonical_notice_order(right_order)
    )


def notice_keys_equivalent(left, right):
    left_no, left_order = split_notice_key(left)
    right_no, right_order = split_notice_key(right)
    return notice_parts_equivalent(left_no, left_order, right_no, right_order)
