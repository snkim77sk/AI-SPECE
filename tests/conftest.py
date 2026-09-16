"""All regression tests use disposable SQLite and deny real source-network traffic."""
import socket
import pytest
import db
from vnext_store import ensure_foundation


@pytest.fixture(autouse=True)
def isolated_regression_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'isolated.sqlite3'))
    monkeypatch.setenv('G2B_SERVICE_KEY', '')
    monkeypatch.setenv('LOFIN_API_KEY', '')
    monkeypatch.setenv('G2B_AUTO_SYNC', '0')
    def blocked(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_IN_REGRESSION')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    db.init_db()
    ensure_foundation()
