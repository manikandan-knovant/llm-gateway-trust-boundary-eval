"""E8: Safe Harbor coverage on both builds (mock upstream, $0).

400 synthetic notes (notes5.generate(seed=8): 300 PHI notes, 100 PHI-free) through tenant A on the route without a BAA
(phi--mock: masking mandatory). Scored on what the upstream RECEIVED (mock GET /_last), per planted identifier:
masked / partial / leaked (scoring.score_entities, exact spans). Over-masking: any masked span on a PHI-free note,
and masked spans touching no planted identifier on PHI notes. Wrong placeholder type: a masked identifier whose
placeholder type is not one the category expects.

  python e8_coverage.py run a-base|a-fix
  python e8_coverage.py analyse
"""

from __future__ import annotations

import collections
import csv
import json
import sys

import httpx

from common5 import RESULTS, TENANTS, load_state, write_csv
from notes5 import CATEGORIES, ORDER, generate
from scoring import score_entities
from stats import wilson

# What the gateway's code targets for each category (both builds; the fix build adds label variants for MRN, member ID
# and claim number and month-name date rules). None = no detector in code.
DETECTOR = {
    "NAME": "NER PERSON", "STREET": "NER LOCATION", "CITY": "NER LOCATION", "COUNTY": "NER LOCATION",
    "ZIP": "regex ZIP (needs 'zip'/'postal' within 40 chars before)", "DATE": "regex DATE + NER DATE_TIME",
    "AGE90": None, "PHONE": "regex PHONE", "FAX": "regex PHONE (any phone shape)", "EMAIL": "regex EMAIL",
    "SSN": "regex SSN (dashed form) + NER US_ITIN", "MRN": "synthetic tenant rule (OQ-062)",
    "MEMBER": "synthetic tenant rule (OQ-062)", "ACCOUNT": "NER US_BANK_NUMBER, CREDIT_CARD, IBAN_CODE",
    "LICENSE": "NER US_DRIVER_LICENSE, MEDICAL_LICENSE, US_PASSPORT",
    "VEHICLE": "regex VIN (needs 'vin'/'vehicle' within 40 chars before); no plate rule",
    "DEVICE": None, "URL": "regex URL (http/https only)", "IP": "regex IPv4", "BIOMETRIC": None,
    "CLAIM": "synthetic tenant rule (OQ-062)",
}
BUILDS = {"a-base": "baseline 6fb0334", "a-fix": "fix 3c468b0"}


def run(tenant: str) -> None:
    t, state = TENANTS[tenant], load_state(tenant)
    key = state["keys"]["phi-app"]
    notes = generate(300, 100, seed=8)
    ent_rows, note_rows = [], []
    with httpx.Client(timeout=120) as c:
        for n in notes:
            r = c.post(f"http://127.0.0.1:{t['gw']}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                       json={"model": "phi", "messages": [{"role": "user", "content": n.text}], "max_tokens": 50})
            recv = c.get(f"http://127.0.0.1:{t['mock']}/_last", params={"target": "phi"}).json()
            users = [m for m in recv.get("messages", []) if m.get("role") == "user"]
            received = users[-1]["content"] if (users and r.status_code == 200) else ""
            scores, over, aligned = score_entities(n.text, received, n.entities) if received else ([], [], False)
            for e, s in zip(n.entities, scores):
                exp = CATEGORIES[e.type][2]
                ent_rows.append({"build": tenant, "note": n.id, "category": e.type, "format": e.fmt,
                                 "status": s.status, "placeholder_types": "|".join(s.placeholder_types),
                                 "wrong_type": s.status == "masked" and not set(s.placeholder_types) <= exp})
            note_rows.append({"build": tenant, "note": n.id, "negative": n.negative, "status": r.status_code,
                              "aligned": aligned, "overmasked_spans": len(over),
                              "overmasked_types": "|".join(sorted({tp for o in over for tp in o.types}))})
    write_csv(f"e8_entities_{tenant}.csv", ent_rows)
    write_csv(f"e8_notes_{tenant}.csv", note_rows)
    print(f"E8 {tenant}: {len(note_rows)} notes, {len(ent_rows)} identifiers, "
          f"non-200: {sum(r['status'] != 200 for r in note_rows)}, unaligned: {sum(not r['aligned'] for r in note_rows)}")


def _rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]" if n else "n/a"


