"""Key handling for E7/E12 (research additions, 2026-11). Stdlib only.

Reads ONLY these variables from the key file (ITPRO_KEY_FILE); every other line is skipped unparsed:
  OPENAI_API_KEY            -> GW_OPENAI_API_KEY
  ANTHROPIC_API_KEY         -> GW_ANTHROPIC_API_KEY
  AZURE_OPENAI_API_KEY      -> GW_AZURE_OPENAI_API_KEY
  AZURE_OPENAI_ENDPOINT     -> GW_AZURE_OPENAI_ENDPOINT     (a host name: treated as secret, never printed)
  AZURE_OPENAI_API_VERSION  -> GW_AZURE_OPENAI_API_VERSION
  AZURE_OPENAI_DEPLOYMENT   -> GW_AZURE_OPENAI_DEPLOYMENT

  python keyenv5.py check                  set/length per variable (never a value)
  python keyenv5.py run -- <command ...>   one command with the GW_* names set; plain provider variables removed
  python keyenv5.py leakcheck <dir> ...    files containing a key, the endpoint host, or a 12-character piece of a key
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from urllib.parse import urlparse

KEY_FILE = os.environ.get("ITPRO_KEY_FILE", "")
MAPPING = {"OPENAI_API_KEY": "GW_OPENAI_API_KEY", "ANTHROPIC_API_KEY": "GW_ANTHROPIC_API_KEY",
           "AZURE_OPENAI_API_KEY": "GW_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT": "GW_AZURE_OPENAI_ENDPOINT",
           "AZURE_OPENAI_API_VERSION": "GW_AZURE_OPENAI_API_VERSION",
           "AZURE_OPENAI_DEPLOYMENT": "GW_AZURE_OPENAI_DEPLOYMENT"}
SECRETS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY")
AMBIENT = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_API_KEY",
           "AZURE_OPENAI_API_KEY", "AZURE_AI_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY",
           "AZURE_OPENAI_ENDPOINT", "AZURE_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE",
           "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT")
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
WINDOW = 12


def _load() -> dict[str, str]:
    if not KEY_FILE:
        raise SystemExit("set ITPRO_KEY_FILE")
    out: dict[str, str] = {}
    with open(KEY_FILE, encoding="utf-8-sig") as fh:
        for line in fh:
            m = _LINE.match(line)
            if not m or m.group(1) not in MAPPING:
                continue   # other variables are never read
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[m.group(1)] = value
    return out


def check() -> None:
    vals = _load()
    for name in MAPPING:
        v = vals.get(name, "")
        print(f"{name}: {'set' if v else 'NOT SET'}" + (f", length {len(v)}" if v else ""))


def run(cmd: list[str]) -> int:
    vals = _load()
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT}
    for name, gw in MAPPING.items():
        if vals.get(name):
            env[gw] = vals[name]
    return subprocess.call(cmd, env=env)


def leakcheck(paths: list[str]) -> int:
    vals = _load()
    needles: set[str] = set()
    for name in SECRETS:
        v = vals.get(name, "")
        if v:
            needles |= {v[i:i + WINDOW] for i in range(0, max(1, len(v) - WINDOW + 1))}
    host = urlparse(vals.get("AZURE_OPENAI_ENDPOINT", "")).hostname or ""
    if host:
        needles.add(host)
        needles.add(host.split(".")[0]) if len(host.split(".")[0]) >= 6 else None
    files = hits = 0
    for root in paths:
        for dirpath, _, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                try:
                    text = open(p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                files += 1
                if any(s in text for s in needles):
                    hits += 1
                    print(f"LEAK: {p}")
    print(f"leak check: {files} files scanned, {hits} contain a key, a 12-character key piece or the endpoint host")
    return 1 if hits else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "check":
        check()
    elif sys.argv[1] == "run":
        a = sys.argv[2:]
        sys.exit(run(a[1:] if a and a[0] == "--" else a))
    elif sys.argv[1] == "leakcheck":
        sys.exit(leakcheck(sys.argv[2:]))
