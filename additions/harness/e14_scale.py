"""E14: scale-out, soak and a real-provider spot check on the fix build (3c468b0).

Worker setting: the gateway runs LiteLLM's proxy, whose worker count is the CLI flag ``--num_workers`` (environment
NUM_WORKERS, overridden by an explicit flag). The shipped image sets ``--num_workers 1`` in its Dockerfile CMD; the
local compose file does the same. ``stack/compose.e14.yaml`` differs from ``compose.add.yaml`` only in taking that value
from GW_WORKERS. The PHI detector keeps its shipped local setting (2 gunicorn workers) and the tenant policy its
``pseudonymization.max_concurrency: 2``.

A  scale-out (mock, fixed 200 ms, $0): masking ON (team phi-app, route without a BAA); gateway workers 1, 2, 4;
   closed-loop concurrency 16, 32, 64, 128; 60 s per level.
B  soak (mock, fixed 200 ms, $0): masking ON, the best worker count from A, concurrency 32, 30 minutes.
C  real spot check: GPT-5 mini (reasoning_effort minimal, 100 output tokens) through the body recorder, masking ON
   (every real route has no BAA), the best worker count from A, concurrency 8, 5 minutes. Run under keyenv5.py.

Every request carries a unique MRN (8 digits, starting with 4) and a unique marker with no 8-digit run, so the E9
marker artefact cannot occur. Mix-up (the corrected E9 rule): a response that is not its own request's text and holds
an MRN or marker issued to another request of the same run. Gateway-added latency: client latency minus the
upstream's own time (mock: x-mock-service-ms passed on by the gateway; real: the recorder's upstream_ms).

Host:
  python e14_scale.py up mock|real WORKERS     (re)create the stack with WORKERS gateway processes; seed the tenant
  python e14_scale.py a                        A for workers 1, 2, 4 (with resource sampling)
  python e14_scale.py b WORKERS                B
  python keyenv5.py run -- python e14_scale.py c WORKERS      C (after `up real WORKERS`, also under keyenv5.py)
  python e14_scale.py analyse [best]           summaries
  python e14_scale.py down mock|real
Inside the tenant network (started by the host commands): load-a W | load-b W | load-c W; sample OUT STOP PROJECT
"""

from __future__ import annotations

import asyncio
import collections
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER_COUNTS = (1, 2, 4)
LEVELS_A = (16, 32, 64, 128)
DURATION_A = float(os.environ.get("E14_DURATION_A", "60"))   # override only for smoke tests
SOAK_C, SOAK_S, WINDOW_S = 32, 1800.0, 300.0
REAL_C, REAL_S, REAL_TOKENS = 8, float(os.environ.get("E14_REAL_S", "300")), 100   # override only for smoke tests
REAL_PRICE = (2.5e-07, 2.0e-06)          # GPT-5 mini, USD per input / output token (route price, as E7)
REAL_CAP_USD = float(os.environ.get("E14_CAP_USD", "4.5"))   # brief's cap is $5; stop issuing before it
NAMES = ["Maria Alvarez", "James Brennan", "Aisha Okafor", "Daniel Lindqvist", "Elena Moreau", "Samuel Castillo"]
MRN_RE = re.compile(r"\b4\d{7}\b")
MARK_RE = re.compile(r"REQ-e14[a-z]-w\d+-c\d+-k\d+x\d+")
SYSTEM_C = ("Rewrite or summarize the user's note for the care team in at most two sentences. Keep every name, date, "
            "phone number, record number and reference code exactly as written.")
MRN_BASE = {"a": {1: 41_000_000, 2: 42_000_000, 4: 43_000_000}, "b": 45_000_000, "c": 49_000_000}

KIND = {"mock": "e14", "real": "e14real"}


def register() -> None:
    """Add the E14 tenants to common5 and point its compose helper at compose.e14.yaml."""
    import common5

    common5.TENANTS["e14"] = {"project": "itpro-add-e14", "env": "e14.env", "gw": 4711, "mock": 4713,
                              "master": os.environ.get("ITPRO_MASTER_E14", "")}
    common5.TENANTS["e14real"] = {"project": "itpro-add-e14real", "env": "e14-real.env", "gw": 4721, "mock": 4723,
                                  "master": os.environ.get("ITPRO_MASTER_E14REAL", "")}
    common5.TEAM_BUDGET["real-app"] = 5.0          # gateway-side safety net at the brief's $5 cap

    def compose_args(tenant: str) -> list[str]:
        t = common5.TENANTS[tenant]
        return ["docker", "compose", "-p", t["project"], "--env-file", str(common5.STACK / t["env"]),
                "-f", str(common5.STACK / "compose.e14.yaml")]

    common5.compose_args = compose_args


