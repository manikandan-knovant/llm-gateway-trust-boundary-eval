"""Shared helpers for the baseline build 2026-11-09 harness (host side and in-network side).

Local stack keys (synthetic tenants, torn down after the run) are kept in a STATE file OUTSIDE the research folder
(ITPRO_STATE_DIR), never in results. Experiments inside the network get the keys they need via environment.
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

ROOT = Path(__file__).resolve().parent.parent          # baseline
RESULTS = ROOT / "results"
STACK = ROOT / "stack"
STATE_DIR = Path(os.environ.get("ITPRO_STATE_DIR", str(Path(tempfile.gettempdir()) / "itpro-state")))

TENANTS = {
    "a": {"project": "itpro-a", "env": "tenant-a.env", "gw": 4101, "console": 4111, "issuer": 4121, "mock": 4131,
          "master": os.environ.get("ITPRO_MASTER_A", ""), "aud": "research-console-a",
          "tid": "00000000-0000-0000-0000-00000000000a", "owner_group": "00000000-0000-0000-0000-0000000000a1"},
    "b": {"project": "itpro-b", "env": "tenant-b.env", "gw": 4201, "console": 4211, "issuer": 4221, "mock": 4231,
          "master": os.environ.get("ITPRO_MASTER_B", ""), "aud": "research-console-b",
          "tid": "00000000-0000-0000-0000-00000000000b", "owner_group": "00000000-0000-0000-0000-0000000000b1"},
    "real": {"project": "itpro-real", "env": "tenant-real.env", "gw": 4301, "console": 4311, "issuer": 4321,
             "mock": 4331, "master": os.environ.get("ITPRO_MASTER_REAL", ""), "aud": "research-console-real",
             "tid": "00000000-0000-0000-0000-00000000000c", "owner_group": "00000000-0000-0000-0000-0000000000c1"},
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_args(tenant: str) -> list[str]:
    t = TENANTS[tenant]
    return ["docker", "compose", "-p", t["project"], "--env-file", str(STACK / t["env"]),
            "-f", str(STACK / "compose.tenant.yaml")]


def compose(tenant: str, *args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(compose_args(tenant) + list(args), check=check, text=True, encoding="utf-8",
                          errors="replace", capture_output=capture)


def admin(tenant: str, path: str, method: str = "POST", body: dict | None = None) -> httpx.Response:
    t = TENANTS[tenant]
    return httpx.request(method, f"http://127.0.0.1:{t['gw']}{path}", json=body, timeout=30,
                         headers={"Authorization": f"Bearer {t['master']}"})


def create_team(tenant: str, alias: str, models: list[str], max_budget: float = 100.0) -> str:
    r = admin(tenant, "/team/new", body={"team_alias": alias, "models": models, "max_budget": max_budget})
    r.raise_for_status()
    return r.json()["team_id"]


def create_key(tenant: str, team_id: str, alias: str, metadata: dict | None = None, **extra) -> str:
    body = {"team_id": team_id, "key_alias": alias, "duration": "7d", **extra}
    if metadata:
        body["metadata"] = metadata
    r = admin(tenant, "/key/generate", body=body)
    r.raise_for_status()
    return r.json()["key"]


def flag_issuer(tenant: str, alias: str, max_ttl: int = 3600, max_budget: float = 2.0, max_runs: int = 50) -> None:
    compose(tenant, "--profile", "tools", "run", "--rm", "--no-deps", "admin", "run-key-issuer", "--alias", alias,
            "--max-ttl-seconds", str(max_ttl), "--max-budget-per-run", str(max_budget),
            "--max-active-runs", str(max_runs), capture=True)


def dev_token(tenant: str, groups: list[str], aud: str | None = None, tid: str | None = None) -> str:
    t = TENANTS[tenant]
    r = httpx.post(f"http://127.0.0.1:{t['issuer']}/token", timeout=10,
                   json={"aud": aud or t["aud"], "groups": groups, "tid": tid or t["tid"], "exp_in": 3600})
    r.raise_for_status()
    return r.json()["access_token"]


def save_state(tenant: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"state-{tenant}.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def load_state(tenant: str) -> dict:
    env = os.environ.get(f"ITPRO_STATE_{tenant.upper()}")
    if env:
        return json.loads(env)
    return json.loads((STATE_DIR / f"state-{tenant}.json").read_text(encoding="utf-8"))


def write_csv(name: str, rows: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def wait_healthy(url: str, timeout_s: float = 180) -> None:
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
    """The gateway's own per-call metadata lines (metrics hook, logger gateway.request) from its container log.
    Read in memory; only the 'request' objects are returned (no other log content is kept)."""
    out = compose(tenant, "logs", "--no-color", "--no-log-prefix", "gateway", capture=True, check=False).stdout
    rows = []
    for line in out.splitlines():
        i = line.find('{"request"')
        if i < 0:
            continue
        try:
            rows.append(json.loads(line[i:])["request"])
        except (ValueError, KeyError):
            continue
    return rows
