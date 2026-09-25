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
    monkeypatch.setenv('EDUINFO_API_KEY', '')
    monkeypatch.setenv('G2B_AUTO_SYNC', '0')
    # Test-only runtime proof key. Production validation workflows generate a fresh
    # ephemeral key file per run and never upload it with validation artifacts.
    monkeypatch.setenv(
        'G2B_VNEXT_PROVENANCE_KEY',
        'test-only-g2b-vnext-provenance-key-0123456789abcdef',
    )
    monkeypatch.delenv('G2B_VNEXT_PROVENANCE_KEY_FILE', raising=False)
    def blocked(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_IN_REGRESSION')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    db.init_db()
    ensure_foundation()
