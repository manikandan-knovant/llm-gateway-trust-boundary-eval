"""E4: PHI/PII detection, masking and restore against the mock upstream (baseline build 2026-11-09).

500 synthetic prompts (seed 2026) through the "phi" logical model (route without BAA: masking mandatory).
The mock echoes the user message it RECEIVED, so the gateway's masking is observed exactly (GET /_last), and the
answer the client gets back is the gateway's restore of that echo.

Restore variants (the mock rewrites placeholders before answering):
  exact <<T_n>> | single <T_n> | brackets [T_n] | bare T_n | stream_split (exact, streamed 3 characters per chunk)

  python e4_phi.py run a        tenant A (Presidio + regex): detection + 5 restore variants
  python e4_phi.py run b        tenant B (regex only): detection only (exact variant)
  python e4_phi.py analyse
"""

from __future__ import annotations

import collections
import csv
import json
import sys

import httpx

from common import RESULTS, TENANTS, load_state, write_csv
from scoring import residual_placeholders, restore_ok, score_entities
from stats import fmt_pct_ci, wilson
from synthetic_prompts import generate

SEED, N = 2026, 500
VARIANTS = ("exact", "single", "brackets", "bare", "stream_split")


def _final_text(resp: httpx.Response, stream: bool) -> str:
    if not stream:
        return resp.json()["choices"][0]["message"]["content"]
    out = []
    for line in resp.text.splitlines():
        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
            continue
        obj = json.loads(line[6:])
        for ch in obj.get("choices") or []:
            out.append((ch.get("delta") or {}).get("content") or "")
    return "".join(out)


def run(tenant: str) -> None:
    t, state = TENANTS[tenant], load_state(tenant)
    gw, mock = f"http://127.0.0.1:{t['gw']}", f"http://127.0.0.1:{t['mock']}"
    key = state["keys"]["phi-app"]
    prompts = generate(N, SEED)
    det_rows, over_rows, rest_rows = [], [], []
    variants = VARIANTS if tenant == "a" else ("exact",)
    with httpx.Client(timeout=60) as c:
        for variant in variants:
            c.post(f"{mock}/_variant", json={"variant": variant}).raise_for_status()
            stream = variant == "stream_split"
            for p in prompts:
                body = {"model": "phi", "messages": [{"role": "user", "content": p.text}], "max_tokens": 200}
                if stream:
                    body["stream"] = True
                r = c.post(f"{gw}/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
                received = c.get(f"{mock}/_last", params={"target": "phi"}).json()
                received_user = [m for m in received.get("messages", []) if m.get("role") == "user"][-1]["content"]
                final = _final_text(r, stream) if r.status_code == 200 else ""
                if variant == "exact":
                    scores, over, aligned = score_entities(p.text, received_user, p.entities)
                    for e, s in zip(p.entities, scores):
                        det_rows.append({"tenant": tenant, "prompt": p.id, "type": e.type, "format": e.fmt,
                                         "status": s.status, "placeholder_types": "|".join(s.placeholder_types),
                                         "aligned": aligned})
                    for o in over:
                        over_rows.append({"tenant": tenant, "prompt": p.id, "masked_text": p.text[o.start:o.end],
                                          "placeholder_types": "|".join(o.types)})
                if tenant == "a":
                    rest_rows.append({"tenant": tenant, "prompt": p.id, "variant": variant, "status": r.status_code,
                                      "restore_ok": restore_ok(p.text, final),
                                      "residual_placeholders": len(residual_placeholders(final)),
                                      "masking_header": r.headers.get("x-gateway-pseudonymization", "")})
        c.post(f"{mock}/_variant", json={"variant": "exact"})
    write_csv(f"e4_detection_{tenant}.csv", det_rows)
    write_csv(f"e4_overmask_{tenant}.csv", over_rows)
    if rest_rows:
        write_csv("e4_restore_a.csv", rest_rows)
    print(f"E4 {tenant}: {len(det_rows)} entity scores, {len(over_rows)} over-masked ranges, {len(rest_rows)} restores")


def analyse() -> None:
    summary = []
    for tenant in ("a", "b"):
        p = RESULTS / f"e4_detection_{tenant}.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
        groups: dict[tuple, list] = collections.defaultdict(list)
        for r in rows:
            groups[("all", "all")].append(r)
            groups[(r["type"], "all")].append(r)
            groups[(r["type"], r["format"])].append(r)
        for (typ, fmt), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] != "all", kv[0])):
            if fmt != "all" and typ == "PERSON":
                continue
            n = len(rs)
            masked = sum(r["status"] == "masked" for r in rs)
            leaked = sum(r["status"] in ("leaked", "partial") for r in rs)
            lo, hi = wilson(masked, n)
            summary.append({"tenant": tenant, "type": typ, "format": fmt, "n": n, "masked": masked,
                            "detection_rate": round(masked / n, 4), "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                            "leaked_or_partial": leaked, "partial": sum(r["status"] == "partial" for r in rs),
                            "unaligned": sum(r["status"] == "unaligned" for r in rs)})
        over = list(csv.DictReader(open(RESULTS / f"e4_overmask_{tenant}.csv", encoding="utf-8"))) \
            if (RESULTS / f"e4_overmask_{tenant}.csv").stat().st_size else []
        prompts_with_over = len({o["prompt"] for o in over})
        lo, hi = wilson(prompts_with_over, N)
        summary.append({"tenant": tenant, "type": "OVER-MASKING", "format": "prompts with >=1 non-entity span masked",
                        "n": N, "masked": prompts_with_over, "detection_rate": round(prompts_with_over / N, 4),
                        "ci95_low": round(lo, 4), "ci95_high": round(hi, 4), "leaked_or_partial": "",
                        "partial": "", "unaligned": ""})
        top = collections.Counter((o["masked_text"], o["placeholder_types"]) for o in over).most_common(8)
        print(f"tenant {tenant} over-masked (text, type) top: {top}")
    write_csv("e4_summary.csv", summary)
    rp = RESULTS / "e4_restore_a.csv"
    if rp.exists():
        rows = list(csv.DictReader(open(rp, encoding="utf-8")))
        rest = []
        for v in VARIANTS + ("all",):
            rs = [r for r in rows if v == "all" or r["variant"] == v]
            ok = sum(r["restore_ok"] == "True" for r in rs)
            lo, hi = wilson(ok, len(rs))
            rest.append({"variant": v, "n": len(rs), "restored_exactly": ok, "rate": round(ok / len(rs), 4),
                         "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                         "non_200": sum(r["status"] != "200" for r in rs),
                         "with_residual_placeholders": sum(int(r["residual_placeholders"]) > 0 for r in rs)})
        write_csv("e4_restore_summary.csv", rest)
        for r in rest:
            print(r)
    for s in summary:
        print(f"{s['tenant']} {s['type']:12} {s['format']:42} {fmt_pct_ci(s['masked'], s['n'])}  leaked/partial "
              f"{s['leaked_or_partial']}")


if __name__ == "__main__":
    run(sys.argv[2]) if sys.argv[1] == "run" else analyse()
