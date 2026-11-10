"""Research-only LiteLLM config (baseline build 2026-11-09), derived at container start from the SHIPPED config.

Keeps everything from the shipped deploy/litellm/config.yaml (router_settings, retry policy, cooldown, callbacks,
litellm_settings, general_settings) and replaces ONLY model_list with the research routes in a routes file.
The shipped file is read, never written. Redis settings are dropped (one replica, no Redis), as the product's own
local stack does (deploy/local/make_local_config.py).

  python research_config.py <shipped config.yaml> <routes.yaml> <output config.yaml>

routes.yaml:
  routes:
    - name: bench--mock          # route group (must match the tenant policy)
      model: openai/mock-model
      api_base: http://mock-upstream:8080/bench/v1
      api_key: literal-placeholder          # or env:GW_OPENAI_API_KEY -> "os.environ/GW_OPENAI_API_KEY"
      timeout: 60
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
        # a key held in an environment variable is referenced, never copied into the file
        params["api_key"] = f"os.environ/{key[4:]}" if key.startswith("env:") else key
        model_list.append({"model_name": r["name"], "litellm_params": params, "model_info": {"id": r["name"]}})
    out["model_list"] = model_list
    router = dict(out.get("router_settings") or {})
    for k in ("redis_host", "redis_port", "redis_password"):
        router.pop(k, None)
    out["router_settings"] = router
    return out


def main(src: str, routes_path: str, dst: str) -> None:
    with open(src, encoding="utf-8") as fh:
        shipped = yaml.safe_load(fh)
    with open(routes_path, encoding="utf-8") as fh:
        routes = yaml.safe_load(fh)["routes"]
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(build(shipped, routes), fh, sort_keys=False)
    print(f"research config: {len(routes)} route(s) from {os.path.basename(routes_path)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