# ---------------------------------------------------------------- inside the tenant network
def prompt(phase: str, workers: int, level: int, k: int, n: int, seq: int, base: int) -> tuple[str, str, str]:
    mrn = str(base + seq)
    marker = f"REQ-e14{phase}-w{workers}-c{level}-k{k}x{n}"
    text = (f"Patient {NAMES[seq % len(NAMES)]} (MRN: {mrn}, DOB 03/{1 + seq % 28:02d}/19{40 + seq % 59}) called "
            f"(617) 555-01{seq % 100:02d} about a refill. Please rewrite briefly. {marker}")
    return text, mrn, marker


def foreign(final: str, mrn: str, marker: str, mrns: set, markers: set) -> int:
    return len((set(MRN_RE.findall(final)) & mrns) - {mrn}) + len((set(MARK_RE.findall(final)) & markers) - {marker})


def _write(name: str, rows: list[dict]) -> None:
    from common5 import write_csv
    write_csv(name, rows)


async def mock_load(phase: str, workers: int, levels: tuple, duration: float) -> None:
    import httpx

    key = json.loads(os.environ["ITPRO_STATE_E14"])["keys"]["phi-app"]
    url = "http://gateway:4000/v1/chat/completions"
    base = MRN_BASE["a"][workers] if phase == "a" else MRN_BASE["b"]
    seq = [0]
    mrns: set = set()
    markers: set = set()
    rows, lv_rows = [], []
    for level in levels:
        async with httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=level + 8,
                                                                      max_keepalive_connections=level + 8)) as c:
            t_start = time.time()
            deadline = time.perf_counter() + duration

            async def worker(k: int) -> None:
                n = 0
                while time.perf_counter() < deadline:
                    seq[0] += 1
                    text, mrn, marker = prompt(phase, workers, level, k, n, seq[0], base)
                    mrns.add(mrn)
                    markers.add(marker)
                    t0 = time.perf_counter()
                    code = ""
                    try:
                        r = await c.post(url, headers={"Authorization": f"Bearer {key}"},
                                         json={"model": "phi", "max_tokens": 200,
                                               "messages": [{"role": "user", "content": text}]})
                        status = r.status_code
                        if status == 200:
                            final = r.json()["choices"][0]["message"]["content"] or ""
                        else:
                            final = ""
                            try:
                                code = str((r.json().get("error") or {}).get("code") or "")
                            except ValueError:
                                code = "non-json"
                        svc = r.headers.get("llm_provider-x-mock-service-ms", "")
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        status, final, svc, code = 0, "", "", type(exc).__name__
                    ms = (time.perf_counter() - t0) * 1000
                    rows.append({"level": level, "t_done": round(time.time(), 3), "status": status, "error_code": code,
                                 "latency_ms": round(ms, 2), "mock_service_ms": svc,
                                 "exact": int(status == 200 and final == text),
                                 "foreign": foreign(final, mrn, marker, mrns, markers)})
                    n += 1

            await asyncio.gather(*(worker(k) for k in range(level)))
            lv_rows.append({"phase": phase, "workers": workers, "level": level, "t_start": round(t_start, 3),
                            "t_end": round(time.time(), 3)})
        if phase == "a":
            await asyncio.sleep(5)          # let the gateway drain between levels
    tag = f"a_w{workers}" if phase == "a" else "b"
    _write(f"e14{tag}_requests.csv", rows)
    _write(f"e14{tag}_levels.csv", lv_rows)
    print(f"E14 {phase} workers={workers}: {len(rows)} requests over {len(lv_rows)} level(s)")


def _sent_user(req: dict) -> str:
    users = [m for m in (req or {}).get("messages", []) if m.get("role") == "user"]
    c = users[-1].get("content") if users else ""
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


