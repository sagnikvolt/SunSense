"""
Local development server: serves ../frontend and runs the Lambda handler at POST /api/calculate,
so the full app (including PVGIS) works on your laptop before AWS is set up.

    python backend/dev_server.py            -> http://localhost:8000

Binds to 127.0.0.1 only (not reachable from other devices).
"""

from __future__ import annotations

import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from handler import MAX_BODY_BYTES, lambda_handler  # noqa: E402

FRONTEND = os.path.join(os.path.dirname(HERE), "frontend")
PORT = int(os.environ.get("PORT", "8000"))


class Handler(SimpleHTTPRequestHandler):
    def _lambda(self, method: str, body: str = ""):
        event = {"requestContext": {"http": {"method": method}}, "rawPath": self.path.split("?")[0],
                 "headers": {"origin": self.headers.get("Origin") or f"http://localhost:{PORT}"}, "body": body}
        out = lambda_handler(event)
        data = out["body"].encode()
        self.send_response(out["statusCode"])
        for k, v in out["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.split("?")[0] not in ("/api/calculate", "/api/pvgis"):
            return self.send_error(404)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY_BYTES:
            return self.send_error(413)
        self._lambda("POST", self.rfile.read(n).decode("utf-8", "replace"))

    def do_OPTIONS(self):
        self._lambda("OPTIONS")

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + fmt % args + "\n")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=FRONTEND))
    print(f"SunSense dev server: http://localhost:{PORT}  (API at /api/calculate, Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
