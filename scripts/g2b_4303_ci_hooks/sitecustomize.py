"""Explicit CI PYTHONPATH only; never imported by a deployment entry point."""
import os
import sys

if os.environ.get("G2B_CI_PARENT_4303") == "1":
    try:
        sys.path.insert(0, "/app/scripts")
        from g2b_4303_parent_child_ci import install_child_hook
        install_child_hook()
    except BaseException:
        # Python otherwise swallows sitecustomize errors and continues running.
        # Never allow an uninstrumented child to contact a real source endpoint.
        os._exit(98)
