"""Key handling for the real-provider sample (baseline build 2026-11-09). Stdlib only.

  python keyenv.py check                      length check only (prints the length, never the value)
  python keyenv.py run -- <command ...>       runs ONE command with GW_OPENAI_API_KEY set from the key file
  python keyenv.py leakcheck <dir> [...]      scans files for the key or any 12-character piece of it; prints counts

Rules:
- reads ONLY the line that assigns OPENAI_API_KEY in KEY_FILE; every other line is skipped unparsed;
- never prints, logs or writes the value; the child gets it as GW_OPENAI_API_KEY only;
- the child's environment has every provider-default credential variable REMOVED (the gateway refuses them, and
  nothing must pick up an ambient key).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

KEY_FILE = os.environ.get("ITPRO_KEY_FILE", "")   # path to the .env file that holds OPENAI_API_KEY (required)
NAME = "OPENAI_API_KEY"
_LINE = re.compile(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*$")
AMBIENT = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_API_KEY",
           "AZURE_OPENAI_API_KEY", "AZURE_AI_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY")
WINDOW = 12


def _load() -> str:
    if not KEY_FILE:
        raise SystemExit("set ITPRO_KEY_FILE to the .env file that holds OPENAI_API_KEY")
    with open(KEY_FILE, encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.lstrip().startswith(("OPENAI_API_KEY", "export OPENAI_API_KEY")):
                continue   # other variables are never parsed
            m = _LINE.match(line)
            if m:
                value = m.group(1)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                if value:
                    return value
    raise SystemExit(f"{NAME} not found in the key file")


def check() -> None:
    print(f"key check: {NAME} found, length {len(_load())}")


def run(cmd: list[str]) -> int:
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT}
    env["GW_OPENAI_API_KEY"] = _load()
    return subprocess.call(cmd, env=env)


def leakcheck(paths: list[str]) -> int:
    key = _load()
    pieces = {key[i:i + WINDOW] for i in range(0, max(1, len(key) - WINDOW + 1))}
    files = hits = 0
    for root in paths:
        for dirpath, _, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except OSError:
                    continue
                files += 1
                if key in text or any(piece in text for piece in pieces):
                    hits += 1
                    print(f"LEAK: {p}")
    print(f"leak check: {files} files scanned, {hits} contain the key or a {WINDOW}-character piece of it")
    return 1 if hits else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "check":
        check()
    elif sys.argv[1] == "run":
        args = sys.argv[2:]
        if args and args[0] == "--":
            args = args[1:]
        sys.exit(run(args))
    elif sys.argv[1] == "leakcheck":
        sys.exit(leakcheck(sys.argv[2:]))
    else:
        raise SystemExit(__doc__)
