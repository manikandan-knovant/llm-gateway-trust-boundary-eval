"""E10: stock LiteLLM vs. The gateway under the E2 fault cases (mock upstream, $0).

Same LiteLLM 1.102.1 in both arms. Stock: a plain config with a fallback from fo-primary to fo-fallback and LiteLLM's
defaults for everything else (stack/stock-litellm.yaml), no gateway hooks. Gateway: the fix build (3c468b0), tenant
A, logical model "failover" = [fo-primary--mock (BAA, cleared for restricted + PHI/PII), fo-fallback--mock (no BAA)].

Open-loop load at 5 requests/s for 75 s; fault on the primary from 10 to 30 s; requests alternate:
  PHI-labelled:  a synthetic patient note, header x-gateway-data-classification "restricted; PII"
  non-PHI:       a short identifier-free prompt, header "internal"
Every request carries a marker "REQ-..." so the mock's own log shows which route(s) it reached and how often.
Faults: 503, 429, timeout (the primary hangs 30 s; route timeout 5 s in both arms).

  python e10_stock.py run stock|gateway 503|429|timeout     (inside that arm's network)
  python e10_stock.py analyse                              (host)
"""

from __future__ import annotations

import asyncio
import collections
import csv
import json
import os
import random
import re
import sys
import time

from synthetic_prompts import generate, neutral_prompt

RATE, NORMAL_S, FAULT_S, TOTAL_S = 5.0, 10.0, 20.0, 75.0
ARMS = {"stock": ("http://litellm:4000/v1/chat/completions", "fo-primary"),
        "gateway": ("http://gateway:4000/v1/chat/completions", "failover")}
INTERNALS = re.compile(r"(?i)litellm|fo-primary|fo-fallback|--mock|fallbacks|openaiexception|mock upstream|"
                       r"http://|api_base|model group|traceback|deployment")


async def run(arm: str, scenario: str) -> None:
    import httpx

    from common5 import RESULTS

    url, model = ARMS[arm]
    key = os.environ["ARM_KEY"]
    notes = generate(400, seed=1010)
    rng = random.Random(f"{arm}-{scenario}")
    rows: list[dict] = []
    mock = "http://mock-upstream:8080"
    async with httpx.AsyncClient(timeout=90, limits=httpx.Limits(max_connections=400)) as client:
        await client.post(f"{mock}/_fault", json={"target": "primary", "mode": "none"})
        since = max([e["seq"] for e in (await client.get(f"{mock}/_log")).json()] or [0])
        start = time.perf_counter()

        async def one(i: int) -> None:
            phi = i % 2 == 0
            marker = f"REQ-{arm}-{scenario}-{i:04d}-{'PHI' if phi else 'NON'}"
            text = (notes[i].text if phi else neutral_prompt(f"q{rng.getrandbits(40):x}")) + f" ({marker})"
            headers = {"Authorization": f"Bearer {key}",
                       "x-gateway-data-classification": "restricted; PII" if phi else "internal"}
            t_send = time.perf_counter() - start
            t0 = time.perf_counter()
            try:
                r = await client.post(url, headers=headers, json={"model": model, "max_tokens": 50,
                                                                  "messages": [{"role": "user", "content": text}]})
                status, body, rh = r.status_code, r.text, r.headers
            except httpx.HTTPError as exc:
                status, body, rh = 0, f"client error {type(exc).__name__}", {}
            code = ""
            if status != 200:
                try:
                    code = str((json.loads(body).get("error") or {}).get("code") or "")
                except ValueError:
                    pass
            rows.append({"arm": arm, "scenario": scenario, "i": i, "marker": marker, "kind": "PHI" if phi else "NON",
                         "t_send_s": round(t_send, 3), "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                         "status": status, "error_code": code,
                         "error_body": body[:400] if status != 200 else "",
                         "body_exposes_internals": bool(INTERNALS.search(body)) if status != 200 else "",
                         "served_route_header": rh.get("x-gateway-served-route", "")})

        async def fault() -> None:
            await asyncio.sleep(NORMAL_S)
            await client.post(f"{mock}/_fault", json={"target": "primary",
                                                      "mode": scenario, "timeout_s": 30})
            await asyncio.sleep(FAULT_S)
            await client.post(f"{mock}/_fault", json={"target": "primary", "mode": "none"})

        tasks = [asyncio.create_task(fault())]
        for i in range(int(TOTAL_S * RATE)):
            delay = start + i / RATE - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(i)))
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)
        log = (await client.get(f"{mock}/_log", params={"since": since})).json()
    hits = collections.defaultdict(collections.Counter)
    for e in log:
        if e["marker"]:
            hits[e["marker"]][e["target"]] += 1
    for r in rows:
        h = hits.get(r["marker"], {})
        r["primary_attempts"], r["fallback_hits"] = h.get("primary", 0), h.get("fallback", 0)
    rows.sort(key=lambda r: r["i"])
    path = RESULTS / f"e10_{arm}_{scenario}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"E10 {arm} {scenario}: {len(rows)} requests, markers seen by the mock {len(hits)}")


