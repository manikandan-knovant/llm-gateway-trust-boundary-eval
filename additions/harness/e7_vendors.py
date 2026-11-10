"""E7: real-provider restore across vendors on the fix build (3c468b0).

Providers (all on routes without a BAA, so masking is mandatory):
  mini    OpenAI gpt-5-mini-2025-08-07         (max_completion_tokens, reasoning_effort minimal; as E4-real)
  gpt5    OpenAI gpt-5-2025-08-07              (same parameters)
  sonnet  Anthropic claude-sonnet-5-5          (parameters in CLAUDE_PARAMS; see 05_additions.md)
Azure OpenAI was in the plan; the key file's AZURE_OPENAI_* values are empty, so that provider is not run.

Prompt sets:
  A  the E4-real set: synthetic_prompts.generate(100, seed=4040) with the E4-real system message, 100 output tokens
  B  the first 100 PHI notes of E8 (notes5.generate(300, 100, seed=8)), "rewrite, keep every identifier", 400 tokens

Each call goes through the gateway to the provider via a body recorder (recorder5.py), which shows what the provider
received, the model's raw answer (with placeholders) and the provider's own time (upstream_ms).
Measured per call: identifiers masked at the provider; placeholder fate in the raw answer (kept / altered restorable /
altered unrestorable / absent); restore success (status 200, every planted value verbatim in the final answer, no
placeholder-like token left); doubled MRN label ("MRN MRN", as measured in the baseline E4-real); gateway-added latency
= client latency - upstream_ms. Spend from the usage the gateway returns, at the route's prices.

  python e7_vendors.py run [--limit N]     (inside the real tenant network; stops before passing the spend cap)
  python e7_vendors.py analyse             (host)
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import time

PROVIDERS = {"mini": ("real-mini", "recorder-openai", 2.5e-07, 2.0e-06, "gpt-5-mini-2025-08-07"),
             "gpt5": ("real-gpt5", "recorder-openai", 1.25e-06, 1.0e-05, "gpt-5-2025-08-07"),
             "sonnet": ("real-sonnet", "recorder-anthropic", 2.0e-06, 1.0e-05, "claude-sonnet-5-5")}
SYSTEM_A = ("Rewrite or summarize the user's note for the care team in at most two sentences. Keep every name, date, "
            "phone number and record number exactly as written.")
SYSTEM_B = ("Rewrite the user's note for the care team in plain language. Keep every name, date, number, address, "
            "identifier and contact detail exactly as written.")
CLAUDE_PARAMS = json.loads(os.environ.get("CLAUDE_PARAMS", "{}"))
DOUBLED = re.compile(r"MRN\W{0,3}(MRN|medical record number)", re.I)
CAP_USD = float(os.environ.get("E7_CAP_USD", "60"))


def params(provider: str, tokens: int) -> dict:
    if provider == "sonnet":
        return {"max_tokens": tokens, **CLAUDE_PARAMS}
    return {"max_completion_tokens": tokens, "reasoning_effort": "minimal"}


def sent_user_text(req: dict) -> str:
    users = [m for m in (req or {}).get("messages", []) if m.get("role") == "user"]
    if not users:
        return ""
    c = users[-1].get("content")
    if isinstance(c, str):
        return c
    return "".join(p.get("text", "") for p in c or [] if isinstance(p, dict) and p.get("type") == "text")


def raw_output(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    if "choices" in resp:
        try:
            return resp["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return ""
    return "".join(b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")


def fate(placeholder: str, raw: str) -> str:
    m = re.fullmatch(r"<<([A-Z_]+)_(\d+)>>", placeholder)
    typ, num = m.group(1), m.group(2)
    if placeholder in raw:
        return "kept"
    if re.search(rf"<{typ}_{num}>|\[{typ}_{num}\]|\b{typ}_{num}\b", raw):
        return "altered_restorable"
    if re.search(rf"{typ}[\s_-]*0*{num}\b", raw, re.I):
        return "altered_unrestorable"
    return "absent"


def run(limit: int | None) -> None:
    import httpx

    from common5 import write_csv
    from notes5 import generate as notes
    from scoring import residual_placeholders, score_entities
    from synthetic_prompts import generate as e4

    key = json.loads(os.environ["ITPRO_STATE_REAL"])["keys"]["real-app"]
    set_a = [("A", p.id, p.text, p.entities, SYSTEM_A, 100) for p in e4(100, 4040)]
    set_b = [("B", n.id, n.text, n.entities, SYSTEM_B, 400) for n in notes(300, 100, seed=8) if not n.negative][:100]
    prompts = (set_a + set_b)[:limit] if limit else set_a + set_b
    rows, ents = [], []
    spent = 0.0
    with httpx.Client(timeout=300) as c:
        since = {rec: max([e["seq"] for e in c.get(f"http://{rec}:8080/_log").json()] or [0])
                 for rec in ("recorder-openai", "recorder-anthropic")}
        for prov, (model, rec, p_in, p_out, snapshot) in PROVIDERS.items():
            for pset, pid, text, entities, system, tokens in prompts:
                if spent > CAP_USD:
                    print(f"E7 stopped: spend {spent:.4f} USD passed the E7 cap {CAP_USD}")
                    break
                body = {"model": model, "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": text}], **params(prov, tokens)}
                t0 = time.perf_counter()
                r = c.post("http://gateway:4000/v1/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}"})
                client_ms = (time.perf_counter() - t0) * 1000
                log = c.get(f"http://{rec}:8080/_log", params={"since": since[rec]}).json()
                ex = log[-1] if log else None
                if ex:
                    since[rec] = ex["seq"]
                final, finish, usage, code = "", "", {}, ""
                if r.status_code == 200:
                    j = r.json()
                    final = j["choices"][0]["message"].get("content") or ""
                    finish, usage = j["choices"][0].get("finish_reason", ""), j.get("usage") or {}
                else:
                    try:
                        code = str((r.json().get("error") or {}).get("code") or "")
                    except ValueError:
                        code = ""
                pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                cost = pt * p_in + ct * p_out
                spent += cost
                sent = sent_user_text(ex["request"]) if ex else ""
                raw = raw_output(ex["response"]) if ex else ""
                scores, over, aligned = score_entities(text, sent, entities) if sent else ([], [], False)
                residual = residual_placeholders(final)
                all_in = all(e.value in final for e in entities)
                fully_masked = bool(scores) and all(s.status == "masked" for s in scores)
                mrn_sent = bool(re.search(r"<<MRN_\d+>>", sent))
                rows.append({"provider": prov, "snapshot": snapshot, "set": pset, "prompt": pid,
                             "status": r.status_code, "error_code": code, "finish_reason": finish,
                             "client_ms": round(client_ms, 1), "upstream_ms": ex["upstream_ms"] if ex else "",
                             "gateway_added_ms": round(client_ms - ex["upstream_ms"], 1) if ex else "",
                             "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": round(cost, 8),
                             "aligned": aligned, "fully_masked": fully_masked,
                             "restore_success": r.status_code == 200 and all_in and not residual,
                             "residual_placeholders": len(residual), "mrn_placeholder_sent": mrn_sent,
                             "doubled_mrn_label": mrn_sent and bool(DOUBLED.search(final))})
                for e, s in zip(entities, scores):
                    fates = [fate(ph, raw) for ph in s.placeholders] if r.status_code == 200 else []
                    order = ["absent", "altered_unrestorable", "altered_restorable", "kept"]
                    ents.append({"provider": prov, "set": pset, "prompt": pid, "type": e.type, "format": e.fmt,
                                 "provider_received": s.status,
                                 "fate": min(fates, key=order.index) if fates else
                                 ("n/a (not masked)" if s.status != "masked" else "n/a (call failed)"),
                                 "in_final": e.value in final})
            print(f"E7 {prov}: done, cumulative spend {spent:.4f} USD")
    write_csv("e7_calls.csv", rows)
    write_csv("e7_identifiers.csv", ents)


def analyse() -> None:
    from common5 import RESULTS, write_csv
    from stats import percentile, wilson

    def rate(k, n):
        lo, hi = wilson(k, n)
        return f"{k}/{n} = {100 * k / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]" if n else "n/a"

    rows = list(csv.DictReader(open(RESULTS / "e7_calls.csv", encoding="utf-8")))
    ents = list(csv.DictReader(open(RESULTS / "e7_identifiers.csv", encoding="utf-8")))
    out = []
    for prov in PROVIDERS:
        for pset in ("A", "B", "all"):
            rs = [r for r in rows if r["provider"] == prov and (pset == "all" or r["set"] == pset)]
            es = [e for e in ents if e["provider"] == prov and (pset == "all" or e["set"] == pset)]
            if not rs:
                continue
            masked_es = [e for e in es if e["provider_received"] == "masked"]
            fm = [r for r in rs if r["fully_masked"] == "True"]
            mrn = [r for r in rs if r["mrn_placeholder_sent"] == "True"]
            added = [float(r["gateway_added_ms"]) for r in rs if r["gateway_added_ms"] and r["status"] == "200"]
            fates = collections.Counter(e["fate"] for e in masked_es)
            out.append({"provider": prov, "snapshot": rs[0]["snapshot"], "set": pset, "calls": len(rs),
                        "status_200": sum(r["status"] == "200" for r in rs),
                        "placeholders_kept_intact": rate(fates["kept"], len(masked_es)),
                        "fate_altered_restorable": fates["altered_restorable"],
                        "fate_altered_unrestorable": fates["altered_unrestorable"], "fate_absent": fates["absent"],
                        "restore_success_all": rate(sum(r["restore_success"] == "True" for r in rs), len(rs)),
                        "restore_success_fully_masked": rate(sum(r["restore_success"] == "True" for r in fm), len(fm)),
                        "identifiers_masked_at_provider": rate(len(masked_es), len(es)),
                        "doubled_mrn_label": rate(sum(r["doubled_mrn_label"] == "True" for r in mrn), len(mrn)),
                        "truncated_finish_length": sum(r["finish_reason"] == "length" for r in rs),
                        "gateway_added_median_ms": round(percentile(added, 50), 1) if added else "",
                        "gateway_added_p95_ms": round(percentile(added, 95), 1) if added else "",
                        "spend_usd": round(sum(float(r["cost_usd"]) for r in rs), 4)})
    write_csv("e7_summary.csv", out)
    for o in out:
        print(json.dumps(o))
    print(f"E7 total spend: {sum(float(r['cost_usd']) for r in rows):.4f} USD")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyse"])
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(a.limit) if a.cmd == "run" else analyse()
