"""E9 resource sampler (host): `docker stats --no-stream` for the load tenant's gateway, PHI detector and mock, one JSON
line per sample with a Unix timestamp, until the stop file exists.

  python e9_sampler.py <out.jsonl> <stop file>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

CONTAINERS = {"gateway": "itpro-add-load-gateway-1", "presidio": "itpro-add-load-presidio-analyzer-1",
              "mock": "itpro-add-load-mock-upstream-1"}


def main(out: str, stop: str) -> None:
    with open(out, "a", encoding="utf-8") as fh:
        while not os.path.exists(stop):
            t = time.time()
            res = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}",
                                  *CONTAINERS.values()], capture_output=True, text=True)
            row = {"t": round(t, 3)}
            for line in res.stdout.splitlines():
                name, cpu, mem = (line.split(";") + ["", "", ""])[:3]
                for key, cname in CONTAINERS.items():
                    if name == cname:
                        row[key] = {"cpu": cpu, "mem": mem}
            fh.write(json.dumps(row) + "\n")
            fh.flush()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
