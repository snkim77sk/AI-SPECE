from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _requirements():
    rows = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            rows.append(value)
    return rows


def test_cafe24_uv_manifest_matches_requirements_exactly():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]

    assert project["name"] == "sinsung-g2b-vnext"
    assert project["version"] == "4.1.165"
    assert project["requires-python"] == ">=3.11,<3.13"
    assert sorted(project["dependencies"]) == sorted(_requirements())


def test_cafe24_uv_manifest_version_matches_release():
    version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip().split()[-1]
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["version"] == version
