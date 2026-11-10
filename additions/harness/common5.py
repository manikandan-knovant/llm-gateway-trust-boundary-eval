"""Shared helpers for the research additions E7-E12 (itpro-add-* compose projects).

Local stack keys (synthetic tenants, torn down after the run) live in ITPRO_STATE_DIR outside the repository, never in
results/. Experiments inside a tenant network get the keys they need through the environment.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent          # additions/
RESULTS = ROOT / "results"
STACK = ROOT / "stack"
STATE_DIR = Path(os.environ.get("ITPRO_STATE_DIR", str(Path(tempfile.gettempdir()) / "itpro-add-state")))

TENANTS = {name: {"project": f"itpro-add-{name}", "env": f"{name}.env", "gw": int(port + "1"), "mock": int(port + "3"),
                  "master": os.environ.get(f"ITPRO_MASTER_{name.upper().replace('-', '_')}", "")}
           for name, port in (("a-base", "450"), ("a-fix", "460"), ("load", "470"), ("e12", "480"), ("real", "490"))}

TEAMS = {
    "policy-a": {"bench-scan": ["bench"], "bench-relaxed": ["bench"], "phi-app": ["phi"], "fo-app": ["failover"],
                 "proj1": ["bench"], "proj2": ["bench", "phi"]},
    "policy-real5": {"real-app": ["real-mini", "real-gpt5", "real-sonnet"], "agent-app": ["real-mini"]},
}
ISSUERS = {"policy-a": ["proj2"], "policy-real5": ["agent-app"]}
TEAM_BUDGET = {"real-app": 40.0, "agent-app": 15.0}     # spend safety nets under the $75 cap (real tenant)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_args(tenant: str) -> list[str]:
    t = TENANTS[tenant]
    return ["docker", "compose", "-p", t["project"], "--env-file", str(STACK / t["env"]),
            "-f", str(STACK / "compose.add.yaml")]


def compose(tenant: str, *args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(compose_args(tenant) + list(args), check=check, text=True, encoding="utf-8",
                          errors="replace", capture_output=capture)


def admin(tenant: str, path: str, method: str = "POST", body: dict | None = None) -> httpx.Response:
    t = TENANTS[tenant]
    return httpx.request(method, f"http://127.0.0.1:{t['gw']}{path}", json=body, timeout=30,
                         headers={"Authorization": f"Bearer {t['master']}"})


def setup(tenant: str, policy: str) -> dict:
    state: dict = {"teams": {}, "keys": {}, "issuers": {}}
    for alias, models in TEAMS[policy].items():
        r = admin(tenant, "/team/new", body={"team_alias": alias, "models": models,
                                             "max_budget": TEAM_BUDGET.get(alias, 100.0)})
        r.raise_for_status()
        team_id = r.json()["team_id"]
        state["teams"][alias] = team_id
        k = admin(tenant, "/key/generate", body={"team_id": team_id, "key_alias": f"{alias}-key", "duration": "7d"})
        k.raise_for_status()
        state["keys"][alias] = k.json()["key"]
    for team in ISSUERS[policy]:
        alias = f"{team}-issuer"
        k = admin(tenant, "/key/generate", body={"team_id": state["teams"][team], "key_alias": alias, "duration": "7d"})
        k.raise_for_status()
        state["issuers"][team] = k.json()["key"]
        compose(tenant, "--profile", "tools", "run", "--rm", "--no-deps", "admin", "run-key-issuer", "--alias", alias,
                "--max-ttl-seconds", "3600", "--max-budget-per-run", "5", "--max-active-runs", "100", capture=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"state-{tenant}.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def load_state(tenant: str) -> dict:
    env = os.environ.get("ITPRO_STATE_" + tenant.upper().replace("-", "_"))
    if env:
        return json.loads(env)
    return json.loads((STATE_DIR / f"state-{tenant}.json").read_text(encoding="utf-8"))


def write_csv(name: str, rows: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return path


def wait_healthy(url: str, timeout_s: float = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(url)


def gateway_metric_lines(tenant: str) -> list[dict]:
    out = compose(tenant, "logs", "--no-color", "--no-log-prefix", "gateway", capture=True, check=False).stdout
    rows = []
    for line in out.splitlines():
        i = line.find('{"request"')
        if i >= 0:
            try:
                rows.append(json.loads(line[i:])["request"])
            except (ValueError, KeyError):
                continue
    return rows
