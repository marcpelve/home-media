"""Static server for desktop/dist that sends no-cache headers.

Use this instead of `python -m http.server` during frontend development so a
plain browser reload always picks up the latest JS/CSS (the default http.server
uses heuristic caching, which serves stale main.js after an edit).
"""

import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "dist"


class NoCacheHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    print(f"Serving {ROOT} on http://127.0.0.1:{PORT} (no-cache)")
    HTTPServer(("127.0.0.1", PORT), NoCacheHandler).serve_forever()
