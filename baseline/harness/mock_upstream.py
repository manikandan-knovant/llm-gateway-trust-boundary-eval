"""Mock OpenAI-compatible upstream for the baseline build 2026-11-09 experiments. Standard library only.

  POST /<target>/v1/chat/completions   (also /v1/chat/completions = target "default")
      waits MOCK_DELAY_MS (default 50) then answers with the LAST user message echoed back,
      rewritten by the current restore variant (exact | single | brackets | bare | stream_split).
      "stream": true -> SSE chunks (16 characters; stream_split: 3 characters, so placeholders split).
  Control (runtime, no restart):
  POST /_fault    {"target": "primary", "mode": "none|503|429|timeout", "timeout_s": 30}
  POST /_variant  {"variant": "exact"}
  POST /_delay    {"delay_ms": 50}
  GET  /_last?target=T      last request body received for target T (synthetic data only)
  GET  /_stats              request counts per target
  GET  /health

Sockets use TCP_NODELAY; HTTP/1.1 keep-alive.
"""

from __future__ import annotations

import json
import os
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
         "counts": {}}
LOCK = threading.Lock()
VARIANTS = {"exact", "single", "brackets", "bare", "stream_split"}


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
    request_queue_size = 256

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
        if u.path == "/health":
            self._json(200, {"status": "ok"})
        elif u.path == "/_last":
            target = (parse_qs(u.query).get("target") or ["default"])[0]
            with LOCK:
                self._json(200, STATE["last"].get(target) or {})
        elif u.path == "/_stats":
            with LOCK:
                self._json(200, {"counts": dict(STATE["counts"]), "variant": STATE["variant"],
                                 "faults": dict(STATE["faults"]), "delay_ms": STATE["delay_ms"]})
        else:
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
        if not path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        parts = [p for p in path.split("/") if p]
        target = parts[0] if parts[0] != "v1" else "default"
        body = self._body()
        with LOCK:
            STATE["last"][target] = body
            STATE["counts"][target] = STATE["counts"].get(target, 0) + 1
            fault = dict(STATE["faults"].get(target) or {"mode": "none"})
            delay, variant = STATE["delay_ms"], STATE["variant"]
        mode = fault.get("mode", "none")
        if mode == "timeout":
            time.sleep(fault.get("timeout_s", 30))
        else:
            time.sleep(delay / 1000.0)
        if mode == "503":
            return self._json(503, {"error": {"message": "mock upstream unavailable", "type": "server_error",
                                              "code": "service_unavailable"}})
        if mode == "429":
            return self._json(429, {"error": {"message": "mock upstream rate limit", "type": "rate_limit_error",
                                              "code": "rate_limit_exceeded"}})
        text = to_variant(_last_user_text(body), variant)
        model = body.get("model", "mock-model")
        prompt_tokens = max(1, sum(len(str(m.get("content", ""))) for m in body.get("messages") or []) // 4)
        completion_tokens = max(1, len(text) // 4)
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens}
        cid, created = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
        if body.get("stream"):
            return self._stream(text, model, cid, created, usage, 3 if variant == "stream_split" else 16,
                                bool((body.get("stream_options") or {}).get("include_usage")))
        self._json(200, {"id": cid, "object": "chat.completion", "created": created, "model": model,
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": text}}],
                         "usage": usage})

    def _stream(self, text: str, model: str, cid: str, created: int, usage: dict, size: int,
                include_usage: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
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
    port = int(os.environ.get("MOCK_PORT", "8080"))
    Server(("0.0.0.0", port), Handler).serve_forever()
