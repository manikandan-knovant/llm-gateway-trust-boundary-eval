"""E11: independent external benchmark ASQ-PHI on both builds (tenant A, mock upstream, $0).

Source: ASQ-PHI, "An Adversarial Synthetic Benchmark for Clinical De-Identification and Search Utility",
Mendeley Data, DOI 10.17632/csz5dzp7nx.1 (version 1), MIT licence; Data in Brief 2026, DOI 10.1016/j.dib.2026.112586.
File used unchanged: data/synthetic_clinical_queries.txt (sha256 checked against the repository's listing).
The dataset is NOT copied into this repository: ASQPHI_FILE points at a copy outside it, and results keep only query
indices, tag types and outcomes (no query text, no values).

Parsing follows the authors' own validator (data_generation_pipeline.ipynb, validate_dataset): the file is split on
"===QUERY==="; each block has the query text, then "===PHI_TAGS===", then one JSON object per line
({"identifier_type", "value"}); a block without tag lines is a hard negative.

Each query is sent UNEDITED through tenant A on the route without a BAA (phi--mock, masking mandatory). A tag's value
is located in its query (every verbatim occurrence); a value that does not occur verbatim is reported as unlocatable
and left out of recall. Each occurrence is scored on what the upstream received: masked / partial / leaked.

  python e11_asqphi.py parse                 counts only (checks against dataset_statistics.txt)
  python e11_asqphi.py run a-base|a-fix
  python e11_asqphi.py analyse
"""

from __future__ import annotations

import collections
import csv
import json
import os
import sys
from dataclasses import dataclass

import httpx

from common5 import RESULTS, TENANTS, load_state, write_csv
from scoring import score_entities
from stats import wilson

EXPECTED = {"NAME": {"PERSON"}, "GEOGRAPHIC_LOCATION": {"LOCATION", "ZIP"}, "DATE": {"DATE"},
            "MEDICAL_RECORD_NUMBER": {"MRN"}, "HEALTH_PLAN_BENEFICIARY_NUMBER": {"MEMBER_ID"},
            "PHONE_NUMBER": {"PHONE"}, "SOCIAL_SECURITY_NUMBER": {"SSN"}, "EMAIL_ADDRESS": {"EMAIL"},
            "UNIQUE_IDENTIFIER": None, "ACCOUNT_NUMBER": {"ACCOUNT"}, "FAX_NUMBER": {"PHONE"},
            "CERTIFICATE_LICENSE_NUMBER": {"LICENSE"}, "IP_ADDRESS": {"IP"}}   # None: any placeholder type is right


@dataclass(frozen=True)
class Occ:
    type: str
    value: str
    fmt: str
    start: int
    end: int


def parse(path: str) -> list[dict]:
    content = open(path, encoding="utf-8").read()
    out = []
    for i, block in enumerate(content.split("===QUERY===")[1:]):
        query, _, tags = block.partition("===PHI_TAGS===")
        items = [json.loads(line) for line in tags.strip().splitlines() if line.strip()]
        out.append({"index": i, "query": query.strip(), "tags": items})
    return out


def occurrences(query: str, tags: list[dict]) -> tuple[list[Occ], list[dict]]:
    occ, missing = [], []
    for t in tags:
        v = t["value"]
        start, found = query.find(v), False
        while v and start >= 0:
            occ.append(Occ(t["identifier_type"], v, t["identifier_type"], start, start + len(v)))
            found = True
            start = query.find(v, start + 1)
        if not found:
            missing.append(t)
    return occ, missing


def cmd_parse() -> None:
    qs = parse(os.environ["ASQPHI_FILE"])
    tags = [t for q in qs for t in q["tags"]]
    print(f"queries {len(qs)}, with PHI {sum(bool(q['tags']) for q in qs)}, hard negatives "
          f"{sum(not q['tags'] for q in qs)}, PHI elements {len(tags)}")
    print("by type:", dict(collections.Counter(t["identifier_type"] for t in tags).most_common()))
    unloc = sum(len(occurrences(q["query"], q["tags"])[1]) for q in qs)
    print(f"tag values not found verbatim in their query: {unloc}")


