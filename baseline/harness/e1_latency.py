"""E1: latency added by the gateway against a 50 ms mock upstream (baseline build 2026-11-09).

Arms (each request is one sequential non-streaming chat call; keep-alive client per arm):
  direct     harness -> mock upstream (target "direct"), no gateway
  gw_noscan  harness -> gateway -> mock; team in relaxed_teams on a BAA-only chain: no PHI/PII scan
  gw_cold    harness -> gateway -> mock; scanned; prompt salted per request (detection-cache miss)
  gw_cached  harness -> gateway -> mock; scanned; identical prompt every time (detection-cache hit)

  run (inside the tenant network):  python e1_latency.py run --n 500 --warmup 30 --seed 7
  analyse (host, after the run):    python e1_latency.py analyse

The gateway is restarted (host) before the run so the detection cache starts empty. Cache state per request is
checked afterwards against the gateway's own per-call metadata log (scan.cold / scan.cache_hit_parts).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time

ARMS = ("direct", "gw_noscan", "gw_cold", "gw_cached")


def run(n: int, warmup: int, seed: int) -> None:
    import httpx

    from common import write_csv
    from synthetic_prompts import neutral_prompt, salt

    state = json.loads(os.environ["ITPRO_STATE_A"])
    rng = random.Random(seed)
    gw = "http://gateway:4000/v1/chat/completions"
    targets = {
        "direct": ("http://mock-upstream:8080/direct/v1/chat/completions", None, "mock-model"),
        "gw_noscan": (gw, state["keys"]["bench-relaxed"], "bench"),
        "gw_cold": (gw, state["keys"]["bench-scan"], "bench"),
        "gw_cached": (gw, state["keys"]["bench-scan"], "bench"),
    }
    clients = {a: httpx.Client(timeout=30) for a in ARMS}
    order = [a for a in ARMS for _ in range(warmup)]
    rng.shuffle(order)
    measured = [a for a in ARMS for _ in range(n)]
    rng.shuffle(measured)
    rows = []
    for phase, seq in (("warmup", order), ("measured", measured)):
        for i, arm in enumerate(seq):
            url, key, model = targets[arm]
            text = neutral_prompt() if arm == "gw_cached" else neutral_prompt(salt(rng))
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            body = {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": 50}
            t0 = time.perf_counter()
            r = clients[arm].post(url, json=body, headers=headers)
            _ = r.content
            ms = (time.perf_counter() - t0) * 1000
            rows.append({"phase": phase, "seq": i, "arm": arm, "status": r.status_code, "latency_ms": round(ms, 3),
                         "request_id": r.headers.get("x-gateway-request-id", ""),
                         "pseudonymization": r.headers.get("x-gateway-pseudonymization", "")})
    path = write_csv("e1_latency.csv", rows)
    print(f"E1: {len(rows)} requests written to {path.name}")


def analyse() -> None:
    from common import RESULTS, gateway_metric_lines, write_csv
    from stats import summarize

    rows = list(csv.DictReader(open(RESULTS / "e1_latency.csv", encoding="utf-8")))
    meta = {m.get("request_id"): m for m in gateway_metric_lines("a")}
    out, summary = [], []
    for r in rows:
        m = meta.get(r["request_id"]) or {}
        scan = m.get("scan") or {}
        r["scan_cold"] = str(scan.get("cold", ""))          # CSV strings throughout ("True"/"False"/"")
        r["scan_parts"] = str(scan.get("parts", ""))
        r["scan_cache_hit_parts"] = str(scan.get("cache_hit_parts", ""))
        r["scan_ms"] = str(m.get("scan_ms", ""))
        out.append(r)
    write_csv("e1_latency.csv", out)
    meas = [r for r in out if r["phase"] == "measured"]
    direct = summarize([float(r["latency_ms"]) for r in meas if r["arm"] == "direct" and r["status"] == "200"])
    for arm in ARMS:
        xs = [r for r in meas if r["arm"] == arm]
        ok = [float(r["latency_ms"]) for r in xs if r["status"] == "200"]
        s = summarize(ok)
        check = ""
        if arm == "gw_cold":
            check = f"cold per gateway log: {sum(1 for r in xs if r['scan_cold'] == 'True')}/{len(xs)}"
        elif arm == "gw_cached":
            hits = sum(1 for r in xs if r["scan_parts"] and r["scan_cache_hit_parts"] == r["scan_parts"])
            check = f"all parts from cache per gateway log: {hits}/{len(xs)}"
        elif arm == "gw_noscan":
            check = f"relaxed (no scan) per header: {sum(1 for r in xs if r['pseudonymization'] == 'relaxed')}/{len(xs)}"
        summary.append({"arm": arm, "n_ok": len(ok), "n_err": len(xs) - len(ok), "median_ms": round(s["median"], 2),
                        "p95_ms": round(s["p95"], 2), "p99_ms": round(s["p99"], 2),
                        "added_median_ms": round(s["median"] - direct["median"], 2),
                        "added_p95_ms": round(s["p95"] - direct["p95"], 2),
                        "added_p99_ms": round(s["p99"] - direct["p99"], 2), "cache_check": check})
    write_csv("e1_summary.csv", summary)
    for s in summary:
        print(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyse"])
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--warmup", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    run(a.n, a.warmup, a.seed) if a.cmd == "run" else analyse()
