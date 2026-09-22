import asyncio
import hashlib
import hmac
import json
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

import db
from seoa_bridge import (
    BRIDGE_PATH,
    _procurement_context,
    _procurement_trend,
    _readiness_projection,
    _safe_health_projection,
    readonly_connection,
    register_seoa_bridge_routes,
    verify_signed_request,
)


SECRET = "SPACE-bridge-secret-0123456789-abcdef"


def _invoke_bridge(app, body: bytes, headers: dict):
    route = next(
        item
        for item in app.routes
        if getattr(item, "path", None) == BRIDGE_PATH
        and "POST" in getattr(item, "methods", set())
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": BRIDGE_PATH,
        "raw_path": BRIDGE_PATH.encode(),
        "query_string": b"",
        "headers": [
            (str(key).lower().encode(), str(value).encode())
            for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, receive)
    return asyncio.run(route.endpoint(request))


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
    body, headers = _signed(
        {"operation": "readiness.read", "parameters": {}},
        nonce="d" * 32,
    )
    response = _invoke_bridge(app, body, headers)
    assert response["data"]["writes_performed"] == 0

    body, headers = _signed(
        {
            "operation": "procurement_context.read",
            "parameters": {"kind": "goods", "days": 3660, "limit": 10},
        },
        nonce="e" * 32,
    )
    response = _invoke_bridge(app, body, headers)
    assert response["data"]["external_api_calls"] == 0


def test_bridge_disabled_without_secret(monkeypatch):
    monkeypatch.delenv("AI_SPACE_SEOA_BRIDGE_SECRET", raising=False)
    app = FastAPI()
    register_seoa_bridge_routes(
        app,
        health_reader=lambda: {"status": "ok"},
    )
    with pytest.raises(HTTPException) as exc:
        _invoke_bridge(
            app,
            b'{"operation":"health.read","parameters":{}}',
            {"content-type": "application/json"},
        )
    assert exc.value.status_code == 503



def test_readiness_does_not_claim_canary_success_from_key_presence(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with readonly_connection() as conn:
        result = _readiness_projection(conn)
    assert result["status"] == "READ_ONLY_READY"
    assert "CANARY_READY" not in result["status"]



def test_seoa_bridge_hmac_interop_vector():
    import seoa_bridge as bridge

    bridge._recent_nonces.clear()
    body = b'{"operation":"health.read","parameters":{}}'
    headers = {
        "x-seoa-bridge-version": "1",
        "x-seoa-bridge-timestamp": "1700000000",
        "x-seoa-bridge-nonce": "0123456789abcdef0123456789abcdef",
        "x-seoa-bridge-signature": (
            "972c592b46a6bfc387c4b79535f3b8d34386a65857127ffd22018cb64a100371"
        ),
    }
    assert hashlib.sha256(body).hexdigest() == (
        "0ad27d7c6145a4a5cea3f8ea71596a7d26b9697b1af0b4cb7247583c0024b1d6"
    )
    assert verify_signed_request(
        "interop-fixture-material-0123456789abcdef",
        method="POST",
        path=BRIDGE_PATH,
        body=body,
        headers=headers,
        now=1700000000,
    )



def test_readiness_allowlists_dataset_and_checkpoint_status(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO raw_records(dataset,source_key,source_date,payload_sha256) "
        "VALUES('unexpected_private_dataset','X','2026-09-20','x')"
    )
    conn.execute(
        "INSERT INTO collection_checkpoints(dataset,scope_key,status) "
        "VALUES('bid_notice_goods','weird','SENSITIVE_CUSTOM_STATUS')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))

    with readonly_connection() as ro:
        result = _readiness_projection(ro)

    assert "unexpected_private_dataset" not in result["raw_counts"]
    assert result["checkpoint_status_counts"]["bid_notice_goods"]["COMPLETE"] == 1
    assert result["checkpoint_status_counts"]["bid_notice_goods"]["OTHER"] == 1
    assert "SENSITIVE_CUSTOM_STATUS" not in str(result)



def test_old_vnext_schema_is_reported_not_ready_without_migration(monkeypatch, tmp_path):
    path = tmp_path / "old-foundation.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '');
        CREATE TABLE raw_records(
            dataset TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_date TEXT NOT NULL DEFAULT '',
            payload_sha256 TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE classifications(
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            classifier_version TEXT NOT NULL
        );
        CREATE TABLE collection_checkpoints(
            dataset TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'IDLE'
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))

    with readonly_connection() as ro:
        ready = _readiness_projection(ro)
        assert ready["status"] == "FOUNDATION_NOT_READY"
        assert ready["schema_compatible"] is False
        assert ready["missing_columns"]["classifications"] == [
            "source_payload_sha256"
        ]
        with pytest.raises(HTTPException) as exc:
            _procurement_context(
                ro,
                {"kind": "goods", "days": 30, "limit": 10},
            )
    assert exc.value.status_code == 503



def _insert_classified(conn, dataset, source_key, source_date, category, digest):
    conn.execute(
        "INSERT INTO raw_records(dataset,source_key,source_date,payload_sha256) "
        "VALUES(?,?,?,?)",
        (dataset, source_key, source_date, digest),
    )
    conn.execute(
        "INSERT INTO classifications(entity_type,entity_key,primary_category,"
        "classifier_version,source_payload_sha256) VALUES(?,?,?,?,?)",
        (dataset, source_key, category, "1.1.0-rule-v1", digest),
    )


def test_procurement_trend_returns_monthly_aggregate_segments_only(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    conn = sqlite3.connect(path)
    for index, month in enumerate(("2026-06", "2026-07", "2026-08", "2026-09"), start=1):
        for n in range(index):
            _insert_classified(
                conn,
                "bid_notice_goods",
                f"notice-{month}-{n}",
                month + "-10",
                "LIGHTING",
                f"n-{month}-{n}",
            )
        for n in range(index + 1):
            _insert_classified(
                conn,
                "shopping_delivery",
                f"delivery-{month}-{n}",
                month + "-11",
                "LIGHTING",
                f"d-{month}-{n}",
            )
        for n in range(index + 2):
            _insert_classified(
                conn,
                "budget",
                f"budget-{month}-{n}",
                month + "-01",
                "LIGHTING",
                f"b-{month}-{n}",
            )
    # An unsafe free-form category is ignored rather than exported.
    _insert_classified(
        conn,
        "bid_notice_goods",
        "unsafe-row",
        "2026-09-12",
        "A/B",
        "unsafe-digest",
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))

    with readonly_connection() as ro:
        result = _procurement_trend(
            ro,
            {
                "kind": "goods",
                "months": 4,
                "limit": 10,
                "end_month": "2026-09",
            },
        )

    assert result["months"] == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert result["external_api_calls"] == 0
    assert result["writes_performed"] == 0
    assert result["raw_payloads_returned"] is False
    assert result["vendor_identity_returned"] is False
    assert [row["segment"] for row in result["segments"]] == ["LIGHTING"]
    observations = result["segments"][0]["observations"]
    assert observations[0] == {
        "period": "2026-06",
        "opportunity_count": 1,
        "delivery_count": 2,
        "budget_count": 3,
        "source_count": 6,
    }
    assert observations[-1]["opportunity_count"] == 4
    assert observations[-1]["delivery_count"] == 5
    assert observations[-1]["budget_count"] == 6
    assert "unsafe-row" not in str(result)


def test_procurement_trend_rejects_unbounded_or_invalid_periods(monkeypatch, tmp_path):
    path = _foundation_db(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with readonly_connection() as ro:
        for params in (
            {"kind": "goods", "months": 2},
            {"kind": "goods", "months": 25},
            {"kind": "goods", "months": 6, "limit": 21},
            {"kind": "goods", "months": 6, "end_month": "2026-13"},
            {"kind": "construction", "months": 6},
        ):
            with pytest.raises(HTTPException):
                _procurement_trend(ro, params)


def test_bridge_route_procurement_trend_is_read_only(monkeypatch, tmp_path):
    import seoa_bridge as bridge_module

    path = _foundation_db(tmp_path)
    conn = sqlite3.connect(path)
    _insert_classified(
        conn,
        "bid_notice_service",
        "svc-1",
        "2026-09-10",
        "ELECTRICAL",
        "svc-digest",
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setenv("AI_SPACE_SEOA_BRIDGE_SECRET", SECRET)
    monkeypatch.setattr(bridge_module.time, "time", lambda: 1000)
    bridge_module._recent_nonces.clear()

    app = FastAPI()
    register_seoa_bridge_routes(
        app,
        health_reader=lambda: {"status": "ok"},
    )
    body, headers = _signed(
        {
            "operation": "procurement_trend.read",
            "parameters": {
                "kind": "services",
                "months": 3,
                "limit": 5,
                "end_month": "2026-09",
            },
        },
        nonce="9" * 32,
    )
    response = _invoke_bridge(app, body, headers)
    assert response["status"] == "OK"
    assert response["data"]["external_api_calls"] == 0
    assert response["data"]["writes_performed"] == 0
