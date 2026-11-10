"""E12: long agent runs on the fix build (3c468b0).

Each run gets its own per-run key from a flagged issuer (POST /v1/gateway/run-keys). Every turn resends the full
history (system message, all earlier notes and the restored answers) plus one new synthetic note (notes5, seed 12:
5 identifiers from 5 Safe Harbor categories per note), on a route WITHOUT a BAA, so masking applies to the whole
history every turn.

  agent    python e12_agent.py agent --target mock|real --runs N --turns T --budget USD --tag NAME
  revoke   python e12_agent.py revoke --trials 20            (mock: revoke a run key mid-run, time to first refusal)
  analyse  python e12_agent.py analyse                       (host; joins the gateway's per-call log for cache hits)

Per turn: status; restore exactness (mock: the answer equals the new note exactly; real: every value of the new note
verbatim in the answer and no placeholder left); which planted values from the WHOLE history reached the upstream in
clear (mock: GET /_last; real: the body recorder), and whether a value that leaked once leaks again on later turns;
tokens and cost at the route's prices (the mock route is priced like GPT-5 mini so budgets are realistic).
Budget runs stop at the first refusal; overshoot = the gateway's own run usage at refusal - budget.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import time

PRICES = (2.5e-07, 2.0e-06)          # GPT-5 mini rates; the mock route is priced the same (routes-e12.yaml)
SYSTEM = ("You are a care-team assistant. For each new note, rewrite it for the care team in plain language, keeping "
          "every name, date, number, address, identifier and contact detail exactly as written.")
TARGETS = {"mock": ("proj2", "phi", None), "real": ("agent-app", "real-mini", "recorder-openai")}


def _post(c, path, key, body):
    return c.post(f"http://gateway:4000{path}", json=body, headers={"Authorization": f"Bearer {key}"})


def agent(target: str, runs: int, turns: int, budgets: list[float], tag: str, offset: int) -> None:
    import httpx

    from common5 import RESULTS
    from notes5 import generate
    from scoring import residual_placeholders

    issuer_team, model, recorder = TARGETS[target]
    state = json.loads(os.environ["ITPRO_STATE"])
    issuer = state["issuers"][issuer_team]
    notes = [n for n in generate(1200, 0, seed=12) if not n.negative]
    rows, refusals = [], []
    with httpx.Client(timeout=300) as c:
        since = max([e["seq"] for e in c.get(f"http://{recorder}:8080/_log").json()] or [0]) if recorder else 0
        for i in range(runs):
            run_id = f"e12-{tag}-{i:02d}"
            budget = budgets[i % len(budgets)]
            mint = _post(c, "/v1/gateway/run-keys", issuer, {"run_id": run_id, "models": [model], "ttl_seconds": 3600,
                                                              "max_budget": budget})
            mint.raise_for_status()
            key = mint.json()["key"]
            history = [{"role": "system", "content": SYSTEM}]
            seen = []
            for t in range(1, turns + 1):
                note = notes[(offset + i * turns + t - 1) % len(notes)]
                history.append({"role": "user", "content": note.text})
                body = {"model": model, "messages": history}
                body.update({"max_tokens": 400} if target == "mock" else
                            {"max_completion_tokens": 400, "reasoning_effort": "minimal"})
                t0 = time.perf_counter()
                r = c.post("http://gateway:4000/v1/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}", "x-gateway-session-id": run_id})
                ms = (time.perf_counter() - t0) * 1000
                final, usage, code = "", {}, ""
                if r.status_code == 200:
                    j = r.json()
                    final, usage = j["choices"][0]["message"].get("content") or "", j.get("usage") or {}
                else:
                    try:
                        code = str((r.json().get("error") or {}).get("code") or "")
                    except ValueError:
                        code = ""
                if recorder:
                    log = c.get(f"http://{recorder}:8080/_log", params={"since": since}).json()
                    if log:
                        since = log[-1]["seq"]
                    sent = json.dumps(log[-1]["request"]["messages"]) if (log and r.status_code == 200) else ""
                else:
                    sent = json.dumps(c.get("http://mock-upstream:8080/_last", params={"target": "phi"}).json()
                                      .get("messages", [])) if r.status_code == 200 else ""
                seen.append((t, note))
                leaked = []
                if sent:
                    for (tt, n) in seen:
                        for k, e in enumerate(n.entities):
                            if json.dumps(e.value)[1:-1] in sent:
                                leaked.append(f"{tt}:{k}:{e.type}")
                exact = (final == note.text) if target == "mock" else \
                    (r.status_code == 200 and all(e.value in final for e in note.entities)
                     and not residual_placeholders(final))
                pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                rows.append({"tag": tag, "target": target, "run": run_id, "turn": t, "status": r.status_code,
                             "error_code": code, "latency_ms": round(ms, 1),
                             "request_id": r.headers.get("x-gateway-request-id", ""),
                             "messages_sent": len(history), "prompt_tokens": pt, "completion_tokens": ct,
                             "cost_usd": round(pt * PRICES[0] + ct * PRICES[1], 8),
                             "restore_exact": r.status_code == 200 and exact,
                             "values_in_history": sum(len(n.entities) for _, n in seen),
                             "values_in_clear": len(leaked), "leaked_ids": "|".join(leaked)})
                if r.status_code != 200:
                    history.pop()
                    refusals.append({"run": run_id, "turn": t, "status": r.status_code, "code": code})
                    break
                history.append({"role": "assistant", "content": final})
            time.sleep(6)   # spend log rows are written in batches before the usage read
            u = c.get(f"http://gateway:4000/v1/gateway/run-keys/{run_id}/usage",
                      headers={"Authorization": f"Bearer {issuer}"})
            rv = _post(c, "/v1/gateway/run-keys/revoke", issuer, {"run_id": run_id})
            usage = u.json() if u.status_code == 200 else {}
            refusals.append({"run": run_id, "budget_usd": budget, "usage_status": u.status_code,
                             "gateway_cost_usd": usage.get("cost_usd"), "gateway_calls": usage.get("calls"),
                             "revoke_status": rv.status_code})
            print(f"E12 {tag} {run_id}: {sum(1 for r in rows if r['run'] == run_id)} turns, "
                  f"usage {usage.get('cost_usd')} USD")
    for name, data in ((f"e12_turns_{tag}.csv", rows), (f"e12_runs_{tag}.csv", refusals)):
        with open(RESULTS / name, "w", newline="", encoding="utf-8") as fh:
            fields = []
            for d in data:
                for k in d:
                    if k not in fields:
                        fields.append(k)
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(data)


def revoke(trials: int) -> None:
    import threading

    import httpx

    from common5 import RESULTS, write_csv

    state = json.loads(os.environ["ITPRO_STATE"])
    issuer = state["issuers"]["proj2"]
    out = []
    for k in range(trials):
        with httpx.Client(timeout=60) as c:
            run_id = f"e12-revoke-{k:02d}"
            key = _post(c, "/v1/gateway/run-keys", issuer, {"run_id": run_id, "models": ["phi"], "ttl_seconds": 600,
                                                             "max_budget": 1.0}).json()["key"]
            calls: list[tuple[float, float, int]] = []
            stop = threading.Event()

            def loop() -> None:
                with httpx.Client(timeout=60) as cc:
                    while not stop.is_set():
                        s = time.time()
                        r = cc.post("http://gateway:4000/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {key}"},
                                    json={"model": "phi", "max_tokens": 20,
                                          "messages": [{"role": "user", "content": "Summarize: stable vitals."}]})
                        calls.append((s, time.time(), r.status_code))
                        if r.status_code != 200:
                            return

            th = threading.Thread(target=loop)
            th.start()
            time.sleep(2.0)
            rv0 = time.time()
            rv = _post(c, "/v1/gateway/run-keys/revoke", issuer, {"run_id": run_id})
            rv1 = time.time()
            th.join(timeout=20)
            stop.set()
            refused = next(((s, e, st) for s, e, st in calls if st != 200), None)
            ok_after = sum(1 for s, e, st in calls if st == 200 and s >= rv1)
            out.append({"trial": k, "revoke_status": rv.status_code, "revoke_call_ms": round((rv1 - rv0) * 1000, 1),
                        "calls_before_revoke": sum(1 for s, e, st in calls if s < rv0),
                        "calls_ok_started_after_revoke_returned": ok_after,
                        "first_refusal_status": refused[2] if refused else "",
                        "refused_after_revoke_returned_ms": round((refused[1] - rv1) * 1000, 1) if refused else "",
                        "refused_after_revoke_started_ms": round((refused[1] - rv0) * 1000, 1) if refused else ""})
            print(out[-1])
    write_csv("e12_revocation.csv", out)


def analyse() -> None:
    from common5 import RESULTS, gateway_metric_lines, write_csv
    from stats import percentile, wilson

    def rate(k, n):
        lo, hi = wilson(k, n)
        return f"{k}/{n} = {100 * k / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]" if n else "n/a"

    meta = {}
    for tenant in ("e12", "real"):
        try:
            meta.update({m.get("request_id"): m for m in gateway_metric_lines(tenant)})
        except Exception:  # noqa: BLE001 - a torn-down tenant has no log
            pass
    summary = []
    for tag in ("mock", "real"):
        p = RESULTS / f"e12_turns_{tag}.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
        for r in rows:
            scan = (meta.get(r["request_id"]) or {}).get("scan") or {}
            r["scan_parts"], r["scan_cache_hit_parts"] = scan.get("parts", ""), scan.get("cache_hit_parts", "")
        write_csv(f"e12_turns_{tag}.csv", rows)
        first_leak: dict[tuple, int] = {}
        for r in sorted(rows, key=lambda r: (r["run"], int(r["turn"]))):
            for lid in filter(None, r["leaked_ids"].split("|")):
                first_leak.setdefault((r["run"], lid), int(r["turn"]))
        for t in sorted({int(r["turn"]) for r in rows}):
            ts = [r for r in rows if int(r["turn"]) == t and r["status"] == "200"]
            parts = sum(int(r["scan_parts"] or 0) for r in ts)
            hits = sum(int(r["scan_cache_hit_parts"] or 0) for r in ts)
            leaks = [(r["run"], lid) for r in ts for lid in filter(None, r["leaked_ids"].split("|"))]
            repeats = sum(1 for key in leaks if first_leak[key] < t)
            lat = [float(r["latency_ms"]) for r in ts]
            summary.append({"tag": tag, "turn": t, "calls_ok": len(ts),
                            "restore_exact": rate(sum(r["restore_exact"] == "True" for r in ts), len(ts)),
                            "cache_hit_rate": rate(hits, parts) if parts else "n/a",
                            "values_in_clear": f"{len(leaks)}/{sum(int(r['values_in_history']) for r in ts)}",
                            "of_which_repeats_of_an_earlier_turn": repeats,
                            "latency_median_ms": round(percentile(lat, 50), 1) if lat else "",
                            "mean_prompt_tokens": round(sum(int(r["prompt_tokens"]) for r in ts) / len(ts), 1) if ts else ""})
        values_leaked_once = {k for k in first_leak}
        never_masked_later = 0
        for (run, lid), t0 in first_leak.items():
            later = [r for r in rows if r["run"] == run and int(r["turn"]) > t0 and r["status"] == "200"]
            if later and all(lid in r["leaked_ids"].split("|") for r in later):
                never_masked_later += 1
        print(f"{tag}: values that reached the upstream in clear at least once: {len(values_leaked_once)}; of those "
              f"with later turns, leaked again on EVERY later turn: {never_masked_later}")
    write_csv("e12_per_turn.csv", summary)
    budget = []
    for tag in ("mock-budget", "real-budget"):
        p = RESULTS / f"e12_runs_{tag}.csv"
        if not p.exists():
            continue
        recs = list(csv.DictReader(open(p, encoding="utf-8")))
        ref = {r["run"]: r for r in recs if r.get("code")}
        for r in recs:
            if r.get("budget_usd"):
                b, cost = float(r["budget_usd"]), float(r["gateway_cost_usd"] or 0)
                budget.append({"tag": tag, "run": r["run"], "budget_usd": b, "gateway_spend_usd": cost,
                               "refused_at_turn": ref.get(r["run"], {}).get("turn", "not refused"),
                               "refusal": f"{ref[r['run']]['status']} {ref[r['run']]['code']}" if r["run"] in ref else "",
                               "overshoot_usd": round(cost - b, 8), "overshoot_pct": round(100 * (cost - b) / b, 1)})
    write_csv("e12_budget.csv", budget)
    for b in budget:
        print(b)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["agent", "revoke", "analyse"])
    ap.add_argument("--target", default="mock")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--turns", type=int, default=30)
    ap.add_argument("--budget", default="5.0")
    ap.add_argument("--tag", default="mock")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--trials", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "agent":
        agent(a.target, a.runs, a.turns, [float(b) for b in a.budget.split(",")], a.tag, a.offset)
    elif a.cmd == "revoke":
        revoke(a.trials)
    else:
        analyse()