def analyse() -> None:
    from common5 import RESULTS, write_csv
    from stats import summarize, wilson

    out = []
    for arm in ARMS:
        for sc in ("503", "429", "timeout"):
            p = RESULTS / f"e10_{arm}_{sc}.csv"
            if not p.exists():
                continue
            rows = list(csv.DictReader(open(p, encoding="utf-8")))
            during = [r for r in rows if NORMAL_S <= float(r["t_send_s"]) < NORMAL_S + FAULT_S]
            phi = [r for r in during if r["kind"] == "PHI"]
            non = [r for r in during if r["kind"] == "NON"]
            phi_fb = sum(int(r["fallback_hits"]) > 0 for r in phi)
            lo, hi = wilson(phi_fb, len(phi))
            ok_lat = [float(r["latency_ms"]) for r in during if r["status"] == "200"]
            fail = [r for r in during if r["status"] != "200"]
            att = [int(r["primary_attempts"]) for r in during]
            exposing = sum(r["body_exposes_internals"] == "True" for r in fail)
            out.append({
                "arm": arm, "fault": sc, "requests_during_fault": len(during),
                "phi_requests_reaching_non_baa_fallback": f"{phi_fb}/{len(phi)} = {100 * phi_fb / len(phi):.1f}% "
                                                          f"[{100 * lo:.1f}, {100 * hi:.1f}]",
                "non_phi_served_by_fallback": f"{sum(r['status'] == '200' and int(r['fallback_hits']) > 0 for r in non)}"
                                              f"/{len(non)}",
                "failed_phi": sum(r["status"] != "200" for r in phi), "failed_non_phi": sum(r["status"] != "200" for r in non),
                "latency_ok_median_ms": round(summarize(ok_lat)["median"], 1) if ok_lat else "",
                "latency_ok_p95_ms": round(summarize(ok_lat)["p95"], 1) if ok_lat else "",
                "latency_failed_median_ms": round(summarize([float(r["latency_ms"]) for r in fail])["median"], 1)
                if fail else "",
                "primary_attempts_per_request_mean": round(sum(att) / len(att), 2) if att else "",
                "primary_attempts_max": max(att) if att else "",
                "error_statuses": "; ".join(sorted({f"{r['status']} {r['error_code']}".strip() for r in fail})),
                "error_bodies_exposing_internals": f"{exposing}/{len(fail)}" if fail else "0/0",
                "example_error_body": fail[0]["error_body"][:300] if fail else "",
            })
    write_csv("e10_summary.csv", out)
    for o in out:
        print(json.dumps(o))


if __name__ == "__main__":
    asyncio.run(run(sys.argv[2], sys.argv[3])) if sys.argv[1] == "run" else analyse()
