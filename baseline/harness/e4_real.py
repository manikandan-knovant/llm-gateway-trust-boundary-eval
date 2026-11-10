"""E4-real: masking and restore with real models (baseline build 2026-11-09).

100 synthetic patient-style prompts (generator seed 4040) through the gateway on routes WITHOUT a BAA (masking
mandatory), on two models: GPT-5 mini (gpt-5-mini-2025-08-07) and GPT-5 (gpt-5-2025-08-07).
The model is asked to rewrite or summarize, so it must carry the placeholders through its own wording.
max_completion_tokens 100, reasoning_effort minimal.

The provider is reached through the body recorder (recorder_proxy.py), which shows exactly what the provider
received and the model's RAW answer (with placeholders), before the gateway restores it.

Per prompt and identifier:
  provider_received   masked | partial | leaked        (scoring.score_entities on the recorded request)
  placeholder fate    kept (exact <<T_n>>) | altered_restorable (<T_n>, [T_n], T_n: the gateway restores these)
                      | altered_unrestorable (another form with the same type and number, e.g. <<PERSON 1>>)
                      | absent (no trace: dropped or reworded; split by manual review, see 02_results.md)
  in_final            the original value appears verbatim in the answer the client got
Restore success (per prompt): status 200, every planted value in the final answer, no placeholder-like token left.

  python e4_real.py run          (inside the real tenant network, gateway on the recorder)
  python e4_real.py analyse      (host)
"""

from __future__ import annotations

import collections
import csv
import json
import os
import re
import sys

MODELS = {"real-mini": ("gpt-5-mini-2025-08-07", 2.5e-07, 2.0e-06), "real-gpt5": ("gpt-5-2025-08-07", 1.25e-06, 1.0e-05)}
SEED, N = 4040, 100
SYSTEM = ("Rewrite or summarize the user's note for the care team in at most two sentences. Keep every name, date, "
          "phone number and record number exactly as written.")


def placeholder_fate(placeholder: str, raw: str) -> str:
    m = re.fullmatch(r"<<([A-Z_]+)_(\d+)>>", placeholder)
    typ, num = m.group(1), m.group(2)
    if placeholder in raw:
        return "kept"
    if re.search(rf"<{typ}_{num}>|\[{typ}_{num}\]|\b{typ}_{num}\b", raw):
        return "altered_restorable"
    if re.search(rf"{typ}[\s_-]*0*{num}\b", raw, re.I):
        return "altered_unrestorable"
    return "absent"


