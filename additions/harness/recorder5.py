"""Body-recording pass-through to a real provider (E7, E12). Stdlib only.

As baseline/harness/recorder_proxy.py, plus:
  * ``upstream_ms``: the time from forwarding the request to the provider until its full answer arrived, so the
    gateway's own time can be separated from the provider's (gateway-added = client latency - upstream_ms);
  * forwards the Anthropic headers (x-api-key, anthropic-version, anthropic-beta) besides OpenAI's.
Never records, logs or returns a request or response HEADER (they carry the keys). Only synthetic data passes.
RECORDER_UPSTREAM: the provider base (scheme + host), from the environment only.
"""

from __future__ import annotations

import collections
import http.client
import json
import os
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

UPSTREAM = urlparse(os.environ.get("RECORDER_UPSTREAM", "https://api.openai.com"))
FORWARD = ("authorization", "content-type", "accept", "x-api-key", "anthropic-version", "anthropic-beta",
           "user-agent")
LOG: collections.deque = collections.deque(maxlen=5000)
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

    def log_message(self, *args) -> None:
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
        headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD}
        headers["Content-Length"] = str(len(body))
        cls = http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": CTX} if UPSTREAM.scheme == "https" else {}
        conn = cls(UPSTREAM.hostname, UPSTREAM.port, timeout=300, **kwargs)
        t0 = time.perf_counter()
        try:
            conn.request("POST", self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status, ctype = resp.status, resp.getheader("Content-Type") or "application/json"
        except Exception as exc:  # noqa: BLE001 - the exception type only
            status, data, ctype = 502, json.dumps({"error": {"message": f"recorder: {type(exc).__name__}"}}).encode(), \
                "application/json"
        finally:
            conn.close()
        upstream_ms = (time.perf_counter() - t0) * 1000
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
            LOG.append({"seq": SEQ[0], "path": urlparse(self.path).path, "status": status,
                        "upstream_ms": round(upstream_ms, 1), "request": req_obj, "response": resp_obj})
        self._send(status, data, ctype)


if __name__ == "__main__":
    Server(("0.0.0.0", int(os.environ.get("RECORDER_PORT", "8080"))), Handler).serve_forever()