async def real_load(workers: int) -> None:
    import httpx

    from scoring import residual_placeholders

    key = json.loads(os.environ["ITPRO_STATE_E14REAL"])["keys"]["real-app"]
    url = "http://gateway:4000/v1/chat/completions"
    rec_url = "http://recorder-openai:8080/_log"
    seq = [0]
    spent = [0.0]
    mrns: set = set()
    markers: set = set()
    rows: list[dict] = []
    finals: dict[str, dict] = {}            # marker -> planted values (kept in memory only, never written)
    rec: list[dict] = []
    async with httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=REAL_C + 4)) as c:
        since = max([e["seq"] for e in (await c.get(rec_url)).json()] or [0])
        start_seq = since
        t_start = time.time()
        deadline = time.perf_counter() + REAL_S
        stop = [False]

        async def poll() -> None:          # the recorder keeps the last 5,000 exchanges: pull them as we go
            nonlocal since
            while not stop[0]:
                await asyncio.sleep(20)
                new = (await c.get(rec_url, params={"since": since})).json()
                if new:
                    since = new[-1]["seq"]
                    rec.extend(new)

        async def worker(k: int) -> None:
            n = 0
            while time.perf_counter() < deadline and spent[0] < REAL_CAP_USD:
                seq[0] += 1
                text, mrn, marker = prompt("c", workers, REAL_C, k, n, seq[0], MRN_BASE["c"])
                mrns.add(mrn)
                markers.add(marker)
                planted = re.match(r"Patient (.+?) \(MRN: (\d+), DOB (\S+)\) called (\(\d+\) [\d-]+)", text).groups()
                body = {"model": "real-mini", "max_completion_tokens": REAL_TOKENS, "reasoning_effort": "minimal",
                        "messages": [{"role": "system", "content": SYSTEM_C}, {"role": "user", "content": text}]}
                t0 = time.perf_counter()
                code, final, rid, finish, usage = "", "", "", "", {}
                try:
                    r = await c.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
                    status = r.status_code
                    j = r.json()
                    if status == 200:
                        final = j["choices"][0]["message"].get("content") or ""
                        finish, usage, rid = j["choices"][0].get("finish_reason", ""), j.get("usage") or {}, j.get("id", "")
                    else:
                        code = str((j.get("error") or {}).get("code") or "")
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    status, code = 0, type(exc).__name__
                client_ms = (time.perf_counter() - t0) * 1000
                pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                spent[0] += pt * REAL_PRICE[0] + ct * REAL_PRICE[1]
                finals[marker] = {"planted": planted, "mrn": mrn}
                rows.append({"marker": marker, "t_done": round(time.time(), 3), "status": status, "error_code": code,
                             "client_ms": round(client_ms, 1), "response_id": rid, "finish_reason": finish,
                             "prompt_tokens": pt, "completion_tokens": ct,
                             "cost_usd": round(pt * REAL_PRICE[0] + ct * REAL_PRICE[1], 8),
                             "exact_restore": int(status == 200 and all(v in final for v in planted)
                                                  and not residual_placeholders(final)),
                             "marker_in_final": int(marker in final),
                             "residual_placeholders": len(residual_placeholders(final)),
                             "foreign": foreign(final, mrn, marker, mrns, markers)})
                n += 1

        poller = asyncio.create_task(poll())
        await asyncio.gather(*(worker(k) for k in range(REAL_C)))
        t_end = time.time()
        stop[0] = True
        await poller
        new = (await c.get(rec_url, params={"since": since})).json()
        rec.extend(new)
    # join with the recorder: by provider response id, else by the marker in the text the provider received
    by_id = {(e.get("response") or {}).get("id"): e for e in rec if isinstance(e.get("response"), dict)}
    by_marker = {}
    for e in rec:
        m = MARK_RE.search(_sent_user(e.get("request")))
        if m:
            by_marker.setdefault(m.group(0), []).append(e)
    for r in rows:
        e = by_id.get(r["response_id"]) if r["response_id"] else None
        if e is None and by_marker.get(r["marker"]):
            e = [x for x in by_marker[r["marker"]] if x["status"] == 200][-1:] or by_marker[r["marker"]][-1:]
            e = e[0] if e else None
        tries = by_marker.get(r["marker"], [])
        r["upstream_ms"] = e["upstream_ms"] if e else ""
        r["gateway_added_ms"] = round(r["client_ms"] - e["upstream_ms"], 1) if e and r["status"] == 200 else ""
        r["recorder_match"] = "id" if e is not None and r["response_id"] and by_id.get(r["response_id"]) else \
            ("marker" if e is not None else "none")
        r["provider_attempts_seen"] = len(tries)
        r["provider_429s_seen"] = sum(x["status"] == 429 for x in tries)
        sent = _sent_user(e.get("request")) if e else ""
        p = finals[r["marker"]]["planted"]
        r["values_in_clear_at_provider"] = sum(v in sent for v in p) if e else ""
        r["marker_masked_at_provider"] = int(bool(e) and r["marker"] not in sent)
    statuses = collections.Counter(e["status"] for e in rec)
    meta = {"t_start": round(t_start, 3), "t_end": round(t_end, 3), "workers": workers, "concurrency": REAL_C,
            "recorder_exchanges": len(rec), "recorder_statuses": json.dumps({str(k): v for k, v in sorted(statuses.items())}), "recorder_first_seq": start_seq + 1,
            "client_spend_estimate_usd": round(spent[0], 6), "stopped_by_cap": spent[0] >= REAL_CAP_USD}
    for r in rows:
        r.pop("response_id")
    _write("e14c_requests.csv", rows)
    _write("e14c_run.csv", [meta])
    print(f"E14 c workers={workers}: {len(rows)} requests; recorder saw {len(rec)} exchanges {dict(statuses)}; "
          f"client spend estimate {spent[0]:.4f} USD")


