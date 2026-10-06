"""Portable process launcher for Cafe24 AI SPACE.

Avoids shell-specific ${PORT:-8000} expansion in Procfile and keeps deployment
behavior identical across process managers.
"""
from __future__ import annotations

import os

import memory_guard

# Apply conservative native allocator/thread defaults before importing Uvicorn or
# the application stack. Explicit non-empty operator values remain untouched.
memory_guard.apply_default_process_tuning()

import uvicorn


def resolve_port(value=None):
    raw = str(value if value is not None else os.getenv("PORT", "8000") or "8000").strip()
    try:
        port = int(raw)
    except ValueError:
        port = 8000
    if not 1 <= port <= 65535:
        port = 8000
    return port


def main():
    forwarded = str(os.getenv("FORWARDED_ALLOW_IPS", "*") or "*").strip()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=resolve_port(),
        proxy_headers=True,
        forwarded_allow_ips=forwarded,
        access_log=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
