"""Body-recording pass-through to the real provider (baseline build 2026-11-09, E4-real and E6). Stdlib only.

  POST /v1/<path>   forwarded to UPSTREAM (default https://api.openai.com) with the caller's headers;
                    the request BODY and the response BODY are kept in memory (ring of the last 500 exchanges).
  GET  /_log?since=N   exchanges with sequence number > N: {"seq", "request", "response", "status"}
  GET  /health

Never records, logs or returns any request or response HEADER (the Authorization header carries the key).
Only synthetic data passes through it.
"""

from __future__ import annotations

import collections
import http.client
import json
import os
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

UPSTREAM = urlparse(os.environ.get("RECORDER_UPSTREAM", "https://api.openai.com"))
FORWARD_HEADERS = ("authorization", "content-type", "accept", "openai-organization", "openai-project", "user-agent")
LOG: collections.deque = collections.deque(maxlen=500)
LOCK = threading.Lock()
SEQ = [0]
CTX = ssl.create_default_context()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        sock, addr = super().get_request()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock, addr


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:   # no access log at all (nothing about requests leaves this process)
        pass

    def _send(self, status: int, data: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, b'{"status":"ok"}')
        if u.path == "/_log":
            since = int((parse_qs(u.query).get("since") or ["0"])[0])
            with LOCK:
                out = [e for e in LOG if e["seq"] > since]
            return self._send(200, json.dumps(out).encode())
        self._send(404, b"{}")

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_HEADERS}
        headers["Content-Length"] = str(len(body))
        conn_cls = http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": CTX} if UPSTREAM.scheme == "https" else {}
        conn = conn_cls(UPSTREAM.hostname, UPSTREAM.port, timeout=300, **kwargs)
        try:
            conn.request("POST", self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status, ctype = resp.status, resp.getheader("Content-Type") or "application/json"
        except Exception as exc:  # noqa: BLE001 - report only the exception type
            status, data, ctype = 502, json.dumps({"error": {"message": f"recorder: {type(exc).__name__}"}}).encode(), \
                "application/json"
        finally:
            conn.close()
        try:
            req_obj = json.loads(body or b"{}")
        except ValueError:
            req_obj = None
        try:
            resp_obj = json.loads(data or b"{}")
        except ValueError:
            resp_obj = None
        with LOCK:
            SEQ[0] += 1
            LOG.append({"seq": SEQ[0], "path": self.path, "status": status, "request": req_obj, "response": resp_obj})
        self._send(status, data, ctype)


if __name__ == "__main__":
    Server(("0.0.0.0", int(os.environ.get("RECORDER_PORT", "8080"))), Handler).serve_forever()
