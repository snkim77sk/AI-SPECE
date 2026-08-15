"""Top-level AI SPACE entrypoint shim.

The actual application lives in a nested folder that was created by the
GitHub web upload. Rather than moving 15 files (and risking corruption of
the 66KB server.py), this shim puts that folder on sys.path and re-exports
the FastAPI ``app`` object so ``uvicorn main:app`` works from the repo root.
"""
import os
import sys

_APP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "LIGHTING_SKETCH_G2B_GITHUB_READY_v2.3.1 (1)",
)

if not os.path.isdir(_APP_DIR):
    raise RuntimeError(f"application folder not found: {_APP_DIR}")

sys.path.insert(0, _APP_DIR)
# server.py resolves static/ and data/ relative to the working directory.
os.chdir(_APP_DIR)

from app import app  # noqa: E402

__all__ = ["app"]
