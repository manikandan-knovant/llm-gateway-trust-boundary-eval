"""Research-only LiteLLM config for the additions: as baseline/harness/research_config.py (shipped
config, only model_list replaced, Redis dropped), plus optional per-route fields:
  input_cost_per_token / output_cost_per_token   explicit prices for a model LiteLLM 1.102.1 cannot price
  extra: {...}                                   extra litellm_params (e.g. a provider-specific default)

  python research_config5.py <shipped config.yaml> <routes.yaml> <output config.yaml>
"""

from __future__ import annotations

import os
import sys

import yaml


def build(shipped: dict, routes: list[dict]) -> dict:
    out = dict(shipped)
    model_list = []
    for r in routes:
        params = {"model": r["model"], "timeout": r.get("timeout", 60), "stream_timeout": r.get("timeout", 60),
                  "max_retries": 0, "drop_params": True}
        if r.get("api_base"):
            params["api_base"] = os.path.expandvars(str(r["api_base"]))
        key = str(r.get("api_key", "research-mock-key-not-a-secret"))
        params["api_key"] = f"os.environ/{key[4:]}" if key.startswith("env:") else key
        for k in ("input_cost_per_token", "output_cost_per_token"):
            if k in r:
                params[k] = float(r[k])
        params.update(r.get("extra") or {})
        model_list.append({"model_name": r["name"], "litellm_params": params, "model_info": {"id": r["name"]}})
    out["model_list"] = model_list
    router = dict(out.get("router_settings") or {})
    for k in ("redis_host", "redis_port", "redis_password"):
        router.pop(k, None)
    out["router_settings"] = router
    return out


if __name__ == "__main__":
    shipped = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    routes = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))["routes"]
    with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(build(shipped, routes), fh, sort_keys=False)
    print(f"research config: {len(routes)} route(s)", file=sys.stderr)
