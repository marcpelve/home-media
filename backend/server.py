"""Packaged backend entry point.

Starts the FastAPI app under uvicorn. Used by the PyInstaller bundle
(backend.exe) and as a convenience for running the server directly.
"""

import os
import sys

# Windowed PyInstaller builds (console=False) leave sys.stdout/stderr as None,
# which crashes uvicorn's default logging formatter (it calls .isatty() on None
# during configure_logging). Point them at devnull before uvicorn runs so the
# logging configures cleanly. In console/dev mode these are already set.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8790, log_level="warning")
