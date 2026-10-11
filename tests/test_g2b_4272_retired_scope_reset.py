"""4.1.272: obsolete G2B 4.0 reset can never erase current storage."""
import ast
import inspect

import pytest

import db
import result_snapshot_vnext
import v4_scope_migration as legacy
import vnext_store


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("old_flag", ["0", "1"])
def test_retired_reset_cannot_open_storage_or_source_even_if_forced(
    monkeypatch, force, old_flag,
):
    monkeypatch.setenv("G2B_V41_FRESH_START", old_flag)
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", old_flag)

    forbidden = []

    def block(name):
        def no_io(*_args, **_kwargs):
            forbidden.append(name)
            raise AssertionError("retired reset attempted " + name)
        return no_io

    for name in ("connect", "get_setting", "set_setting"):
        monkeypatch.setattr(db, name, block("db." + name))
    monkeypatch.setattr(vnext_store, "ensure_foundation", block("foundation"))
    monkeypatch.setattr(result_snapshot_vnext, "clear_snapshot", block("snapshot"))

    result = legacy.apply_v4_scope_reset(force=force)
    assert result["status"] == "RETIRED"
    assert result["reason"] == "G2B_V4_SCOPE_RESET_RETIRED"
    assert result["deleted"] == {}
    assert result["already_applied"] is None
    assert result["snapshot_cleared"] is False
    assert result["force_ignored"] is force
    assert result["budget_preserved"] is True
    assert result["shopping_preserved"] is True
    assert result["checkpoints_preserved"] is True
    assert result["result_snapshot_preserved"] is True
    assert result["source_api_calls"] == 0
    assert forbidden == []


def test_old_scope_dataset_names_are_inert_compatibility_metadata():
    assert legacy.MIGRATION_KEY == "g2b_v4_scope_reset_data_complete"
    assert legacy.SNAPSHOT_MIGRATION_KEY == "g2b_v4_scope_reset_snapshot_complete"
    assert legacy.MIGRATION_VALUE == "1"
    assert "shopping_delivery" in legacy.RESET_DATASETS
    assert "bid_notice_service" in legacy.RESET_DATASETS
    assert "shopping_recent_state" in legacy.SHOPPING_STATE_KEYS


def test_retired_scope_reset_module_has_no_db_or_mutating_imports():
    source = inspect.getsource(legacy)
    parsed = ast.parse(source)

    imports = [
        node for node in parsed.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
        )
    ]
    assert not imports, "compatibility shim must not import DB or source code"

    for token in (
        "DELETE FROM", "DROP SCHEMA", "TRUNCATE ",
        ".execute(", ".connect(", ".unlink(", "os.remove(",
        "clear_snapshot(", "set_setting(", "get_setting(",
        "ensure_foundation(",
    ):
        assert token not in source, "dangerous historical action: " + token


def test_reset_function_body_has_no_calls_even_after_old_flags():
    tree = ast.parse(inspect.getsource(legacy.apply_v4_scope_reset))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Name)
    assert calls[0].func.id == "bool", "only boolean coercion is allowed"
    assert not any(
        isinstance(node, (ast.Await, ast.YieldFrom))
        for node in ast.walk(tree)
    )


def test_bootstrap_does_not_call_legacy_scope_migration():
    import vnext_clean_app

    boot_source = inspect.getsource(vnext_clean_app.initialize_backend)
    assert "apply_v4_scope_reset" not in boot_source
    assert "v4_scope_migration" not in boot_source
