"""Guard the additive vNext foundation from becoming a production import side effect."""
import ast
from pathlib import Path


PRODUCTION_RUNTIME_FILES = (
    "app.py", "app_version.py", "budget_sync.py", "collector_v200.py",
    "db.py", "education_budget_sync.py", "g2b_sync.py", "main.py",
    "scheduler.py", "seed.py", "server.py",
    "sinsung_budget_flash_fix.py", "sinsung_budget_item_mapping.py",
    "sinsung_budget_monitor.py", "sinsung_education_budget.py",
    "sinsung_history_vendor_fix.py", "sinsung_region_fix.py",
    "sinsung_runtime_fix.py", "sinsung_signup_approval.py",
    "sinsung_ui_restore.py", "sinsung_v200_reset.py", "sinsung_v210_auto.py",
    "sinsung_v220_app.py", "sinsung_v220_stability.py", "sinsung_v220_ui.py",
    "sinsung_v251_patch.py", "sinsung_v252_patch.py",
)

VNEXT_MODULES = frozenset({
    "analysis_vnext", "award_projection", "award_vnext", "bid_vnext",
    "budget_appropriation_vnext", "budget_collection_status_vnext",
    "budget_notice_links_vnext", "budget_organization_vnext",
    "budget_procurement_lifecycle_vnext", "budget_projection_vnext",
    "budget_read_vnext", "budget_reorganize_vnext", "budget_snapshot_vnext",
    "budget_targets_vnext", "budget_vnext", "budget_vnext_canary",
    "classification_vnext", "contract_projection", "contract_vnext",
    "education_budget_vnext", "g2b_vnext_canary", "g2b_vnext_pipeline",
    "historical_vnext", "lofin_vnext_http", "notice_identity_vnext",
    "projection_store_vnext", "readiness_vnext",
    "service_lifecycle_status_vnext", "service_reorganize_vnext",
    "shopping_vnext", "vnext_collection", "vnext_finalize_guard",
    "vnext_http", "vnext_live_gate", "vnext_paging", "vnext_provenance",
    "vnext_response", "vnext_schema", "vnext_source_guard",
    "vnext_stability", "vnext_store",
})


def _root(name):
    return str(name or "").split(".", 1)[0]


def _dynamic_import_name(node):
    if not isinstance(node, ast.Call) or not node.args:
        return ""
    func = node.func
    is_importlib = (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
        and func.attr == "import_module"
    )
    is_builtin = isinstance(func, ast.Name) and func.id == "__import__"
    if not (is_importlib or is_builtin):
        return ""
    arg = node.args[0]
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else ""


def test_existing_production_runtime_does_not_import_vnext_modules():
    root = Path(".")
    collisions = []
    missing = []
    for filename in PRODUCTION_RUNTIME_FILES:
        path = root / filename
        if not path.exists():
            missing.append(filename)
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    candidate = _root(alias.name)
                    if candidate in VNEXT_MODULES:
                        collisions.append((filename, node.lineno, candidate, "import"))
            elif isinstance(node, ast.ImportFrom):
                module = _root(node.module)
                if module in VNEXT_MODULES:
                    collisions.append((filename, node.lineno, module, "from"))
            dynamic = _root(_dynamic_import_name(node))
            if dynamic in VNEXT_MODULES:
                collisions.append((filename, node.lineno, dynamic, "dynamic"))

    assert not missing, f"production runtime file list drifted: {missing}"
    assert not collisions, (
        "vNext must remain additive and unimported by existing production runtime; "
        f"collisions={collisions}"
    )
