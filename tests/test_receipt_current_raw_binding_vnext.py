import db
import vnext_stability
from vnext_collection import collect_pages, verified_checkpoint
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def _collect(dataset="receipt_binding", scope="scope"):
    pages = {1: [{"id": "A", "value": 1}], 2: []}
    result = collect_pages(
        dataset=dataset,
        scope=scope,
        range_start="2026-09-16",
        range_end="2026-09-16",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-16",
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )
    assert result["complete"] is True
    cp = get_checkpoint(dataset, scope)
    assert verified_checkpoint(cp) is True
    return pages


def test_receipt_page_hash_is_recomputed_from_item_receipts():
    _collect("receipt_hash", "scope")
    sha_b = preserve_raw(
        "receipt_hash", "B", {"id": "B", "value": 1},
        source_system="TEST", source_operation="TEST_LIST", source_date="2026-09-16",
    )
    cp = get_checkpoint("receipt_hash", "scope")
    generation = __import__("json").loads(cp["cursor_value"])["generation"]

    # Keep counts and revision existence valid while swapping the item identity.
    # Without recomputing the page hash this could masquerade as the original page.
    with db.connect() as conn:
        conn.execute(
            """UPDATE vnext_collection_items
               SET source_key=?,payload_sha256=?
               WHERE dataset=? AND scope_key=? AND generation=? AND source_key='A'""",
            ("B", sha_b, "receipt_hash", "scope", generation),
        )

    assert verified_checkpoint(get_checkpoint("receipt_hash", "scope")) is False


def test_receipt_complete_is_invalid_if_current_raw_drifted_after_collection():
    _collect("raw_drift", "scope")
    # Preserve a legitimate later revision under the same source identity. The old
    # immutable revision still exists, but the completed receipt no longer describes
    # the current RAW payload that normalization would consume.
    preserve_raw(
        "raw_drift", "A", {"id": "A", "value": 2},
        source_system="TEST", source_operation="TEST_LIST", source_date="2026-09-16",
    )
    assert verified_checkpoint(get_checkpoint("raw_drift", "scope")) is False


def test_existing_stability_proof_is_invalidated_when_current_raw_changes():
    pages = _collect("proof_raw_drift", "scope")
    checked = vnext_stability.verify_checkpoint_source(
        dataset="proof_raw_drift",
        scope="scope",
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
    )
    assert checked["stable"] is True
    assert vnext_stability.stability_verified_checkpoint(
        get_checkpoint("proof_raw_drift", "scope")
    ) is True

    preserve_raw(
        "proof_raw_drift", "A", {"id": "A", "value": 2},
        source_system="TEST", source_operation="TEST_LIST", source_date="2026-09-16",
    )
    cp = get_checkpoint("proof_raw_drift", "scope")
    assert verified_checkpoint(cp) is False
    assert vnext_stability.stability_verified_checkpoint(cp) is False
    assert vnext_stability.stability_fresh_checkpoint(cp) is False
