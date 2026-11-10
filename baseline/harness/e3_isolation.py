"""E3: isolation between tenants and between projects (baseline build 2026-11-09).

  python e3_isolation.py plan    writes results/e3_plan.csv (expected outcome of every attempt) BEFORE anything runs
  python e3_isolation.py run     makes the attempts, compares with the plan, writes results/e3_isolation.csv

Tenants A and B are separate compose projects (own network, Postgres, master key, keys, token issuer, console).
"Strict match": the observed status (and, where stated, the row count) equals the plan.
"Access denied": the attempt returned none of the other tenant's or project's data (any non-2xx, a connection
failure, or a 2xx with zero rows).
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time

import httpx

from common import RESULTS, TENANTS, dev_token, load_state, utcnow, write_csv

A, B = TENANTS["a"], TENANTS["b"]
GA, GB = f"http://127.0.0.1:{A['gw']}", f"http://127.0.0.1:{B['gw']}"
CA, CB = f"http://127.0.0.1:{A['console']}", f"http://127.0.0.1:{B['console']}"

# id, scope, description, expected status ("conn_fail" = no connection), expected rows (None = not checked), kind
PLAN = [
    ("X1", "cross-tenant", "tenant A application key calls tenant B chat", 401, None, "attack"),
    ("X2", "cross-tenant", "tenant A master key calls tenant B key management (/key/generate)", 403, None, "attack"),
    ("X3", "cross-tenant", "tenant A master key calls tenant B chat", 401, None, "attack"),
    ("X4", "cross-tenant", "tenant A per-run key calls tenant B chat", 401, None, "attack"),
    ("X5", "cross-tenant", "tenant A issuer key mints a run key on tenant B", 401, None, "attack"),
    ("X6", "cross-tenant", "tenant A console token (A issuer, A audience) reads tenant B requests", 401, None, "attack"),
    ("X7", "cross-tenant", "token minted at tenant A issuer with tenant B audience, tenant and owner group, to B console",
     401, None, "attack"),
    ("X8", "cross-tenant", "network: tenant A gateway container opens TCP to tenant B Postgres", "conn_fail", None,
     "attack"),
    ("X9", "cross-tenant", "network: tenant A gateway container opens TCP to tenant B gateway", "conn_fail", None,
     "attack"),
    ("X10", "cross-tenant", "network: tenant A gateway container resolves tenant B container names", "conn_fail", None,
     "attack"),
    ("Y1", "cross-project", "proj1 key requests a logical model only proj2 may use", 403, None, "attack"),
    ("Y2", "cross-project", "proj1 key reads the library's spend logs (/spend/logs)", 403, None, "attack"),
    ("Y3", "cross-project", "proj1 key reads proj2's key info (/key/info)", 403, None, "attack"),
    ("Y4", "cross-project", "proj1 key reads proj2's team info (/team/info)", 403, None, "attack"),
    ("Y5", "cross-project", "proj1 issuer reads usage of a run issued by proj2's issuer", 404, None, "attack"),
    ("Y6", "cross-project", "proj1 issuer revokes a run issued by proj2's issuer", 404, None, "attack"),
    ("Y7", "cross-project", "proj1 per-run key requests proj2's logical model", 403, None, "attack"),
    ("Y8", "cross-project", "proj1-scoped console developer lists proj2 requests (?project=proj2)", 200, 0, "attack"),
    ("Y9", "cross-project", "proj1-scoped console developer opens a proj2 request by id", 404, None, "attack"),
    ("Y10", "cross-project", "proj1-scoped console developer reads proj2 cost", 403, None, "attack"),
    ("Y11", "cross-project", "proj1-scoped console developer assigns itself a role", 403, None, "attack"),
    ("P1", "positive", "tenant A proj1 key calls tenant A chat", 200, None, "control"),
    ("P2", "positive", "tenant B proj1 key calls tenant B chat", 200, None, "control"),
    ("P3", "positive", "proj1-scoped console developer lists proj1 requests", 200, ">=1", "control"),
    ("P4", "positive", "proj1 issuer reads usage of its own run", 200, None, "control"),
    ("P5", "positive", "tenant A owner token reads /v1/me on tenant A console", 200, None, "control"),
]


def plan() -> None:
    rows = [{"id": i, "scope": s, "attempt": d, "expected_status": e, "expected_rows": "" if r is None else r,
             "kind": k, "planned_at_utc": utcnow()} for i, s, d, e, r, k in PLAN]
    path = write_csv("e3_plan.csv", rows)
    print(f"E3 plan: {len(rows)} attempts written to {path.name} before any attempt was made")


def chat(base: str, key: str, model: str = "bench") -> httpx.Response:
    return httpx.post(f"{base}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=30,
                      json={"model": model, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 5})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def net_probe(target: str, port: int | None) -> str:
    """From inside tenant A's gateway container: TCP connect (or DNS lookup when port is None)."""
    code = ("import socket,sys\n"
            "t,p=sys.argv[1],sys.argv[2]\n"
            "try:\n"
            "  socket.getaddrinfo(t,None) if p=='dns' else socket.create_connection((t,int(p)),timeout=3).close()\n"
            "  print('connected')\n"
            "except Exception as e:\n"
            "  print('failed:'+type(e).__name__)\n")
    out = subprocess.run(["docker", "exec", "itpro-a-gateway-1", "python", "-c", code, target,
                          "dns" if port is None else str(port)], capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def ip_of(container: str) -> str:
    out = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
                          container], capture_output=True, text=True)
    return out.stdout.split()[0]