def run() -> None:
    import httpx

    from common import write_csv
    from scoring import residual_placeholders, score_entities
    from synthetic_prompts import generate

    key = json.loads(os.environ["ITPRO_STATE_REAL"])["keys"]["real-app"]
    prompts = generate(N, SEED)
    rows, ents = [], []
    with httpx.Client(timeout=300) as c:
        since = max([e["seq"] for e in c.get("http://recorder:8080/_log").json()] or [0])
        for model, (snapshot, p_in, p_out) in MODELS.items():
            for p in prompts:
                body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                     {"role": "user", "content": p.text}],
                        "max_completion_tokens": 100, "reasoning_effort": "minimal"}
                r = c.post("http://gateway:4000/v1/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {key}"})
                log = c.get("http://recorder:8080/_log", params={"since": since}).json()
                ex = log[-1] if log else None
                if ex:
                    since = ex["seq"]
                final, finish, usage = "", "", {}
                if r.status_code == 200:
                    j = r.json()
                    final = j["choices"][0]["message"].get("content") or ""
                    finish = j["choices"][0].get("finish_reason", "")
                    usage = j.get("usage") or {}
                sent_user = ""
                raw = ""
                if ex and ex.get("request"):
                    users = [m for m in ex["request"].get("messages", []) if m.get("role") == "user"]
                    sent_user = users[-1]["content"] if users else ""
                    try:
                        raw = ex["response"]["choices"][0]["message"].get("content") or ""
                    except (KeyError, TypeError, IndexError):
                        raw = ""
                scores, over, aligned = score_entities(p.text, sent_user, p.entities)
                placeholders = re.findall(r"<<[A-Z_]+_\d+>>", sent_user)
                all_in = all(e.value in final for e in p.entities)
                residual = residual_placeholders(final)
                ok = r.status_code == 200 and all_in and not residual
                pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                rows.append({"model": model, "snapshot": snapshot, "prompt": p.id, "status": r.status_code,
                             "error_code": "" if r.status_code == 200 else str(((r.json() if r.headers.get(
                                 "content-type", "").startswith("application/json") else {}).get("error") or {})
                                 .get("code", "")),
                             "finish_reason": finish, "prompt_tokens": pt, "completion_tokens": ct,
                             "cost_usd": round(pt * p_in + ct * p_out, 8),
                             "recorded": bool(ex), "aligned": aligned, "placeholders_sent": len(placeholders),
                             "over_masked_spans": len(over), "all_values_in_final": all_in,
                             "residual_placeholders": len(residual), "restore_success": ok,
                             "prompt_text": p.text, "provider_received": sent_user, "model_raw_output": raw,
                             "final_answer": final})
                for e, s in zip(p.entities, scores):
                    # the entity's own placeholder token(s), from the alignment; the worst fate counts
                    fates = [placeholder_fate(ph, raw) for ph in s.placeholders]
                    order = ["absent", "altered_unrestorable", "altered_restorable", "kept"]
                    fate = min(fates, key=order.index) if fates else "n/a (not masked)"
                    ents.append({"model": model, "prompt": p.id, "type": e.type, "format": e.fmt,
                                 "provider_received": s.status, "placeholders": "|".join(s.placeholders),
                                 "placeholder_fate": fate if r.status_code == 200 else "n/a (call failed)",
                                 "in_final": e.value in final})
    write_csv("e4_real.csv", rows)
    write_csv("e4_real_entities.csv", ents)
    print(f"E4-real: {len(rows)} calls; restore success "
          + ", ".join(f"{m}: {sum(r['restore_success'] for r in rows if r['model'] == m)}/{N}" for m in MODELS))


def analyse() -> None:
    from common import RESULTS, write_csv
    from stats import wilson

    rows = list(csv.DictReader(open(RESULTS / "e4_real.csv", encoding="utf-8")))
    ents = list(csv.DictReader(open(RESULTS / "e4_real_entities.csv", encoding="utf-8")))
    out = []
    for m in MODELS:
        rs = [r for r in rows if r["model"] == m]
        es = [e for e in ents if e["model"] == m]
        ok = sum(r["restore_success"] == "True" for r in rs)
        lo, hi = wilson(ok, len(rs))
        rec = collections.Counter(e["provider_received"] for e in es)
        fate = collections.Counter(e["placeholder_fate"] for e in es)
        out.append({"model": m, "snapshot": MODELS[m][0], "prompts": len(rs), "status_200": sum(r["status"] == "200"
                                                                                                 for r in rs),
                    "restore_success": ok, "rate": round(ok / len(rs), 4), "ci95_low": round(lo, 4),
                    "ci95_high": round(hi, 4),
                    "identifiers": len(es), "received_masked": rec["masked"], "received_partial": rec["partial"],
                    "received_leaked": rec["leaked"], "fate_kept": fate["kept"],
                    "fate_altered_restorable": fate["altered_restorable"],
                    "fate_altered_unrestorable": fate["altered_unrestorable"], "fate_absent": fate["absent"],
                    "values_in_final": sum(e["in_final"] == "True" for e in es),
                    "finish_length": sum(r["finish_reason"] == "length" for r in rs),
                    "residual_in_final": sum(int(r["residual_placeholders"]) > 0 for r in rs),
                    "spend_usd": round(sum(float(r["cost_usd"]) for r in rs), 4)})
    write_csv("e4_real_summary.csv", out)
    print(json.dumps(out, indent=1))
    by = collections.Counter((e["model"], e["type"], e["format"], e["provider_received"]) for e in ents
                             if e["provider_received"] != "masked")
    print("not fully masked at the provider:", dict(by))


if __name__ == "__main__":
    run() if sys.argv[1] == "run" else analyse()
