"""Mock OpenAI-compatible upstream for the research additions (E8-E12). Standard library only.

As baseline/harness/mock_upstream.py (echo of the last user message, restore variants, runtime faults,
TCP_NODELAY, keep-alive), plus:
  * every request is logged (target, the request marker "REQ-..." found in the request, arrival time): GET /_log
  * x-mock-service-ms: the time this mock spent on the request (its fixed delay), so callers can subtract it
  * POST /_reset clears counts and the log
  * MOCK_DELAY_MS sets the default delay (E9 uses 200)
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scoring import to_variant  # noqa: E402

STATE = {"delay_ms": float(os.environ.get("MOCK_DELAY_MS", "50")), "variant": "exact", "faults": {}, "last": {},
         "counts": {}, "log": [], "seq": 0}
LOCK = threading.Lock()
VARIANTS = {"exact", "single", "brackets", "bare", "stream_split"}
MARKER = re.compile(r"REQ-[A-Za-z0-9_-]+")


def _last_user_text(body: dict) -> str:
    for msg in reversed(body.get("messages") or []):
        if msg.get("role") == "user":
            c = msg.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 1024

    def get_request(self):
        sock, addr = super().get_request()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock, addr


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _json(self, status: int, obj, headers: dict | None = None) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._json(200, {"status": "ok"})
        if u.path == "/_last":
            with LOCK:
                return self._json(200, STATE["last"].get((q.get("target") or ["default"])[0]) or {})
        if u.path == "/_log":
            since = int((q.get("since") or ["0"])[0])
            with LOCK:
                return self._json(200, [e for e in STATE["log"] if e["seq"] > since])
        if u.path == "/_stats":
            with LOCK:
                return self._json(200, {"counts": dict(STATE["counts"]), "variant": STATE["variant"],
                                        "faults": dict(STATE["faults"]), "delay_ms": STATE["delay_ms"]})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/_fault":
            b = self._body()
            with LOCK:
                STATE["faults"][b.get("target", "default")] = {"mode": b.get("mode", "none"),
                                                              "timeout_s": float(b.get("timeout_s", 30))}
            return self._json(200, {"ok": True})
        if path == "/_variant":
            v = self._body().get("variant", "exact")
            if v not in VARIANTS:
                return self._json(400, {"error": f"unknown variant {v}"})
            with LOCK:
                STATE["variant"] = v
            return self._json(200, {"ok": True})
        if path == "/_delay":
            with LOCK:
                STATE["delay_ms"] = float(self._body().get("delay_ms", 50))
            return self._json(200, {"ok": True})
        if path == "/_reset":
            with LOCK:
                STATE["counts"], STATE["log"], STATE["last"] = {}, [], {}
            return self._json(200, {"ok": True})
        if not path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        t0 = time.perf_counter()
        parts = [p for p in path.split("/") if p]
        target = parts[0] if parts[0] != "v1" else "default"
        body = self._body()
        user = _last_user_text(body)
        marker = MARKER.search(json.dumps(body))
        with LOCK:
            STATE["seq"] += 1
            STATE["last"][target] = body
            STATE["counts"][target] = STATE["counts"].get(target, 0) + 1
            STATE["log"].append({"seq": STATE["seq"], "target": target, "marker": marker.group(0) if marker else "",
                                 "t": time.time()})
            if len(STATE["log"]) > 200_000:
                STATE["log"] = STATE["log"][-100_000:]
            fault = dict(STATE["faults"].get(target) or {"mode": "none"})
            delay, variant = STATE["delay_ms"], STATE["variant"]
        mode = fault.get("mode", "none")
        time.sleep(fault.get("timeout_s", 30) if mode == "timeout" else delay / 1000.0)
        served = {"x-mock-service-ms": f"{(time.perf_counter() - t0) * 1000:.2f}"}
        if mode == "503":
            return self._json(503, {"error": {"message": "mock upstream unavailable", "type": "server_error",
                                              "code": "service_unavailable"}}, served)
        if mode == "429":
            return self._json(429, {"error": {"message": "mock upstream rate limit", "type": "rate_limit_error",
                                              "code": "rate_limit_exceeded"}}, served)
        text = to_variant(user, variant)
        model = body.get("model", "mock-model")
        pt = max(1, sum(len(str(m.get("content", ""))) for m in body.get("messages") or []) // 4)
        ct = max(1, len(text) // 4)
        usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
        cid, created = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
        if body.get("stream"):
            return self._stream(text, model, cid, created, usage, 3 if variant == "stream_split" else 16,
                                bool((body.get("stream_options") or {}).get("include_usage")), served)
        self._json(200, {"id": cid, "object": "chat.completion", "created": created, "model": model,
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": text}}], "usage": usage}, served)

    def _stream(self, text, model, cid, created, usage, size, include_usage, served) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        for k, v in served.items():
            self.send_header(k, v)
        self.end_headers()

        def send(obj) -> None:
            data = b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n"
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
        send({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        for i in range(0, len(text), size):
            send({**base, "choices": [{"index": 0, "delta": {"content": text[i:i + size]}, "finish_reason": None}]})
        send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if include_usage:
            send({**base, "choices": [], "usage": usage})
        send(b"[DONE]")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    Server(("0.0.0.0", int(os.environ.get("MOCK_PORT", "8080"))), Handler).serve_forever()