def analyse() -> None:
    ents = {b: list(csv.DictReader(open(RESULTS / f"e8_entities_{b}.csv", encoding="utf-8"))) for b in BUILDS}
    notes = {b: list(csv.DictReader(open(RESULTS / f"e8_notes_{b}.csv", encoding="utf-8"))) for b in BUILDS}
    rows = []
    groups = [(k, [k]) for k in ORDER]
    groups.insert(ORDER.index("STREET"), ("GEO_ALL", ["STREET", "CITY", "COUNTY", "ZIP"]))
    for key, members in groups + [("ALL", ORDER)]:
        row = {"category": key, "label": CATEGORIES[key][0] if key in CATEGORIES else
               ("Geographic subdivisions (all four)" if key == "GEO_ALL" else "All planted identifiers"),
               "safe_harbor": CATEGORIES[key][1] if key in CATEGORIES else ("2" if key == "GEO_ALL" else "")}
        for b in BUILDS:
            rs = [r for r in ents[b] if r["category"] in members]
            masked = sum(r["status"] == "masked" for r in rs)
            row[f"recall_{b}"] = _rate(masked, len(rs))
            row[f"recall_lo_{b}"] = round(wilson(masked, len(rs))[0], 4) if rs else ""
            row[f"leaked_{b}"] = sum(r["status"] == "leaked" for r in rs)
            row[f"partial_{b}"] = sum(r["status"] == "partial" for r in rs)
            row[f"wrong_type_{b}"] = sum(r["wrong_type"] == "True" for r in rs)
        if key in DETECTOR:
            det = DETECTOR[key]
            lo = row["recall_lo_a-fix"]
            row["status_fix"] = ("not in scope (no detector)" if det is None else
                                 "handled" if lo != "" and lo >= 0.90 else "partially")
            row["detector"] = det or "none"
        rows.append(row)
    rows.append({"category": "PHOTO", "label": "Full-face photographs", "safe_harbor": "17",
                 "status_fix": "not in scope (no detector; non-text parts are refused on non-BAA routes, "
                               "pseudonymization_unsupported_content)", "detector": "none (not text)"})
    write_csv("e8_coverage.csv", rows)
    fmt_rows = []
    for b in BUILDS:
        c = collections.defaultdict(list)
        for r in ents[b]:
            c[(r["category"], r["format"])].append(r)
        for (cat, f), rs in sorted(c.items()):
            fmt_rows.append({"build": b, "category": cat, "format": f,
                             "recall": _rate(sum(r["status"] == "masked" for r in rs), len(rs)),
                             "leaked": sum(r["status"] == "leaked" for r in rs),
                             "partial": sum(r["status"] == "partial" for r in rs),
                             "wrong_type": sum(r["wrong_type"] == "True" for r in rs)})
    write_csv("e8_by_format.csv", fmt_rows)
    over = []
    for b in BUILDS:
        neg = [n for n in notes[b] if n["negative"] == "True"]
        pos = [n for n in notes[b] if n["negative"] != "True"]
        types = collections.Counter(tp for n in neg for tp in n["overmasked_types"].split("|") if tp)
        over.append({"build": b, "phi_free_notes_with_masking": _rate(sum(int(n["overmasked_spans"]) > 0 for n in neg),
                                                                      len(neg)),
                     "phi_free_masked_spans": sum(int(n["overmasked_spans"]) for n in neg),
                     "phi_free_types": json.dumps(dict(types)),
                     "phi_notes_with_overmasking": _rate(sum(int(n["overmasked_spans"]) > 0 for n in pos), len(pos)),
                     "unaligned_notes": sum(n["aligned"] != "True" for n in notes[b])})
    write_csv("e8_overmasking.csv", over)
    for r in rows:
        print(json.dumps({k: r.get(k) for k in ("category", "recall_a-base", "recall_a-fix", "wrong_type_a-base",
                                                 "wrong_type_a-fix", "status_fix")}))
    for o in over:
        print(o)


if __name__ == "__main__":
    run(sys.argv[2]) if sys.argv[1] == "run" else analyse()
