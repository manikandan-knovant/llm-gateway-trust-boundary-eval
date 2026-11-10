"""E6: agent loop with per-run keys on a real model (baseline build 2026-11-09). GPT-5 mini snapshot.

20 runs x 5 turns. Each run gets its OWN per-run key from a flagged issuer key (POST /v1/gateway/run-keys:
TTL 900 s, budget USD 0.05, model real-mini only). Each turn resends the full history plus one new synthetic
patient-style message (generator seed 6060), so masking applies to the whole history every turn.
One extra run gets a budget small enough to be exceeded, to show the refusal.

Measured per turn: status, latency, tokens and cost, whether the provider received ANY planted value from the whole
history (recorded body), restore success for the new message (every value of it in the answer, no placeholder left).
Per run (host analysis): total cost (computed and the gateway's own run usage), detection-cache hit rate per turn from
The gateway's per-call metadata log.

  python e6_agent.py run        (inside the real tenant network, gateway on the recorder)
  python e6_agent.py analyse    (host)
"""

from __future__ import annotations

import collections
import csv
import json
import os
import sys
import time

RUNS, TURNS, SEED = 20, 5, 6060
RUN_BUDGET, TINY_BUDGET = 0.05, 0.0002
PRICE_IN, PRICE_OUT = 2.5e-07, 2.0e-06
SYSTEM = ("You are a care-team assistant. For each new note, reply with a one-sentence summary that keeps every "
          "name, date, phone number and record number exactly as written.")


def run() -> None:
    import httpx

    from common import write_csv
    from scoring import residual_placeholders
    from synthetic_prompts import generate

    issuer = json.loads(os.environ["ITPRO_STATE_REAL"])["issuers"]["agent-app"]
    prompts = generate(RUNS * TURNS + 10, SEED)
    turns, runs = [], []
    with httpx.Client(timeout=300) as c:
        since = max([e["seq"] for e in c.get("http://recorder:8080/_log").json()] or [0])

        def mint(run_id: str, budget: float) -> str:
            r = c.post("http://gateway:4000/v1/gateway/run-keys", headers={"Authorization": f"Bearer {issuer}"},
                       json={"run_id": run_id, "models": ["real-mini"], "ttl_seconds": 900, "max_budget": budget})
            r.raise_for_status()
            return r.json()["key"]

        def turn(run_id: str, key: str, history: list, t: int, p) -> dict:
            nonlocal since
            history.append({"role": "user", "content": p.text})
            body = {"model": "real-mini", "messages": history, "max_completion_tokens": 100,
                    "reasoning_effort": "minimal"}
            t0 = time.perf_counter()
            r = c.post("http://gateway:4000/v1/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}", "x-gateway-session-id": run_id})
            ms = (time.perf_counter() - t0) * 1000
            final, usage, code = "", {}, ""
            if r.status_code == 200:
                j = r.json()
                final, usage = j["choices"][0]["message"].get("content") or "", j.get("usage") or {}
                history.append({"role": "assistant", "content": final})
            else:
                try:
                    code = str((r.json().get("error") or {}).get("code") or "")
                except ValueError:
                    code = ""
                history.pop()
            log = c.get("http://recorder:8080/_log", params={"since": since}).json()
            if log:
                since = log[-1]["seq"]
            sent = json.dumps(log[-1]["request"]) if (log and r.status_code == 200) else ""
            planted = [e.value for q in seen[run_id] for e in q.entities] + [e.value for e in p.entities]
            leaks = sum(v in sent for v in planted) if sent else ""
            seen[run_id].append(p)
            pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            return {"run": run_id, "turn": t, "status": r.status_code, "error_code": code, "latency_ms": round(ms, 1),
                    "request_id": r.headers.get("x-gateway-request-id", ""), "messages_sent": len(body["messages"]),
                    "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": round(pt * PRICE_IN + ct * PRICE_OUT, 8),
                    "planted_values_in_history": len(planted), "values_leaked_to_provider": leaks,
                    "new_values_in_answer": sum(e.value in final for e in p.entities) if final else 0,
                    "restore_success": r.status_code == 200 and all(e.value in final for e in p.entities)
                    and not residual_placeholders(final),
                    "masking_header": r.headers.get("x-gateway-pseudonymization", "")}

        seen: dict = collections.defaultdict(list)
        for i in range(RUNS):
            run_id = f"e6-run-{i:02d}"
            key = mint(run_id, RUN_BUDGET)
            history = [{"role": "system", "content": SYSTEM}]
            for t in range(1, TURNS + 1):
                turns.append(turn(run_id, key, history, t, prompts[i * TURNS + t - 1]))
            time.sleep(8)   # spend log rows are written in batches before the usage read
            usage = c.get(f"http://gateway:4000/v1/gateway/run-keys/{run_id}/usage",
                          headers={"Authorization": f"Bearer {issuer}"})
            rev = c.post("http://gateway:4000/v1/gateway/run-keys/revoke", json={"run_id": run_id},
                         headers={"Authorization": f"Bearer {issuer}"})
            runs.append({"run": run_id, "budget_usd": RUN_BUDGET, "usage_status": usage.status_code,
                         "gateway_usage": json.dumps(usage.json() if usage.status_code == 200 else {}),
                         "revoke_status": rev.status_code})
        # budget enforcement: a run whose budget is smaller than a few calls
        run_id = "e6-run-budget"
        key = mint(run_id, TINY_BUDGET)
        history = [{"role": "system", "content": SYSTEM}]
        for t in range(1, 9):
            row = turn(run_id, key, history, t, prompts[RUNS * TURNS + t - 1])
            turns.append(row)
            if row["status"] != 200:
                break
            time.sleep(3)   # let the spend update land before the next call
        usage = c.get(f"http://gateway:4000/v1/gateway/run-keys/{run_id}/usage",
                      headers={"Authorization": f"Bearer {issuer}"})
        rev = c.post("http://gateway:4000/v1/gateway/run-keys/revoke", json={"run_id": run_id},
                     headers={"Authorization": f"Bearer {issuer}"})
        runs.append({"run": run_id, "budget_usd": TINY_BUDGET, "usage_status": usage.status_code,
                     "gateway_usage": json.dumps(usage.json() if usage.status_code == 200 else {}),
                     "revoke_status": rev.status_code})
    write_csv("e6_turns.csv", turns)
    write_csv("e6_runs.csv", runs)
    print(f"E6: {len(turns)} turns in {len(runs)} runs; non-200: {sum(t['status'] != 200 for t in turns)}")


