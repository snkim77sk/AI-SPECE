import gzip
import json

import g2b_result_sync_worker
import result_snapshot_vnext


def test_result_sync_worker_imports_snapshot_outside_web_process(monkeypatch, tmp_path):
    serving = tmp_path / "serving.sqlite3"
    source = tmp_path / "snapshot.json.gz"
    result = tmp_path / "manifest.json"

    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))
    payload = {
        "schema_version": 1,
        "snapshot_id": "WORKER-SAFE",
        "generated_at_utc": "2026-10-07T00:00:00+00:00",
        "source_version": "4.1.171",
        "sections": {
            "shopping": [{
                "source_key": "REQ-1",
                "source_date": "2026-10-06",
                "demand_org": "테스트기관",
                "primary_category": "LIGHTING",
                "item_name": "LED 조명",
                "amount": 1000,
            }],
            "vendors": [],
            "budget_targets": [],
            "budget_prebid": [],
        },
        "collection_status": {},
        "readiness": {},
        "source_counts": {},
    }
    source.write_bytes(
        gzip.compress(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
    )

    manifest = g2b_result_sync_worker._process_snapshot_file(
        source,
        "gzip",
        result,
    )

    assert manifest["snapshot_id"] == "WORKER-SAFE"
    assert result_snapshot_vnext.active_snapshot_id() == "WORKER-SAFE"
    stored = json.loads(result.read_text(encoding="utf-8"))
    assert stored["total_rows"] == 1


def test_result_sync_worker_rejects_oversized_expanded_payload(tmp_path):
    source = tmp_path / "large.json.gz"
    result = tmp_path / "manifest.json"
    source.write_bytes(
        gzip.compress(
            b"x" * (g2b_result_sync_worker.MAX_JSON_BYTES + 1)
        )
    )

    try:
        g2b_result_sync_worker._decode_snapshot_file(source, "gzip")
    except ValueError as exc:
        assert str(exc) == "SNAPSHOT_JSON_TOO_LARGE"
    else:
        raise AssertionError("oversized expanded snapshot must fail closed")
