import hashlib
import hmac
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from seoa_bridge import (
    BRIDGE_PATH,
    _procurement_context,
    _readiness_projection,
    _safe_health_projection,
    readonly_connection,
    register_seoa_bridge_routes,
    verify_signed_request,
)


SECRET = "SPACE-bridge-secret-0123456789-abcdef"


def _signed(payload, *, timestamp=1000, nonce="a" * 32):
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    canonical = (
        "SEOA-BRIDGE-V1\nPOST\n"
        + BRIDGE_PATH
        + "\n"
        + str(timestamp)
        + "\n"
        + nonce
        + "\n"
        + hashlib.sha256(body).hexdigest()
    ).encode()
    signature = hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    return body, {
        "content-type": "application/json",
        "x-seoa-bridge-version": "1",
        "x-seoa-bridge-timestamp": str(timestamp),
        "x-seoa-bridge-nonce": nonce,
        "x-seoa-bridge-signature": signature,
    }


def _foundation_db(tmp_path):
    path = tmp_path / "space-bridge.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '');
        CREATE TABLE raw_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_date TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            payload_sha256 TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE classifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            classifier_version TEXT NOT NULL,
            source_payload_sha256 TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE collection_checkpoints(
            dataset TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'IDLE'
        );
        """
    )
    conn.execute(
        "INSERT INTO app_settings(key,value) VALUES('api_key','configured-placeholder')"
    )
    conn.execute(
        "INSERT INTO raw_records(dataset,source_key,source_date,payload_sha256) "
        "VALUES('bid_notice_goods','A','2026-09-20','abc')"
    )
    conn.execute(
        "INSERT INTO classifications(entity_type,entity_key,primary_category,"
        "classifier_version,source_payload_sha256) VALUES(?,?,?,?,?)",
        ("bid_notice_goods", "A", "LIGHTING", "1.1.0-rule-v1", "abc"),
    )
    conn.execute(
        "INSERT INTO collection_checkpoints(dataset,scope_key,status) "
        "VALUES('bid_notice_goods','scope','COMPLETE')"
    )
    conn.commit()
    conn.close()
    return path


def test_space_bridge_auth_rejects_replay_tamper_and_stale():
    import seoa_bridge as bridge

    bridge._recent_nonces.clear()
    body, headers = _signed({"operation": "health.read", "parameters": {}})
    assert verify_signed_request(
        SECRET, method="POST", path=BRIDGE_PATH, body=body, headers=headers, now=1030
    )
    assert not verify_signed_request(
        SECRET, method="POST", path=BRIDGE_PATH, body=body, headers=headers, now=1031
    )

    body2, headers2 = _signed(
        {"operation": "health.read", "parameters": {}},
        nonce="b" * 32,
    )
    assert not verify_signed_request(
        SECRET,
        method="POST",
        path=BRIDGE_PATH,
        body=b'{"operation":"health.read","parameters":{"x":1}}',
        headers=headers2,
        now=1030,
    )

    body3, headers3 = _signed(
        {"operation": "health.read", "parameters": {}},
        nonce="c" * 32,
    )
    assert not verify_signed_request(
        SECRET, method="POST", path=BRIDGE_PATH, body=body3, headers=headers3, now=2000
    )


def test_readonly_connection_cannot_write(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with readonly_connection() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES('must_not_write','1')"
            )


def test_readiness_returns_boolean_credentials_and_aggregate_state(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.delenv("G2B_SERVICE_KEY", raising=False)
    monkeypatch.delenv("LOFIN_API_KEY", raising=False)

    with readonly_connection() as conn:
        result = _readiness_projection(conn)

    assert result["status"] == "READ_ONLY_READY"
    assert result["credentials"]["g2b_service_key_configured"] is True
    assert result["credentials"]["lofin_api_key_configured"] is False
    assert result["raw_counts"]["bid_notice_goods"] == 1
    assert result["checkpoint_status_counts"]["bid_notice_goods"] == {"COMPLETE": 1}
    rendered = str(result)
    assert "configured-placeholder" not in rendered
    assert result["external_api_calls"] == 0
    assert result["writes_performed"] == 0


def test_procurement_context_returns_counts_not_raw_or_vendor_data(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with readonly_connection() as conn:
        result = _procurement_context(
            conn,
            {"kind": "goods", "days": 3660, "limit": 20},
        )
    row = result["datasets"]["bid_notice_goods"]
    assert row["raw_count"] == 1
    assert row["current_classified_count"] == 1
    assert row["category_counts"] == {"LIGHTING": 1}
    assert result["raw_payloads_returned"] is False
    assert result["vendor_identity_returned"] is False


def test_health_projection_drops_db_path_backend_error_and_message():
    projected = _safe_health_projection({
        "status": "ok",
        "configured": True,
        "backend_ok": True,
        "backend_error": "private failure",
        "db_path": "/app/user_data/private.sqlite3",
        "version": "2.3.2",
        "test_mode": False,
        "sample_generation": False,
        "message": "private message",
        "sync": {"status": "완료", "job": "입찰공고", "message": "private sync"},
    })
    rendered = str(projected)
    assert projected["backend_ok"] is True
    assert "/app/user_data" not in rendered
    assert "private failure" not in rendered
    assert "private message" not in rendered


def test_bridge_route_happy_path_uses_readonly_db(monkeypatch, tmp_path):
    import seoa_bridge as bridge

    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("AI_SPACE_SEOA_BRIDGE_SECRET", SECRET)
    monkeypatch.setattr(bridge.time, "time", lambda: 1000)
    bridge._recent_nonces.clear()

    app = FastAPI()
    register_seoa_bridge_routes(
        app,
        health_reader=lambda: {
            "status": "ok",
            "configured": True,
            "backend_ok": True,
            "version": "2.3.2",
            "test_mode": False,
            "sample_generation": False,
            "sync": {"status": "대기", "job": ""},
        },
    )
    with TestClient(app) as client:
        body, headers = _signed(
            {"operation": "readiness.read", "parameters": {}},
            nonce="d" * 32,
        )
        response = client.post(BRIDGE_PATH, content=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["writes_performed"] == 0

        body, headers = _signed(
            {
                "operation": "procurement_context.read",
                "parameters": {"kind": "goods", "days": 3660, "limit": 10},
            },
            nonce="e" * 32,
        )
        response = client.post(BRIDGE_PATH, content=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["external_api_calls"] == 0


def test_bridge_disabled_without_secret(monkeypatch):
    monkeypatch.delenv("AI_SPACE_SEOA_BRIDGE_SECRET", raising=False)
    app = FastAPI()
    register_seoa_bridge_routes(
        app,
        health_reader=lambda: {"status": "ok"},
    )
    with TestClient(app) as client:
        response = client.post(
            BRIDGE_PATH,
            content=b'{"operation":"health.read","parameters":{}}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 503



def test_readiness_does_not_claim_canary_success_from_key_presence(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with readonly_connection() as conn:
        result = _readiness_projection(conn)
    assert result["status"] == "READ_ONLY_READY"
    assert "CANARY_READY" not in result["status"]
