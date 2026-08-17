from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path, old, new, count=1):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise SystemExit(f"{path}: expected {count} occurrence(s), found {found}: {old!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


def remove_exact(path, text, count=1):
    replace_exact(path, text, "", count=count)


# Canonical version module: VERSION.txt is the only product-version source.
version_module = '''"""Canonical product version loaded from VERSION.txt."""\nfrom pathlib import Path\n\n_VERSION_FILE = Path(__file__).with_name("VERSION.txt")\n\n\ndef load_version():\n    raw = _VERSION_FILE.read_text(encoding="utf-8").strip()\n    if not raw:\n        raise RuntimeError("VERSION.txt is empty")\n    version = raw.split()[-1].strip()\n    if not version:\n        raise RuntimeError("VERSION.txt does not contain a version")\n    return version\n\n\nAPP_VERSION = load_version()\nVERSION = APP_VERSION\n\n__all__ = ["APP_VERSION", "VERSION", "load_version"]\n'''
(ROOT / "app_version.py").write_text(version_module, encoding="utf-8")

# main.py: consume canonical version and stop repairing module versions at the end.
replace_exact("main.py", 'VERSION = "2.2"\n', 'from app_version import APP_VERSION, VERSION  # noqa: E402\n')
remove_exact("main.py", 'import server as server_module  # noqa: E402\nserver_module.APP_VERSION = VERSION\n\n')
remove_exact("main.py", 'app_module.APP_VERSION = VERSION\n')
remove_exact("main.py", 'app_module.app.version = VERSION\n')

# app.py: FastAPI is born with the canonical version; no later repair needed.
replace_exact(
    "app.py",
    'from fastapi.responses import HTMLResponse, Response\n\nAPP_VERSION = "2.4.1-api-status-unified"\n',
    'from fastapi.responses import HTMLResponse, Response\n\nfrom app_version import APP_VERSION\n',
)

# server.py: direct import/health/log output use the same canonical value.
replace_exact(
    "server.py",
    'from seed import clear_samples, seed_if_empty\n\nBASE_DIR = os.path.dirname(os.path.abspath(__file__))\nAPP_VERSION = \'2.4.0-sinsung-groups-auth\'\n',
    'from seed import clear_samples, seed_if_empty\nfrom app_version import APP_VERSION\n\nBASE_DIR = os.path.dirname(os.path.abspath(__file__))\n',
)
replace_exact("server.py", "'version':'2.3-reviewed'", "'version':APP_VERSION")
replace_exact(
    "server.py",
    "print(f'LIGHTING SKETCH G2B DATA VIEW v2.3 REVIEWED - http://{HOST}:{PORT}/dashboard')",
    "print(f'LIGHTING SKETCH G2B DATA VIEW v{APP_VERSION} - http://{HOST}:{PORT}/dashboard')",
)

# v2.2 wrapper exposes the canonical value but does not mutate legacy app version fields.
replace_exact(
    "sinsung_v220_app.py",
    'import app as legacy_app\n\nAPP_VERSION = "2.2"\n',
    'import app as legacy_app\nfrom app_version import APP_VERSION\n',
)
remove_exact("sinsung_v220_app.py", 'legacy_app.APP_VERSION = APP_VERSION\n')
remove_exact("sinsung_v220_app.py", 'legacy_app.app.version = APP_VERSION\n')

# Active patches keep their historical patch labels, but must never own product version.
for path in (
    "sinsung_runtime_fix.py",
    "sinsung_ui_restore.py",
    "sinsung_region_fix.py",
    "sinsung_budget_monitor.py",
    "sinsung_v251_patch.py",
    "sinsung_v252_patch.py",
    "sinsung_v220_ui.py",
):
    remove_exact(path, '    s.APP_VERSION = VERSION\n')

print("PHASE2_VERSION_SYNC_APPLIED")
