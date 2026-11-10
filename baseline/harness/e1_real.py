"""E1-real: added latency with a real provider (baseline build 2026-11-09). GPT-5 mini snapshot, OpenAI direct.

Arms, interleaved in random order (seed 7), from the same container in the tenant network:
  direct    harness -> https://api.openai.com/v1/chat/completions (model gpt-5-mini-2025-08-07)
  gateway   harness -> gateway -> the same model (logical model real-mini; route without BAA, so the PHI/PII scan
            runs on every request, cold because each prompt is salted)
Identical parameters in both arms: max_completion_tokens 50, reasoning_effort minimal, one short salted prompt.

The key comes from GW_OPENAI_API_KEY (set for this process only by keyenv.py). For a failed call only the status
and error code/type are stored, never the message (provider auth errors can echo part of a key).

  python e1_real.py run --n 200 --warmup 5 --seed 7      (inside the real tenant network)
  python e1_real.py analyse                               (host)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time

MODEL = "gpt-5-mini-2025-08-07"
PRICE_IN, PRICE_OUT = 2.5e-07, 2.0e-06   # USD per token, LiteLLM 1.102.1 price map entry for the snapshot


def _err(r) -> str:
    try:
        e = r.json().get("error") or {}
        return str(e.get("code") or e.get("type") or "")
    except Exception:  # noqa: BLE001
        return ""


def run(n: int, warmup: int, seed: int) -> None:
    import httpx

    from common import write_csv
    from synthetic_prompts import neutral_prompt, salt

    key = os.environ["GW_OPENAI_API_KEY"]
    app_key = json.loads(os.environ["ITPRO_STATE_REAL"])["keys"]["real-app"]
    rng = random.Random(seed)
    arms = {"direct": ("https://api.openai.com/v1/chat/completions", key, MODEL),
            "gateway": ("http://gateway:4000/v1/chat/completions", app_key, "real-mini")}
    clients = {a: httpx.Client(timeout=120) for a in arms}
    warm = [a for a in arms for _ in range(warmup)]
    rng.shuffle(warm)
    meas = [a for a in arms for _ in range(n)]
    rng.shuffle(meas)
    rows = []
    for phase, seq in (("warmup", warm), ("measured", meas)):
        for i, arm in enumerate(seq):
            url, k, model = arms[arm]
            body = {"model": model, "messages": [{"role": "user", "content": neutral_prompt(salt(rng))}],
                    "max_completion_tokens": 50, "reasoning_effort": "minimal"}
            t0 = time.perf_counter()
            r = clients[arm].post(url, json=body, headers={"Authorization": f"Bearer {k}"})
            _ = r.content
            ms = (time.perf_counter() - t0) * 1000
            usage, served = {}, ""
            if r.status_code == 200:
                j = r.json()
                usage, served = j.get("usage") or {}, j.get("model", "")
            pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            rows.append({"phase": phase, "seq": i, "arm": arm, "status": r.status_code, "error_code": _err(r)
                         if r.status_code != 200 else "", "latency_ms": round(ms, 1), "model": served,
                         "prompt_tokens": pt, "completion_tokens": ct,
                         "cost_usd": round(pt * PRICE_IN + ct * PRICE_OUT, 8),
                         "gateway_cost_header": r.headers.get("x-gateway-cost-usd", "") if arm == "gateway" else "",
                         "pseudonymization": r.headers.get("x-gateway-pseudonymization", "") if arm == "gateway" else ""})
    write_csv("e1_real.csv", rows)
    print(f"E1-real: {len(rows)} calls; non-200: {sum(r['status'] != 200 for r in rows)}")


def analyse() -> None:
    from common import RESULTS, write_csv
    from stats import paired_median_difference, summarize

    rows = list(csv.DictReader(open(RESULTS / "e1_real.csv", encoding="utf-8")))
    meas = [r for r in rows if r["phase"] == "measured"]
    out = {}
    for arm in ("direct", "gateway"):
        ok = [float(r["latency_ms"]) for r in meas if r["arm"] == arm and r["status"] == "200"]
        out[arm] = (summarize(ok), [float(r["latency_ms"]) for r in meas if r["arm"] == arm])
    # pairs: k-th direct call with the k-th gateway call in interleaved order
    d_seq = [float(r["latency_ms"]) for r in meas if r["arm"] == "direct"]
    g_seq = [float(r["latency_ms"]) for r in meas if r["arm"] == "gateway"]
    m = min(len(d_seq), len(g_seq))
    paired = paired_median_difference(d_seq[:m], g_seq[:m])
    d, g = out["direct"][0], out["gateway"][0]
    summary = []
    for arm in ("direct", "gateway"):
        s = out[arm][0]
        summary.append({"arm": arm, "n_ok": s["n"], "n_err": sum(1 for r in meas if r["arm"] == arm
                                                                  and r["status"] != "200"),
                        "median_ms": round(s["median"], 1), "p95_ms": round(s["p95"], 1),
                        "p99_ms": round(s["p99"], 1), "min_ms": round(s["min"], 1), "max_ms": round(s["max"], 1)})
    summary.append({"arm": "gateway minus direct", "n_ok": m, "n_err": "",
                    "median_ms": round(g["median"] - d["median"], 1), "p95_ms": round(g["p95"] - d["p95"], 1),
                    "p99_ms": "", "min_ms": "", "max_ms": ""})
    summary.append({"arm": "paired median difference (k-th gateway - k-th direct)", "n_ok": m, "n_err": "",
                    "median_ms": round(paired, 1), "p95_ms": "", "p99_ms": "", "min_ms": "", "max_ms": ""})
    write_csv("e1_real_summary.csv", summary)
    spend = sum(float(r["cost_usd"]) for r in rows)
    print(json.dumps(summary, indent=1))
    print(f"E1-real computed spend (all calls incl. warm-up): ${spend:.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyse"])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    run(a.n, a.warmup, a.seed) if a.cmd == "run" else analyse()
