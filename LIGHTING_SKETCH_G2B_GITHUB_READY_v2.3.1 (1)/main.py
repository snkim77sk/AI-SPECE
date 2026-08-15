"""Canonical Cafe24 AI SPACE entrypoint.

AI SPACE/PaaS platforms commonly auto-detect ``main.py`` with an ``app``
FastAPI object. The full implementation lives in app.py.
"""
from app import app

__all__ = ["app"]