def analyse() -> None:
    from common import RESULTS, gateway_metric_lines, write_csv
    from stats import summarize, wilson

    turns = list(csv.DictReader(open(RESULTS / "e6_turns.csv", encoding="utf-8")))
    meta = {m.get("request_id"): m for m in gateway_metric_lines("real")}
    for t in turns:
        scan = (meta.get(t["request_id"]) or {}).get("scan") or {}
        t["scan_parts"], t["scan_cache_hit_parts"] = str(scan.get("parts", "")), str(scan.get("cache_hit_parts", ""))
    write_csv("e6_turns.csv", turns)
    normal = [t for t in turns if t["run"] != "e6-run-budget"]
    per_turn = []
    for k in range(1, TURNS + 1):
        ts = [t for t in normal if int(t["turn"]) == k]
        parts = sum(int(t["scan_parts"] or 0) for t in ts)
        hits = sum(int(t["scan_cache_hit_parts"] or 0) for t in ts)
        ok = sum(t["restore_success"] == "True" for t in ts)
        lo, hi = wilson(ok, len(ts))
        lat = summarize([float(t["latency_ms"]) for t in ts if t["status"] == "200"])
        per_turn.append({"turn": k, "calls": len(ts), "restore_success": ok, "ci95_low": round(lo, 4),
                         "ci95_high": round(hi, 4), "median_latency_ms": round(lat["median"], 1),
                         "p95_latency_ms": round(lat["p95"], 1), "scan_parts": parts, "cache_hit_parts": hits,
                         "cache_hit_rate": round(hits / parts, 4) if parts else "",
                         "values_leaked_to_provider": sum(int(t["values_leaked_to_provider"] or 0) for t in ts),
                         "mean_prompt_tokens": round(sum(int(t["prompt_tokens"]) for t in ts) / len(ts), 1)})
    write_csv("e6_per_turn.csv", per_turn)
    runs = list(csv.DictReader(open(RESULTS / "e6_runs.csv", encoding="utf-8")))
    per_run = []
    for r in runs:
        ts = [t for t in turns if t["run"] == r["run"]]
        u = json.loads(r["gateway_usage"] or "{}")
        per_run.append({"run": r["run"], "budget_usd": r["budget_usd"], "turns": len(ts),
                        "turns_ok": sum(t["status"] == "200" for t in ts),
                        "refusals": "; ".join(f"turn {t['turn']}: {t['status']} {t['error_code']}" for t in ts
                                              if t["status"] != "200"),
                        "computed_cost_usd": round(sum(float(t["cost_usd"]) for t in ts), 6),
                        "gateway_usage_cost_usd": u.get("cost_usd", (u.get("usage") or {}).get("cost_usd", "")),
                        "gateway_usage_calls": u.get("calls", (u.get("usage") or {}).get("calls", "")),
                        "total_latency_ms": round(sum(float(t["latency_ms"]) for t in ts), 1),
                        "restore_success_turns": sum(t["restore_success"] == "True" for t in ts),
                        "usage_status": r["usage_status"], "revoke_status": r["revoke_status"]})
    write_csv("e6_per_run.csv", per_run)
    print(json.dumps(per_turn, indent=1))
    for r in per_run:
        print(r)
    print(f"E6 computed spend: ${sum(float(t['cost_usd']) for t in turns):.4f}")


if __name__ == "__main__":
    run() if sys.argv[1] == "run" else analyse()