# ---------------------------------------------------------------- host
def env_with(workers: int) -> None:
    os.environ["GW_WORKERS"] = str(workers)


def sample(out: str, stop: str, project: str) -> None:
    roles = {f"{project}-gateway-1": "gateway", f"{project}-presidio-analyzer-1": "presidio",
             f"{project}-mock-upstream-1": "mock", f"{project}-loadgen": "loadgen"}
    with open(out, "a", encoding="utf-8") as fh:
        while not os.path.exists(stop):
            t = time.time()
            res = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}"],
                                 capture_output=True, text=True)
            row: dict = {"t": round(t, 3)}
            for line in res.stdout.splitlines():
                name, cpu, mem = (line.split(";") + ["", "", ""])[:3]
                if name in roles:
                    row[roles[name]] = {"cpu": cpu, "mem": mem}
            fh.write(json.dumps(row) + "\n")
            fh.flush()


def up(kind: str, workers: int) -> None:
    import httpx

    import common5
    register()
    env_with(workers)
    tenant = KIND[kind]
    common5.compose(tenant, "up", "-d", "--wait")
    t = common5.TENANTS[tenant]
    common5.wait_healthy(f"http://127.0.0.1:{t['gw']}/health/liveliness")
    ok = False
    try:
        state = common5.load_state(tenant)
        team_key = state["keys"]["phi-app" if kind == "mock" else "real-app"]
        ok = httpx.get(f"http://127.0.0.1:{t['gw']}/v1/models", timeout=30,
                       headers={"Authorization": f"Bearer {team_key}"}).status_code == 200
    except (OSError, KeyError, ValueError, httpx.HTTPError):
        ok = False
    if not ok:
        common5.setup(tenant, "policy-a" if kind == "mock" else "policy-real5")
        print(f"{tenant}: tenant seeded")
    probe = ("import os;c=[open(f'/proc/{p}/cmdline','rb').read().replace(bytes([0]),b' ').decode() for p in os.listdir('/proc')"
             " if p.isdigit() and os.path.exists(f'/proc/{p}/cmdline')];"
             "print(sum('bin/litellm' in x for x in c), sum('spawn_main(' in x and 'listdir' not in x for x in c))")
    procs = common5.compose(tenant, "exec", "-T", "gateway", "python", "-c", probe, capture=True, check=False).stdout.split()
    print(f"{tenant}: up with GW_WORKERS={workers}; litellm processes {procs[:1]}, spawned workers {procs[1:]}")


def harness_run(kind: str, *args: str) -> None:
    import common5
    register()
    tenant = KIND[kind]
    state_var = "ITPRO_STATE_" + tenant.upper()
    os.environ[state_var] = json.dumps(common5.load_state(tenant))
    project = common5.TENANTS[tenant]["project"]
    common5.compose(tenant, "--profile", "tools", "run", "--rm", "--no-deps", "--name", f"{project}-loadgen",
                    "-e", state_var, *(["-e", "E14_REAL_S"] if os.environ.get("E14_REAL_S") else []), "harness", "e14_scale.py", *args)