def run() -> None:
    planned = {r["id"]: r for r in csv.DictReader(open(RESULTS / "e3_plan.csv", encoding="utf-8"))}
    sa, sb = load_state("a"), load_state("b")
    # fixtures: one run per issuer, some traffic per project, console tokens
    ri = httpx.post(f"{GA}/v1/gateway/run-keys", headers=bearer(sa["issuers"]["proj1"]), timeout=30,
                    json={"run_id": "e3-run-proj1", "models": ["bench"], "ttl_seconds": 600, "max_budget": 0.5})
    ri.raise_for_status()
    run_key_p1 = ri.json()["key"]
    r2 = httpx.post(f"{GA}/v1/gateway/run-keys", headers=bearer(sa["issuers"]["proj2"]), timeout=30,
                    json={"run_id": "e3-run-proj2", "models": ["bench"], "ttl_seconds": 600, "max_budget": 0.5})
    r2.raise_for_status()
    chat(GA, sa["keys"]["proj1"]).raise_for_status()
    chat(GA, run_key_p1).raise_for_status()
    p2 = chat(GA, sa["keys"]["proj2"])
    p2.raise_for_status()
    p2_request_id = p2.headers.get("x-gateway-request-id")
    time.sleep(10)   # spend log rows are written in batches (proxy_batch_write_at: 1 s + up to 5 s)
    owner_a = dev_token("a", [A["owner_group"]])
    dev_p1 = dev_token("a", [sa["dev_group"]])
    forged = dev_token("a", [B["owner_group"]], aud=B["aud"], tid=B["tid"])
    p1, p2t = sa["teams"]["proj1"], sa["teams"]["proj2"]
    b_pg, b_gw = ip_of("itpro-b-postgres-1"), ip_of("itpro-b-gateway-1")

    def rows_of(r: httpx.Response):
        try:
            return len(r.json().get("data", []))
        except (ValueError, AttributeError):
            return ""

    attempts = {
        "X1": lambda: chat(GB, sa["keys"]["proj1"]),
        "X2": lambda: httpx.post(f"{GB}/key/generate", headers=bearer(A["master"]), json={}, timeout=30),
        "X3": lambda: chat(GB, A["master"]),
        "X4": lambda: chat(GB, run_key_p1),
        "X5": lambda: httpx.post(f"{GB}/v1/gateway/run-keys", headers=bearer(sa["issuers"]["proj1"]), timeout=30,
                                 json={"run_id": "e3-cross", "models": ["bench"], "ttl_seconds": 600,
                                       "max_budget": 0.5}),
        "X6": lambda: httpx.get(f"{CB}/v1/requests", headers=bearer(owner_a), timeout=30),
        "X7": lambda: httpx.get(f"{CB}/v1/me", headers=bearer(forged), timeout=30),
        "X8": lambda: net_probe(b_pg, 5432),
        "X9": lambda: net_probe(b_gw, 4000),
        "X10": lambda: net_probe("itpro-b-postgres-1", None),
        "Y1": lambda: chat(GA, sa["keys"]["proj1"], model="phi"),
        "Y2": lambda: httpx.get(f"{GA}/spend/logs", headers=bearer(sa["keys"]["proj1"]), timeout=30),
        "Y3": lambda: httpx.get(f"{GA}/key/info", params={"key": sa["keys"]["proj2"]},
                                headers=bearer(sa["keys"]["proj1"]), timeout=30),
        "Y4": lambda: httpx.get(f"{GA}/team/info", params={"team_id": p2t}, headers=bearer(sa["keys"]["proj1"]),
                                timeout=30),
        "Y5": lambda: httpx.get(f"{GA}/v1/gateway/run-keys/e3-run-proj2/usage", headers=bearer(sa["issuers"]["proj1"]),
                                timeout=30),
        "Y6": lambda: httpx.post(f"{GA}/v1/gateway/run-keys/revoke", headers=bearer(sa["issuers"]["proj1"]),
                                 json={"run_id": "e3-run-proj2"}, timeout=30),
        "Y7": lambda: chat(GA, run_key_p1, model="phi"),
        "Y8": lambda: httpx.get(f"{CA}/v1/requests", params={"project": p2t}, headers=bearer(dev_p1), timeout=30),
        "Y9": lambda: httpx.get(f"{CA}/v1/requests/{p2_request_id}", headers=bearer(dev_p1), timeout=30),
        "Y10": lambda: httpx.get(f"{CA}/v1/cost", params={"project": p2t}, headers=bearer(dev_p1), timeout=30),
        "Y11": lambda: httpx.post(f"{CA}/v1/role-assignments", headers=bearer(dev_p1), timeout=30,
                                  json={"group_object_id": sa["dev_group"], "role": "org_owner"}),
        "P1": lambda: chat(GA, sa["keys"]["proj1"]),
        "P2": lambda: chat(GB, sb["keys"]["proj1"]),
        "P3": lambda: httpx.get(f"{CA}/v1/requests", params={"project": p1}, headers=bearer(dev_p1), timeout=30),
        "P4": lambda: httpx.get(f"{GA}/v1/gateway/run-keys/e3-run-proj1/usage", headers=bearer(sa["issuers"]["proj1"]),
                                timeout=30),
        "P5": lambda: httpx.get(f"{CA}/v1/me", headers=bearer(owner_a), timeout=30),
    }
    out = []
    for i, scope, desc, exp_status, exp_rows, kind in PLAN:
        assert str(planned[i]["expected_status"]) == str(exp_status), f"plan changed for {i}"
        res = attempts[i]()
        if isinstance(res, str):
            status, code, nrows = ("conn_fail" if res.startswith("failed") else "connected"), res, ""
        else:
            status = res.status_code
            try:
                body = res.json()
                err = body.get("error") if isinstance(body, dict) else None
                code = (err.get("code") or err.get("type") or "") if isinstance(err, dict) else ""
            except ValueError:
                code = ""
            nrows = rows_of(res) if exp_rows is not None else ""
        strict = str(status) == str(exp_status)
        if exp_rows == 0:
            strict = strict and nrows == 0
        elif exp_rows == ">=1":
            strict = strict and isinstance(nrows, int) and nrows >= 1
        if kind == "attack":
            denied = (status == "conn_fail") or (isinstance(status, int) and not 200 <= status < 300) or nrows == 0
        else:
            denied = ""
        out.append({"id": i, "scope": scope, "attempt": desc, "expected_status": exp_status,
                    "expected_rows": "" if exp_rows is None else exp_rows, "observed_status": status,
                    "observed_code": code, "observed_rows": nrows, "strict_match": strict,
                    "access_denied": denied, "kind": kind})
    write_csv("e3_isolation.csv", out)
    for o in out:
        print(f"{o['id']:4} exp {str(o['expected_status']):9} got {str(o['observed_status']):9} {o['observed_code']:32} "
              f"rows={o['observed_rows']!s:3} strict={o['strict_match']} denied={o['access_denied']}")
    attacks = [o for o in out if o["kind"] == "attack"]
    controls = [o for o in out if o["kind"] == "control"]
    print(f"attacks: strict {sum(o['strict_match'] for o in attacks)}/{len(attacks)}, "
          f"denied {sum(o['access_denied'] is True for o in attacks)}/{len(attacks)}; "
          f"controls passed {sum(o['strict_match'] for o in controls)}/{len(controls)}")


if __name__ == "__main__":
    {"plan": plan, "run": run}[sys.argv[1]]()
