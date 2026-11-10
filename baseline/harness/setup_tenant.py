"""Seed one synthetic tenant: teams and keys matching its policy, run-key issuers, console role assignments.

  python setup_tenant.py a|b|real

Keys go to the state file outside the research folder (common.STATE_DIR); nothing is printed but aliases.
"""

from __future__ import annotations

import sys

import httpx

from common import TENANTS, create_key, create_team, dev_token, flag_issuer, save_state

TEAMS = {
    "a": {"bench-scan": ["bench"], "bench-relaxed": ["bench"], "phi-app": ["phi"], "fo-app": ["failover"],
          "proj1": ["bench"], "proj2": ["bench", "phi"]},
    "b": {"bench-scan": ["bench"], "bench-relaxed": ["bench"], "phi-app": ["phi"], "fo-app": ["failover"],
          "proj1": ["bench"], "proj2": ["bench", "phi"]},
    "real": {"real-app": ["real-mini", "real-gpt5"], "agent-app": ["real-mini"]},
}
ISSUERS = {"a": ["proj1", "proj2"], "b": ["proj1"], "real": ["agent-app"]}
# team budgets (USD): mock tenants cost $0; on the real tenant they are a spend safety net under the $25 cap
TEAM_BUDGET = {"real-app": 15.0, "agent-app": 5.0}
DEV_GROUP = {"a": "00000000-0000-0000-0000-0000000000d1"}   # synthetic group for the proj1-scoped developer


def main(tenant: str) -> None:
    t = TENANTS[tenant]
    state: dict = {"teams": {}, "keys": {}, "issuers": {}}
    for alias, models in TEAMS[tenant].items():
        team_id = create_team(tenant, alias, models, TEAM_BUDGET.get(alias, 100.0))
        state["teams"][alias] = team_id
        state["keys"][alias] = create_key(tenant, team_id, f"{alias}-key")
    for team in ISSUERS[tenant]:
        alias = f"{team}-issuer"
        state["issuers"][team] = create_key(tenant, state["teams"][team], alias)
        flag_issuer(tenant, alias, max_ttl=3600, max_budget=2.0, max_runs=50)
    if tenant in DEV_GROUP:
        owner = dev_token(tenant, [t["owner_group"]])
        r = httpx.post(f"http://127.0.0.1:{t['console']}/v1/role-assignments", timeout=15,
                       headers={"Authorization": f"Bearer {owner}"},
                       json={"group_object_id": DEV_GROUP[tenant], "role": "developer", "scope_type": "project",
                             "scope_id": state["teams"]["proj1"]})
        r.raise_for_status()
        state["dev_group"] = DEV_GROUP[tenant]
    save_state(tenant, state)
    print(f"tenant {tenant}: teams {sorted(state['teams'])}, issuers {sorted(state['issuers'])}"
          + (", proj1 developer role assigned" if tenant in DEV_GROUP else ""))


if __name__ == "__main__":
    main(sys.argv[1])