def run(tenant: str) -> None:
    t, key = TENANTS[tenant], load_state(tenant)["keys"]["phi-app"]
    qs = parse(os.environ["ASQPHI_FILE"])
    occ_rows, q_rows = [], []
    with httpx.Client(timeout=120) as c:
        for q in qs:
            r = c.post(f"http://127.0.0.1:{t['gw']}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                       json={"model": "phi", "messages": [{"role": "user", "content": q["query"]}], "max_tokens": 50})
            recv = c.get(f"http://127.0.0.1:{t['mock']}/_last", params={"target": "phi"}).json()
            users = [m for m in recv.get("messages", []) if m.get("role") == "user"]
            received = users[-1]["content"] if (users and r.status_code == 200) else ""
            occ, missing = occurrences(q["query"], q["tags"])
            scores, over, aligned = score_entities(q["query"], received, occ) if received else ([], [], False)
            for o, s in zip(occ, scores):
                exp = EXPECTED.get(o.type)
                occ_rows.append({"build": tenant, "query": q["index"], "type": o.type, "status": s.status,
                                 "placeholder_types": "|".join(s.placeholder_types),
                                 "wrong_type": s.status == "masked" and exp is not None
                                 and not set(s.placeholder_types) <= exp})
            q_rows.append({"build": tenant, "query": q["index"], "hard_negative": not q["tags"],
                           "status": r.status_code, "aligned": aligned, "unlocatable_tags": len(missing),
                           "overmasked_spans": len(over),
                           "overmasked_types": "|".join(sorted({tp for o in over for tp in o.types}))})
    write_csv(f"e11_occurrences_{tenant}.csv", occ_rows)
    write_csv(f"e11_queries_{tenant}.csv", q_rows)
    print(f"E11 {tenant}: {len(q_rows)} queries, {len(occ_rows)} tagged occurrences, "
          f"non-200 {sum(r['status'] != 200 for r in q_rows)}, unaligned {sum(not r['aligned'] for r in q_rows)}")


def _rate(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]" if n else "n/a"


def analyse() -> None:
    builds = ("a-base", "a-fix")
    occ = {b: list(csv.DictReader(open(RESULTS / f"e11_occurrences_{b}.csv", encoding="utf-8"))) for b in builds}
    qs = {b: list(csv.DictReader(open(RESULTS / f"e11_queries_{b}.csv", encoding="utf-8"))) for b in builds}
    types = [t for t, _ in collections.Counter(r["type"] for r in occ["a-fix"]).most_common()]
    rows = []
    for t in types + ["ALL"]:
        row = {"type": t}
        for b in builds:
            rs = [r for r in occ[b] if t == "ALL" or r["type"] == t]
            row[f"recall_{b}"] = _rate(sum(r["status"] == "masked" for r in rs), len(rs))
            row[f"leaked_{b}"] = sum(r["status"] == "leaked" for r in rs)
            row[f"partial_{b}"] = sum(r["status"] == "partial" for r in rs)
            row[f"wrong_type_{b}"] = sum(r["wrong_type"] == "True" for r in rs)
        rows.append(row)
    write_csv("e11_recall.csv", rows)
    over = []
    for b in builds:
        neg = [q for q in qs[b] if q["hard_negative"] == "True"]
        pos = [q for q in qs[b] if q["hard_negative"] != "True"]
        leak_q = {r["query"] for r in occ[b] if r["status"] != "masked"}
        over.append({"build": b, "hard_negatives_overmasked": _rate(sum(int(q["overmasked_spans"]) > 0 for q in neg),
                                                                    len(neg)),
                     "hard_negative_masked_spans": sum(int(q["overmasked_spans"]) for q in neg),
                     "hard_negative_types": json.dumps(dict(collections.Counter(
                         tp for q in neg for tp in q["overmasked_types"].split("|") if tp))),
                     "phi_queries_with_any_value_in_clear": _rate(len(leak_q), len(pos)),
                     "unlocatable_tags": sum(int(q["unlocatable_tags"]) for q in qs[b]),
                     "unaligned_queries": sum(q["aligned"] != "True" for q in qs[b]),
                     "non_200": sum(q["status"] != "200" for q in qs[b])})
    write_csv("e11_overmasking.csv", over)
    for r in rows:
        print(json.dumps(r))
    for o in over:
        print(o)


if __name__ == "__main__":
    {"parse": lambda: cmd_parse(), "run": lambda: run(sys.argv[2]), "analyse": lambda: analyse()}[sys.argv[1]]()
