"""E13: stock LiteLLM 1.102.1 with its own tag-based routing, under the E10 fault script (mock upstream, $0).

Config (stack/e13-litellm-tags.yaml, identical for every variant): fo-primary (BAA) tagged ["phi", "default"],
fo-fallback (no BAA) tagged ["default"], router_settings.enable_tag_filtering true, fallbacks fo-primary -> fo-fallback,
everything else LiteLLM's default (as E10). Load, fault window and request mix are E10's: open loop 5 req/s for 75 s,
fault on the primary from 10 to 30 s, requests alternate PHI note / identifier-free prompt, each with a "REQ-..." marker
so the mock's log shows which route(s) each request reached and how often.

Variants (only the key and the request tags differ):
  A        tag in the request: PHI requests send body "tags": ["phi"]; key without tags
  B-other  tag on the virtual key (key metadata.tags ["phi"]); the client sends a different tag, ["internal"]
  B-none   tag on the virtual key (key metadata.tags ["phi"]); the client sends no tag
  C-key    supplementary, not in the brief: the documented required-tag form "&phi" on the key (metadata.tags
           ["&phi"]); the client sends no tag
Non-PHI requests use a key without tags and send no tags in every variant.

Key tags: the documented /key/generate field "tags" is refused without an Enterprise licence in 1.102.1 ("Only premium
users can add tags to keys"); the proxy stores that field as metadata.tags on the key, so the run sets metadata.tags
directly (same stored value). Both calls are recorded in results/e13_key_setup.json (status and message, never a key).

  python e13_tags.py smoke                    (inside the itpro-add-e13 network)
  python e13_tags.py run VARIANT 503|429|timeout
  python e13_tags.py analyse                  (host)
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
URL = "http://litellm:4000"
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")   # local throwaway stack master key
MOCK = "http://mock-upstream:8080"
VARIANTS = {  # name: (key metadata tags for PHI requests, request tags for PHI requests)
    "A": (None, ["phi"]),
    "B-other": (["phi"], ["internal"]),
    "B-none": (["phi"], None),
    "C-key": (["&phi"], None),
}
INTERNALS = re.compile(r"(?i)litellm|fo-primary|fo-fallback|--mock|fallbacks|openaiexception|mock upstream|"
                       r"http://|api_base|model group|traceback|deployment")
RESP_HEADERS = ("x-litellm-model-api-base", "x-litellm-attempted-fallbacks", "x-litellm-attempted-retries",
                "x-litellm-model-group")


async def make_key(client, body: dict) -> tuple[int, str, str]:
    r = await client.post(f"{URL}/key/generate", headers={"Authorization": f"Bearer {MASTER}"}, json=body)
    try:
        j = r.json()
    except ValueError:
        j = {}
    msg = "" if r.status_code == 200 else str((j.get("error") or {}).get("message") or j.get("detail") or r.text)[:300]
    return r.status_code, j.get("key", "") if r.status_code == 200 else "", msg


async def keys_for(client, variant: str) -> tuple[str, str]:
    key_tags, _ = VARIANTS[variant]
    _, plain, _ = await make_key(client, {"key_alias": f"e13-{variant}-plain-{time.time_ns()}"})
    if key_tags is None:
        return plain, plain
    st, phi_key, msg = await make_key(client, {"key_alias": f"e13-{variant}-phi-{time.time_ns()}",
                                               "metadata": {"tags": key_tags}})
    assert st == 200, msg
    return phi_key, plain


async def key_setup_record() -> None:
    import httpx

    from common5 import RESULTS
    async with httpx.AsyncClient(timeout=30) as c:
        out = {}
        for name, body in (("documented field: {\"tags\": [\"phi\"]}", {"tags": ["phi"]}),
                           ("key metadata: {\"metadata\": {\"tags\": [\"phi\"]}}", {"metadata": {"tags": ["phi"]}}),
                           ("key metadata: {\"metadata\": {\"tags\": [\"&phi\"]}}", {"metadata": {"tags": ["&phi"]}})):
            st, key, msg = await make_key(c, body)
            info = {}
            if key:
                ri = await c.get(f"{URL}/key/info", params={"key": key}, headers={"Authorization": f"Bearer {MASTER}"})
                info = {"stored_metadata_tags": ((ri.json().get("info") or {}).get("metadata") or {}).get("tags")}
            out[name] = {"status": st, "error_message": msg, **info}
    (RESULTS / "e13_key_setup.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


async def one_call(client, key: str, tags, marker: str, text: str):
    body = {"model": "fo-primary", "max_tokens": 50, "messages": [{"role": "user", "content": f"{text} ({marker})"}]}
    if tags is not None:
        body["tags"] = tags
    return await client.post(f"{URL}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)


async def smoke() -> None:
    import httpx
    async with httpx.AsyncClient(timeout=90) as c:
        await key_setup_record()
        note = generate(2, seed=1313)[0].text
        for variant in VARIANTS:
            phi_key, plain = await keys_for(c, variant)
            for fault in ("none", "503"):
                await c.post(f"{MOCK}/_fault", json={"target": "primary", "mode": fault})
                since = max([e["seq"] for e in (await c.get(f"{MOCK}/_log")).json()] or [0])
                res = []
                for kind, key, tags, text in (("PHI", phi_key, VARIANTS[variant][1], note),
                                              ("NON", plain, None, neutral_prompt("smoke"))):
                    m = f"REQ-e13smoke-{variant}-{fault}-{kind}"
                    r = await one_call(c, key, tags, m, text)
                    res.append((kind, r.status_code, {h: r.headers.get(h, "") for h in RESP_HEADERS[1:3]},
                                r.text[:160] if r.status_code != 200 else ""))
                log = (await c.get(f"{MOCK}/_log", params={"since": since})).json()
                hits = collections.Counter((e["marker"].rsplit("-", 1)[-1], e["target"]) for e in log)
                print(variant, fault, res, dict(hits))
        await c.post(f"{MOCK}/_fault", json={"target": "primary", "mode": "none"})


async def run(variant: str, scenario: str) -> None:
    import httpx

    from common5 import RESULTS

    notes = generate(400, seed=1010)
    rng = random.Random(f"e13-{variant}-{scenario}")
    rows: list[dict] = []
    tag_v = variant.lower().replace("-", "")
    async with httpx.AsyncClient(timeout=90, limits=httpx.Limits(max_connections=400)) as client:
        phi_key, plain = await keys_for(client, variant)
        await client.post(f"{MOCK}/_fault", json={"target": "primary", "mode": "none"})
        await asyncio.sleep(2)
        since = max([e["seq"] for e in (await client.get(f"{MOCK}/_log")).json()] or [0])
        start = time.perf_counter()

        async def one(i: int) -> None:
            phi = i % 2 == 0
            marker = f"REQ-e13{tag_v}-{scenario}-{i:04d}-{'PHI' if phi else 'NON'}"
            text = notes[i].text if phi else neutral_prompt(f"q{rng.getrandbits(40):x}")
            tags = VARIANTS[variant][1] if phi else None
            t_send = time.perf_counter() - start
            t0 = time.perf_counter()
            try:
                r = await one_call(client, phi_key if phi else plain, tags, marker, text)
                status, body, rh = r.status_code, r.text, r.headers
            except httpx.HTTPError as exc:
                status, body, rh = 0, f"client error {type(exc).__name__}", {}
            code = ""
            if status != 200:
                try:
                    code = str((json.loads(body).get("error") or {}).get("code") or "")
                except ValueError:
                    pass
            rows.append({"variant": variant, "scenario": scenario, "i": i, "marker": marker,
                         "kind": "PHI" if phi else "NON", "request_tags": json.dumps(tags),
                         "t_send_s": round(t_send, 3), "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                         "status": status, "error_code": code, "error_body": body[:400] if status != 200 else "",
                         "body_exposes_internals": bool(INTERNALS.search(body)) if status != 200 else "",
                         "hdr_attempted_retries": rh.get("x-litellm-attempted-retries", ""),
                         "hdr_attempted_fallbacks": rh.get("x-litellm-attempted-fallbacks", ""),
                         "hdr_served_api_base_is_fallback": "/fallback/" in rh.get("x-litellm-model-api-base", "")})

        async def fault() -> None:
            await asyncio.sleep(NORMAL_S)
            await client.post(f"{MOCK}/_fault", json={"target": "primary", "mode": scenario, "timeout_s": 30})
            await asyncio.sleep(FAULT_S)
            await client.post(f"{MOCK}/_fault", json={"target": "primary", "mode": "none"})

        tasks = [asyncio.create_task(fault())]
        for i in range(int(TOTAL_S * RATE)):
            delay = start + i / RATE - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(i)))
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)
        log = (await client.get(f"{MOCK}/_log", params={"since": since})).json()
    hits = collections.defaultdict(collections.Counter)
    for e in log:
        if e["marker"]:
            hits[e["marker"]][e["target"]] += 1
    for r in rows:
        h = hits.get(r["marker"], {})
        r["primary_attempts"], r["fallback_hits"] = h.get("primary", 0), h.get("fallback", 0)
    rows.sort(key=lambda r: r["i"])
    with open(RESULTS / f"e13_{variant}_{scenario}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"E13 {variant} {scenario}: {len(rows)} requests, markers seen by the mock {len(hits)}")


def analyse() -> None:
    from common5 import RESULTS, write_csv
    from stats import summarize, wilson

    out = []
    for variant in VARIANTS:
        for sc in ("503", "429", "timeout"):
            p = RESULTS / f"e13_{variant}_{sc}.csv"
            if not p.exists():
                continue
            rows = list(csv.DictReader(open(p, encoding="utf-8")))
            during = [r for r in rows if NORMAL_S <= float(r["t_send_s"]) < NORMAL_S + FAULT_S]
            outside = [r for r in rows if r not in during]
            phi = [r for r in during if r["kind"] == "PHI"]
            non = [r for r in during if r["kind"] == "NON"]
            phi_fb = sum(int(r["fallback_hits"]) > 0 for r in phi)
            lo, hi = wilson(phi_fb, len(phi))
            ok_lat = [float(r["latency_ms"]) for r in during if r["status"] == "200"]
            fail = [r for r in during if r["status"] != "200"]
            att = [int(r["primary_attempts"]) for r in during]
            exposing = sum(r["body_exposes_internals"] == "True" for r in fail)
            out.append({
                "variant": variant, "fault": sc, "requests_during_fault": len(during),
                "phi_requests_reaching_non_baa_fallback": f"{phi_fb}/{len(phi)} = {100 * phi_fb / len(phi):.1f}% "
                                                          f"[{100 * lo:.1f}, {100 * hi:.1f}]",
                "phi_served_200_by_fallback": sum(r["status"] == "200" and int(r["fallback_hits"]) > 0 for r in phi),
                "non_phi_served_by_fallback": f"{sum(r['status'] == '200' and int(r['fallback_hits']) > 0 for r in non)}"
                                              f"/{len(non)}",
                "phi_reaching_fallback_outside_fault": sum(r["kind"] == "PHI" and int(r["fallback_hits"]) > 0
                                                           for r in outside),
                "failed_phi": sum(r["status"] != "200" for r in phi), "failed_non_phi": sum(r["status"] != "200" for r in non),
                "latency_ok_median_ms": round(summarize(ok_lat)["median"], 1) if ok_lat else "",
                "latency_ok_p95_ms": round(summarize(ok_lat)["p95"], 1) if ok_lat else "",
                "latency_failed_median_ms": round(summarize([float(r["latency_ms"]) for r in fail])["median"], 1)
                if fail else "",
                "primary_attempts_per_request_mean": round(sum(att) / len(att), 2) if att else "",
                "primary_attempts_max": max(att) if att else "",
                "requests_with_primary_retries": sum(a > 1 for a in att),
                "error_statuses": "; ".join(sorted({f"{r['status']} {r['error_code']}".strip() for r in fail})),
                "error_bodies_exposing_internals": f"{exposing}/{len(fail)}" if fail else "0/0",
                "example_error_body": fail[0]["error_body"][:300] if fail else "",
            })
    write_csv("e13_summary.csv", out)
    for o in out:
        print(json.dumps(o))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "smoke":
        asyncio.run(smoke())
    elif cmd == "run":
        asyncio.run(run(sys.argv[2], sys.argv[3]))
    else:
        analyse()
