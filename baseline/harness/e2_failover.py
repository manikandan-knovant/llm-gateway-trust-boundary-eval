"""E2: failover on the primary route with a non-BAA fallback (baseline build 2026-11-09).

Logical model "failover" = [fo-primary--mock (BAA, restricted + PHI/PII), fo-fallback--mock (no BAA, internal)].
Open-loop load at 5 requests/s for 75 s: 0-10 s normal, 10-30 s fault on the primary, 30-75 s recovery.

  scenario a: primary returns 503; internal data (fallback is cleared)
  scenario b: primary returns 429; internal data
  scenario c: primary returns 503; requests declared "restricted; PII" (fallback NOT cleared for them)
  scenario d: primary hangs (30 s); both routes have a 5 s route timeout

  run (inside the tenant network, gateway restarted by the host first):  python e2_failover.py run a
  analyse (host):                                                         python e2_failover.py analyse
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import time

SCENARIOS = {"a": ("503", None), "b": ("429", None), "c": ("503", "restricted; PII"), "d": ("timeout", None)}
RATE, NORMAL_S, FAULT_S, TOTAL_S = 5.0, 10.0, 20.0, 75.0


async def run(scenario: str) -> None:
    import httpx

    from common import RESULTS
    from synthetic_prompts import neutral_prompt, salt

    mode, classification = SCENARIOS[scenario]
    key = json.loads(os.environ["ITPRO_STATE_A"])["keys"]["fo-app"]
    rng = random.Random(100 + ord(scenario))
    headers = {"Authorization": f"Bearer {key}"}
    if classification:
        headers["x-gateway-data-classification"] = classification
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=200)) as client:
        await client.post("http://mock-upstream:8080/_fault", json={"target": "primary", "mode": "none"})
        start = time.perf_counter()

        async def one(i: int, t_send: float) -> None:
            body = {"model": "failover", "messages": [{"role": "user", "content": neutral_prompt(salt(rng))}],
                    "max_tokens": 50}
            t0 = time.perf_counter()
            try:
                r = await client.post("http://gateway:4000/v1/chat/completions", json=body, headers=headers)
                status, rh, text = r.status_code, r.headers, r.text
            except httpx.HTTPError as exc:
                status, rh, text = 0, {}, f"client error {type(exc).__name__}"
            t1 = time.perf_counter()
            code = ""
            if status != 200:
                try:
                    code = (json.loads(text).get("error") or {}).get("code") or ""
                except ValueError:
                    code = ""
            rows.append({"scenario": scenario, "i": i, "t_send_s": round(t_send, 3), "t_done_s": round(t1 - start, 3),
                         "latency_ms": round((t1 - t0) * 1000, 1), "status": status, "error_code": code,
                         "served_route": rh.get("x-gateway-served-route", ""),
                         "fallback": rh.get("x-gateway-fallback", ""),
                         "error_body": text[:300] if status != 200 else ""})

        async def fault_switch() -> None:
            await asyncio.sleep(NORMAL_S)
            await client.post("http://mock-upstream:8080/_fault",
                              json={"target": "primary", "mode": mode, "timeout_s": 30})
            await asyncio.sleep(FAULT_S)
            await client.post("http://mock-upstream:8080/_fault", json={"target": "primary", "mode": "none"})

        tasks = [asyncio.create_task(fault_switch())]
        n = int(TOTAL_S * RATE)
        for i in range(n):
            t_send = i / RATE
            delay = start + t_send - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(i, time.perf_counter() - start)))
        await asyncio.gather(*tasks)
    rows.sort(key=lambda r: r["i"])
    path = RESULTS / f"e2_failover_{scenario}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"E2 {scenario}: {len(rows)} requests -> {path.name}")


def analyse() -> None:
    from common import RESULTS, write_csv
    from stats import summarize

    out = []
    for sc in SCENARIOS:
        p = RESULTS / f"e2_failover_{sc}.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
        for r in rows:
            r["t_send_s"], r["latency_ms"] = float(r["t_send_s"]), float(r["latency_ms"])
        f0, f1 = NORMAL_S, NORMAL_S + FAULT_S
        before = [r for r in rows if r["t_send_s"] < f0]
        during = [r for r in rows if f0 <= r["t_send_s"] < f1]
        after = [r for r in rows if r["t_send_s"] >= f1]
        fb = [r for r in during if r["status"] == "200" and r["served_route"] == "fo-fallback--mock"]
        first_fb = min((r["t_send_s"] for r in fb), default=None)
        first_primary_after = min((r["t_send_s"] for r in after
                                   if r["status"] == "200" and r["served_route"] == "fo-primary--mock"), default=None)
        ok_during = [r["latency_ms"] for r in during if r["status"] == "200"]
        failed_lat = [r["latency_ms"] for r in during if r["status"] != "200"]
        first_fb_latency = next((r["latency_ms"] for r in sorted(fb, key=lambda r: r["t_send_s"])), None)
        errs = sorted({f"{r['status']} {r['error_code']}".strip() for r in rows if r["status"] != "200"})
        out.append({
            "scenario": sc, "fault": SCENARIOS[sc][0], "classification": SCENARIOS[sc][1] or "internal (default)",
            "requests_total": len(rows),
            "failed_before": sum(r["status"] != "200" for r in before),
            "failed_during": sum(r["status"] != "200" for r in during), "requests_during": len(during),
            "served_by_fallback_during": len(fb),
            "time_to_switch_s": round(first_fb - f0, 2) if first_fb is not None else "",
            "first_fallback_request_latency_ms": first_fb_latency if first_fb_latency is not None else "",
            "failed_during_median_latency_ms": round(summarize(failed_lat)["median"], 1) if failed_lat else "",
            "latency_during_median_ms": round(summarize(ok_during)["median"], 1) if ok_during else "",
            "latency_during_p95_ms": round(summarize(ok_during)["p95"], 1) if ok_during else "",
            "failed_after": sum(r["status"] != "200" for r in after),
            "served_by_fallback_after": sum(r["status"] == "200" and r["served_route"] == "fo-fallback--mock"
                                            for r in after),
            "recovery_to_primary_s": round(first_primary_after - f1, 2) if first_primary_after is not None else "",
            "error_statuses": "; ".join(errs),
            "example_error_body": next((r["error_body"] for r in rows if r["status"] != "200"), "")[:300],
        })
    write_csv("e2_summary.csv", out)
    for o in out:
        print(json.dumps(o))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyse"])
    ap.add_argument("scenario", nargs="?", default="a")
    a = ap.parse_args()
    asyncio.run(run(a.scenario)) if a.cmd == "run" else analyse()
