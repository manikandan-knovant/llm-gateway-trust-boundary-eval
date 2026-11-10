"""E9: load on the fix build (mock upstream with a fixed 200 ms latency, $0).

Concurrency 1, 2, 4, 8, 16, 32, 64, 128 (closed loop: N workers, each sends its next request when the last one
returns); 60 s per level; masking ON (team phi-app, route without BAA: PHI/PII scan, mask and restore on every call)
and masking OFF (team bench-relaxed on a BAA-only route: no scan). The gateway runs as shipped (one worker process).

Every request carries unique synthetic values (an MRN unique across the whole run) and a marker. Correctness: the
response must restore exactly its own request's text; a response that contains another request's MRN or marker is a
cross-request mix-up. Gateway-added latency = client latency - the mock's own service time (x-mock-service-ms, if the
gateway passes the provider header on; otherwise the nominal 200 ms).

  python e9_load.py run on|off       (inside the load tenant's network; writes per-request and per-level CSVs)
  python e9_load.py analyse SAMPLES  (host; SAMPLES = docker stats samples, JSON lines)
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
import time

LEVELS = (1, 2, 4, 8, 16, 32, 64, 128)
DURATION_S = 60.0
NAMES = ["Maria Alvarez", "James Brennan", "Aisha Okafor", "Daniel Lindqvist", "Elena Moreau", "Samuel Castillo"]
MRN_RE = re.compile(r"\b\d{8}\b")
MARK_RE = re.compile(r"REQ-[a-z]+-\d+-\d+")


def prompt(mode: str, level: int, n: int, seq: int) -> tuple[str, str]:
    mrn = str(30_000_000 + seq)                                # unique across the run (8 digits)
    text = (f"Patient {NAMES[seq % len(NAMES)]} (MRN: {mrn}, DOB 03/{1 + seq % 28:02d}/19{40 + seq % 59}) called "
            f"(617) 555-01{seq % 100:02d} about a refill. Please rewrite briefly. REQ-{mode}-{level}-{n}")
    return text, mrn


async def run_mode(mode: str) -> None:
    import httpx

    from common5 import RESULTS

    state = json.loads(os.environ["ITPRO_STATE_LOAD"])
    key = state["keys"]["phi-app" if mode == "on" else "bench-relaxed"]
    model = "phi" if mode == "on" else "bench"
    url = "http://gateway:4000/v1/chat/completions"
    seq = [0 if mode == "on" else 5_000_000]
    rows, levels = [], []
    for level in LEVELS:
        async with httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=level + 8,
                                                                      max_keepalive_connections=level + 8)) as c:
            mrns: dict[str, str] = {}
            t_start = time.time()
            deadline = time.perf_counter() + DURATION_S

            async def worker(w: int) -> None:
                n = 0
                while time.perf_counter() < deadline:
                    seq[0] += 1
                    text, mrn = prompt(mode, level, w * 1_000_000 + n, seq[0])
                    marker = MARK_RE.search(text).group(0)
                    mrns[marker] = mrn
                    t0 = time.perf_counter()
                    try:
                        r = await c.post(url, headers={"Authorization": f"Bearer {key}"},
                                         json={"model": model, "max_tokens": 200,
                                               "messages": [{"role": "user", "content": text}]})
                        status = r.status_code
                        final = r.json()["choices"][0]["message"]["content"] if status == 200 else ""
                        svc = r.headers.get("llm_provider-x-mock-service-ms", "")
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        status, final, svc = 0, "", ""
                    ms = (time.perf_counter() - t0) * 1000
                    foreign_mrn = [m for m in MRN_RE.findall(final) if m != mrn]
                    foreign_marker = [m for m in MARK_RE.findall(final) if m != marker]
                    rows.append({"mode": mode, "level": level, "worker": w, "t_done": round(time.time(), 3),
                                 "status": status, "latency_ms": round(ms, 2), "mock_service_ms": svc,
                                 "restored_exactly": status == 200 and final == text,
                                 "foreign_values": len(foreign_mrn) + len(foreign_marker)})
                    n += 1

            await asyncio.gather(*(worker(w) for w in range(level)))
            levels.append({"mode": mode, "level": level, "t_start": round(t_start, 3), "t_end": round(time.time(), 3)})
        await asyncio.sleep(5)    # let the gateway drain between levels
    for name, data in ((f"e9_requests_{mode}.csv", rows), (f"e9_levels_{mode}.csv", levels)):
        with open(RESULTS / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    print(f"E9 {mode}: {len(rows)} requests over {len(levels)} levels")


def _cpu(v: str) -> float:
    return float(v.strip().rstrip("%") or 0)


def _mem_mib(v: str) -> float:
    used = v.split("/")[0].strip()
    num = float(re.match(r"[\d.]+", used).group(0))
    unit = used[len(re.match(r"[\d.]+", used).group(0)):].strip().lower()
    return num * {"b": 1 / 1048576, "kib": 1 / 1024, "kb": 1 / 1024, "mib": 1, "mb": 1, "gib": 1024, "gb": 1024}.get(unit, 1)


def analyse(samples_path: str) -> None:
    from common5 import RESULTS, write_csv
    from stats import percentile, wilson

    samples = [json.loads(line) for line in open(samples_path, encoding="utf-8") if line.strip()]
    out = []
    for mode in ("off", "on"):
        reqs = list(csv.DictReader(open(RESULTS / f"e9_requests_{mode}.csv", encoding="utf-8")))
        for lv in csv.DictReader(open(RESULTS / f"e9_levels_{mode}.csv", encoding="utf-8")):
            level, t0, t1 = int(lv["level"]), float(lv["t_start"]), float(lv["t_end"])
            rs = [r for r in reqs if int(r["level"]) == level]
            ok = [r for r in rs if r["status"] == "200"]
            added = [float(r["latency_ms"]) - (float(r["mock_service_ms"]) if r["mock_service_ms"] else 200.0)
                     for r in ok]
            errs = len(rs) - len(ok)
            lo, hi = wilson(errs, len(rs)) if rs else (0, 0)
            exact = sum(r["restored_exactly"] == "True" for r in ok)
            win = [s for s in samples if t0 <= s["t"] <= t1]
            def stat(container, f):
                vals = [f(s[container]) for s in win if container in s]
                return (round(sum(vals) / len(vals), 1), round(max(vals), 1)) if vals else ("", "")
            g_cpu, g_mem = stat("gateway", lambda s: _cpu(s["cpu"])), stat("gateway", lambda s: _mem_mib(s["mem"]))
            p_cpu = stat("presidio", lambda s: _cpu(s["cpu"]))
            out.append({"masking": mode, "concurrency": level, "requests": len(rs),
                        "throughput_rps": round(len(ok) / (t1 - t0), 1),
                        "added_p50_ms": round(percentile(added, 50), 1) if added else "",
                        "added_p95_ms": round(percentile(added, 95), 1) if added else "",
                        "added_p99_ms": round(percentile(added, 99), 1) if added else "",
                        "error_rate": f"{errs}/{len(rs)} = {100 * errs / len(rs):.2f}% [{100 * lo:.2f}, {100 * hi:.2f}]"
                        if rs else "",
                        "error_statuses": json.dumps(dict(sorted(
                            __import__("collections").Counter(r["status"] for r in rs if r["status"] != "200").items()))),
                        "restored_exactly": f"{exact}/{len(ok)}",
                        # a mix-up needs a response that is NOT its own request's text and carries a foreign value;
                        # (the raw foreign_values count also matched the 8-digit worker number inside each request's
                        # own marker at 16+ workers, so it is not used on its own)
                        "cross_request_mixups": sum(r["restored_exactly"] != "True" and int(r["foreign_values"]) > 0
                                                    for r in rs),
                        "raw_foreign_number_matches_incl_own_marker": sum(int(r["foreign_values"]) > 0 for r in rs),
                        "provider_service_header_seen": sum(bool(r["mock_service_ms"]) for r in ok),
                        "gateway_cpu_mean_max_pct": f"{g_cpu[0]} / {g_cpu[1]}",
                        "gateway_mem_mean_max_mib": f"{g_mem[0]} / {g_mem[1]}",
                        "presidio_cpu_mean_max_pct": f"{p_cpu[0]} / {p_cpu[1]}", "cpu_samples": len(win)})
    write_csv("e9_summary.csv", out)
    for o in out:
        print(json.dumps(o))


if __name__ == "__main__":
    asyncio.run(run_mode(sys.argv[2])) if sys.argv[1] == "run" else analyse(sys.argv[2])