def sampled(kind: str, samples: str, *args: str) -> None:
    import common5
    register()
    project = common5.TENANTS[KIND[kind]]["project"]
    stop = str(common5.STATE_DIR / f"{project}.stop")
    for f in (stop, samples):
        if os.path.exists(f):
            os.remove(f)
    p = subprocess.Popen([sys.executable, str(HERE / "e14_scale.py"), "sample", samples, stop, project])
    try:
        harness_run(kind, *args)
    finally:
        Path(stop).write_text("stop")
        p.wait()


def host_a() -> None:
    from common5 import RESULTS
    for w in WORKER_COUNTS:
        up("mock", w)
        sampled("mock", str(RESULTS / f"e14a_w{w}_samples.jsonl"), "load-a", str(w))


def down(kind: str) -> None:
    import common5
    register()
    env_with(1)
    common5.compose(KIND[kind], "--profile", "tools", "--profile", "presidio", "--profile", "real", "down", "-v",
                    "--remove-orphans")


def team_spend() -> None:
    import common5
    register()
    state = common5.load_state("e14real")
    r = common5.admin("e14real", f"/team/info?team_id={state['teams']['real-app']}", method="GET")
    info = r.json().get("team_info") or r.json()
    out = {"team": "real-app", "gateway_team_spend_usd": info.get("spend"), "max_budget_usd": info.get("max_budget")}
    (common5.RESULTS / "e14c_spend.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))


# ---------------------------------------------------------------- analysis
def _cpu(v: str) -> float:
    return float(v.strip().rstrip("%") or 0)


def _mem_mib(v: str) -> float:
    used = v.split("/")[0].strip()
    num = re.match(r"[\d.]+", used).group(0)
    unit = used[len(num):].strip().lower()
    return float(num) * {"b": 1 / 1048576, "kib": 1 / 1024, "kb": 1 / 1024, "mib": 1, "mb": 1, "gib": 1024,
                         "gb": 1024}.get(unit, 1)


def _stat(win: list[dict], role: str, f) -> tuple:
    vals = [f(s[role]) for s in win if role in s]
    return (round(sum(vals) / len(vals), 1), round(max(vals), 1)) if vals else ("", "")


def _rate(k: int, n: int) -> str:
    from stats import wilson
    lo, hi = wilson(k, n) if n else (0, 0)
    return f"{k}/{n} = {100 * k / n:.2f}% [{100 * lo:.2f}, {100 * hi:.2f}]" if n else "n/a"


def _rows(name: str) -> list[dict]:
    from common5 import RESULTS
    return list(csv.DictReader(open(RESULTS / name, encoding="utf-8")))


def _samples(name: str) -> list[dict]:
    from common5 import RESULTS
    return [json.loads(x) for x in open(RESULTS / name, encoding="utf-8") if x.strip()]


def _block(rs: list[dict], t0: float, t1: float, win: list[dict]) -> dict:
    from stats import percentile
    ok = [r for r in rs if r["status"] == "200"]
    added = [float(r["latency_ms"]) - (float(r["mock_service_ms"]) if r["mock_service_ms"] else 200.0) for r in ok]
    errs = len(rs) - len(ok)
    out = {"requests": len(rs), "throughput_rps": round(len(ok) / (t1 - t0), 1),
           "added_p50_ms": round(percentile(added, 50), 1) if added else "",
           "added_p95_ms": round(percentile(added, 95), 1) if added else "",
           "added_p99_ms": round(percentile(added, 99), 1) if added else "",
           "errors": _rate(errs, len(rs)),
           "error_statuses": json.dumps(dict(collections.Counter(f"{r['status']} {r['error_code']}".strip()
                                                                 for r in rs if r["status"] != "200"))),
           "restored_exactly": f"{sum(r['exact'] == '1' for r in ok)}/{len(ok)}",
           "cross_request_mixups": sum(r["exact"] != "1" and int(r["foreign"]) > 0 for r in rs),
           "service_header_seen": f"{sum(bool(r['mock_service_ms']) for r in ok)}/{len(ok)}"}
    for role in ("gateway", "presidio", "mock", "loadgen"):
        c, m = _stat(win, role, lambda s: _cpu(s["cpu"])), _stat(win, role, lambda s: _mem_mib(s["mem"]))
        out[f"{role}_cpu_mean_max_pct"] = f"{c[0]} / {c[1]}"
        if role in ("gateway", "presidio"):
            out[f"{role}_mem_mean_max_mib"] = f"{m[0]} / {m[1]}"
    out["resource_samples"] = len(win)
    return out


def analyse(best: str | None) -> None:
    from common5 import RESULTS, write_csv
    from stats import percentile

    out_a = []
    for w in WORKER_COUNTS:
        if not (RESULTS / f"e14a_w{w}_requests.csv").exists():
            continue
        reqs, samples = _rows(f"e14a_w{w}_requests.csv"), _samples(f"e14a_w{w}_samples.jsonl")
        for lv in _rows(f"e14a_w{w}_levels.csv"):
            t0, t1, level = float(lv["t_start"]), float(lv["t_end"]), int(lv["level"])
            rs = [r for r in reqs if int(r["level"]) == level]
            out_a.append({"workers": w, "concurrency": level,
                          **_block(rs, t0, t1, [s for s in samples if t0 <= s["t"] <= t1])})
    if out_a:
        write_csv("e14a_summary.csv", out_a)
        for o in out_a:
            print(json.dumps(o))

    if (RESULTS / "e14b_requests.csv").exists():
        reqs, samples = _rows("e14b_requests.csv"), _samples("e14b_samples.jsonl")
        lv = _rows("e14b_levels.csv")[0]
        t0, t1 = float(lv["t_start"]), float(lv["t_end"])
        out_b = []
        k = 0
        while t0 + k * WINDOW_S < t1 - 1:
            a, b = t0 + k * WINDOW_S, min(t0 + (k + 1) * WINDOW_S, t1)
            rs = [r for r in reqs if a <= float(r["t_done"]) < b or (b == t1 and float(r["t_done"]) >= b)]
            out_b.append({"window": f"{int(k * WINDOW_S // 60)}-{int((b - t0) // 60)} min", "workers": lv["workers"],
                          **_block(rs, a, b, [s for s in samples if a <= s["t"] < b])})
            k += 1
        out_b.append({"window": "all", "workers": lv["workers"],
                      **_block(reqs, t0, t1, [s for s in samples if t0 <= s["t"] <= t1])})
        write_csv("e14b_windows.csv", out_b)
        mem = []
        for m in range(int((t1 - t0) // 60) + 1):
            a, b = t0 + 60 * m, min(t0 + 60 * (m + 1), t1)
            win = [s for s in samples if a <= s["t"] < b]
            if not win:
                continue
            g, p = _stat(win, "gateway", lambda s: _mem_mib(s["mem"])), _stat(win, "presidio", lambda s: _mem_mib(s["mem"]))
            mem.append({"minute": m, "gateway_mem_mean_mib": g[0], "gateway_mem_max_mib": g[1],
                        "presidio_mem_mean_mib": p[0], "presidio_mem_max_mib": p[1], "samples": len(win)})
        write_csv("e14b_memory_per_minute.csv", mem)

        def slope(key: str, rows: list[dict]) -> float:     # least squares, MiB per hour
            xs = [r["minute"] for r in rows if r[key] != ""]
            ys = [r[key] for r in rows if r[key] != ""]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            return round(60 * sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs), 1)
        steady = [r for r in mem if r["minute"] >= 5]
        trend = {"gateway_slope_all_mib_per_h": slope("gateway_mem_mean_mib", mem),
                 "gateway_slope_after_5min_mib_per_h": slope("gateway_mem_mean_mib", steady),
                 "presidio_slope_all_mib_per_h": slope("presidio_mem_mean_mib", mem),
                 "presidio_slope_after_5min_mib_per_h": slope("presidio_mem_mean_mib", steady),
                 "gateway_first_last_minute_mib": f"{mem[0]['gateway_mem_mean_mib']} -> {mem[-1]['gateway_mem_mean_mib']}",
                 "presidio_first_last_minute_mib": f"{mem[0]['presidio_mem_mean_mib']} -> {mem[-1]['presidio_mem_mean_mib']}"}
        write_csv("e14b_memory_trend.csv", [trend])
        for o in out_b + [trend]:
            print(json.dumps(o))

    if (RESULTS / "e14c_requests.csv").exists():
        rs, meta = _rows("e14c_requests.csv"), _rows("e14c_run.csv")[0]
        t0, t1 = float(meta["t_start"]), float(meta["t_end"])
        ok = [r for r in rs if r["status"] == "200"]
        added = [float(r["gateway_added_ms"]) for r in ok if r["gateway_added_ms"]]
        client = [float(r["client_ms"]) for r in ok]
        prov429 = [r for r in rs if r["status"] == "429" and r["error_code"] in ("upstream_rate_limited", "429")]
        other_err = [r for r in rs if r["status"] != "200" and r not in prov429]
        recst = {str(k): v for k, v in __import__("ast").literal_eval(meta["recorder_statuses"]).items()}
        spend = json.loads((RESULTS / "e14c_spend.json").read_text()) if (RESULTS / "e14c_spend.json").exists() else {}
        out_c = {"workers": meta["workers"], "concurrency": meta["concurrency"], "requests": len(rs),
                 "throughput_rps": round(len(ok) / (t1 - t0), 2),
                 "gateway_added_p50_ms": round(percentile(added, 50), 1) if added else "",
                 "gateway_added_p95_ms": round(percentile(added, 95), 1) if added else "",
                 "gateway_added_p99_ms": round(percentile(added, 99), 1) if added else "",
                 "client_p50_ms": round(percentile(client, 50), 1) if client else "",
                 "client_p95_ms": round(percentile(client, 95), 1) if client else "",
                 "joined_to_recorder": f"{sum(r['recorder_match'] != 'none' for r in ok)}/{len(ok)} "
                                       f"(id {sum(r['recorder_match'] == 'id' for r in ok)}, "
                                       f"marker {sum(r['recorder_match'] == 'marker' for r in ok)})",
                 "provider_429_at_recorder": recst.get("429", 0),
                 "recorder_statuses": json.dumps(recst),
                 "client_429_upstream_rate_limited": len(prov429),
                 "gateway_errors_other": _rate(len(other_err), len(rs)),
                 "other_error_statuses": json.dumps(dict(collections.Counter(f"{r['status']} {r['error_code']}"
                                                                             for r in other_err))),
                 "exact_restore": _rate(sum(r["exact_restore"] == "1" for r in ok), len(ok)),
                 "residual_placeholders_in_answers": sum(int(r["residual_placeholders"]) for r in ok),
                 "cross_request_mixups": sum(int(r["foreign"]) > 0 for r in rs),
                 "planted_values_in_clear_at_provider": f"{sum(int(r['values_in_clear_at_provider'] or 0) for r in ok)}"
                                                        f"/{4 * sum(r['recorder_match'] != 'none' for r in ok)}",
                 "finish_length": sum(r["finish_reason"] == "length" for r in ok),
                 "tokens_in_out": f"{sum(int(r['prompt_tokens']) for r in rs)} / {sum(int(r['completion_tokens']) for r in rs)}",
                 "spend_usd_tokens_x_price": round(sum(float(r["cost_usd"]) for r in rs), 6),
                 "spend_usd_gateway_team": spend.get("gateway_team_spend_usd", ""),
                 "stopped_by_cap": meta["stopped_by_cap"]}
        write_csv("e14c_summary.csv", [out_c])
        print(json.dumps(out_c))
    if best:
        print("best worker count:", best)


def main() -> None:
    sys.path.insert(0, str(HERE))
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "load-a":
        asyncio.run(mock_load("a", int(args[0]), LEVELS_A, DURATION_A))
    elif cmd == "load-b":
        asyncio.run(mock_load("b", int(args[0]), (SOAK_C,), SOAK_S))
    elif cmd == "load-c":
        asyncio.run(real_load(int(args[0])))
    elif cmd == "sample":
        sample(*args)
    elif cmd == "up":
        up(args[0], int(args[1]))
    elif cmd == "a":
        host_a()
    elif cmd == "b":
        from common5 import RESULTS
        up("mock", int(args[0]))
        sampled("mock", str(RESULTS / "e14b_samples.jsonl"), "load-b", args[0])
    elif cmd == "c":
        up("real", int(args[0]))
        harness_run("real", "load-c", args[0])
        team_spend()
    elif cmd == "analyse":
        analyse(args[0] if args else None)
    elif cmd == "down":
        down(args[0])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
