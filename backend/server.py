"""Packaged backend entry point.

Starts the FastAPI app under uvicorn. Used by the PyInstaller bundle
(backend.exe) and as a convenience for running the server directly.
"""

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8790, log_level="warning")
