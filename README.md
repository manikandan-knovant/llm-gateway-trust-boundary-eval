# LLM gateway as a trust boundary: measurement harness, synthetic data and results

**Author:** Manikandan Elumalai, Independent Researcher. ORCID [0009-0009-0049-6695](https://orcid.org/0009-0009-0049-6695)
**Licences:** code under MIT ([LICENSE](LICENSE)); data and results under CC BY 4.0 ([DATA_LICENSE](DATA_LICENSE)).
**How to cite:** [CITATION.cff](CITATION.cff).

This package holds the evidence behind an article about an LLM gateway that sits between applications (including
agents) and model providers in regulated healthcare. Below it is called **the gateway**. The gateway is built on
**LiteLLM 1.102.1** (the PyPI wheel `litellm[proxy]==1.102.1`) plus its own hooks. These enforce:
- a per-tenant policy;
- data-classification-aware routing (routes with and without a BAA);
- reversible PHI/PII pseudonymization (Presidio plus regex);
- per-run agent keys with budgets;
- typed errors.

The gateway's source code is **not** included. The package holds only the harness, the synthetic-data generators, the
stack configuration used to run them, and the results.

## Builds measured

| Name in this package | Commit | What it is |
|---|---|---|
| **baseline build** | `6fb0334` | The gateway as measured on 2026-11-09. All baseline numbers (E1–E6) come from this build |
| **fixed build** | `3c468b0` | The baseline plus three fixes found by the baseline run: typed upstream errors, fast failover on 429, and PHI-detection fixes |
| stock LiteLLM | PyPI 1.102.1 | The same LiteLLM with none of the gateway's hooks (E10 stock arm, E13). Every file was checked against the wheel's `RECORD` hashes: 3,319 files, 0 modified |

## What was measured

| Exp. | Question | Upstream | Report |
|---|---|---|---|
| E1 | Latency the gateway adds (mock, 50 ms) | mock | `baseline/02_results.md` |
| E1-real | The same with a real model (GPT-5 mini) | OpenAI | `baseline/02_results.md` |
| E2 | Failover when the primary fails and the only fallback has no BAA | mock | `baseline/02_results.md`, `baseline/04_after_fixes.md` |
| E3 | Isolation between tenants and projects | mock | `baseline/02_results.md` |
| E4 / E4-real | PHI detection, masking and restore (synthetic prompts; real models) | mock / OpenAI | `baseline/02_results.md`, `baseline/04_after_fixes.md` |
| E5 | Does an evaluation gate exist? (static search of the product source; script and output not included because they quote that source) | – | `baseline/02_results.md` |
| E6 | Agent loop with per-run keys | OpenAI | `baseline/02_results.md` |
| E7 | Restore across vendors (GPT-5 mini, GPT-5, Claude Sonnet 5.5) | real | `additions/05_additions.md` |
| E8 | HIPAA Safe Harbor coverage: 21 identifier categories, 1,500 planted identifiers plus PHI-free notes | mock | `additions/05_additions.md` |
| E9 | Load: concurrency 1–128, masking on/off, cross-request mix-ups | mock | `additions/05_additions.md` |
| E10 | Stock LiteLLM vs the gateway under 503 / 429 / timeout faults | mock | `additions/05_additions.md` |
| E11 | External benchmark ASQ-PHI (not redistributed; see below) | mock | `additions/05_additions.md` |
| E12 | 30-turn agent runs: restore, detection cache, sticky misses, budget overshoot, key revocation | mock + OpenAI | `additions/05_additions.md` |
| E13 | Stock LiteLLM with its own documented tag-based routing, under the E10 faults | mock | `additions/05_additions.md` |
| E14 | Scale-out (1, 2 and 4 gateway workers), a 30-minute soak, and a real-provider spot check at concurrency 8 (GPT-5 mini) | mock + OpenAI | `additions/05_additions.md` |

**Headline fault result (E10, E13).** The primary route fails and the only fallback has no BAA.
- **The gateway:** sent 0 of 50 PHI-labelled requests to that fallback in every fault (503, 429, timeout).
- **Stock LiteLLM 1.102.1 with a plain fallback:** sent 90% (503, 429) and 38% (timeout).
- **Stock LiteLLM with its own tag routing**, tag in the request or on the key: still sent 36–90%. A tag that matches nothing in the fallback group falls back to that group's `default` deployment.
- **Only the documented required-tag form `&phi` on the key blocked it (0/50).** That needs an Enterprise-gated key field, set through key metadata, and returns a 401 whose body exposes internals.

**Scale-out and soak (E14),** masking on, measured on one host (Intel Core Ultra 9 185H, Docker Desktop on WSL2, no
container limits; details in `additions/results/e14_host.json`):
- 1 gateway worker reaches about 68 req/s, and 2 or 4 workers about 113 req/s. 4 workers were no faster than 2.
- Over 30 minutes at concurrency 32 (4 workers), 151,124 requests ran with 0 errors and 0 cross-request mix-ups.
- Gateway and detector memory grew about 12–13% during that run and had not levelled off when it ended.
- Against GPT-5 mini at concurrency 8, the gateway added p50 25.0 ms and p95 36.2 ms, with 1,471 of 1,471 calls
  restored exactly.

Total real-provider spend was **$0.873576**: $0.649287 for E7 and E12, and $0.224289 for E14. The baseline run's real-provider spend is in
`baseline/02_results.md`. Every other experiment used a local mock upstream and cost $0.

## Layout

```
baseline/      02_results.md (baseline report), 04_after_fixes.md (re-check on the fixed build)
  harness/     E1–E6 scripts, synthetic prompt generator, mock upstream, body recorder, stats, unit tests
  stack/       compose file, tenant policies, route lists; *.env.example templates
  results/     CSV results of the baseline run
recheck/
  results/     CSV results of the re-check on the fixed build (04_after_fixes.md); same harness as baseline/
additions/     05_additions.md (E7–E14 report)
  harness/     E7–E14 scripts, Safe Harbor note generator (notes5.py), mock, recorder, stats, unit tests
  stack/       compose files (gateway tenants, stock LiteLLM, stock LiteLLM with tag routing), configs, *.env.example
  results/     CSV / JSON / JSON-lines results of E7–E14
```

## Data

- **Synthetic data only.** Every identifier is generated:
  - phone numbers in 555-01xx;
  - e-mail domains `example.com/.org/.net`;
  - IP addresses from the documentation ranges;
  - SSNs outside issued ranges;
  - Luhn-valid test card numbers.

  The generators are deterministic, so a seed reproduces the exact text:
  - `baseline/harness/synthetic_prompts.py`;
  - `additions/harness/notes5.py`.
- **No request or response bodies are stored.** Results keep scores, statuses, latencies, counts and error bodies
  returned by the gateway or LiteLLM. Raw request logs from the mock or the recorder are not included.
- **ASQ-PHI (E11)** is the external benchmark "An Adversarial Synthetic Benchmark for Clinical De-Identification and
  Search Utility":
  - Mendeley Data, DOI [10.17632/csz5dzp7nx.1](https://doi.org/10.17632/csz5dzp7nx.1);
  - article: Data in Brief, DOI [10.1016/j.dib.2026.112586](https://doi.org/10.1016/j.dib.2026.112586).

  **It is not included.** Download it yourself and check the file:
  - `synthetic_clinical_queries.txt`;
  - SHA-256 `cf00e424b8d2347d019f9f34e2ad1510cb4d853605410f8314bef44df8021fc8`.

  The E11 results hold query indices, identifier types, statuses, counts and value-shape classes, never query text.

## Re-running

Requirements:
- Docker with Compose v2;
- Python 3.12 with `httpx` and `pytest` on the host;
- for the real-provider experiments, provider API keys.

Unit tests (no network, no Docker):

```
cd baseline/harness  && python -m pytest -q tests
cd additions/harness && python -m pytest -q tests
```

### Against stock LiteLLM (E10 stock arm, E13)

These runs need nothing from the gateway. Any image with `litellm[proxy]==1.102.1` works, or another LiteLLM version
to compare. Set the image in `additions/stack/compose.stock.yaml` or `compose.e13.yaml`, set a local master key and
run, for example:

```
cd additions/stack
export E13_MASTER_KEY=<a local LiteLLM master key>      # LiteLLM requires it to start with "sk-"
docker compose -p itpro-add-e13 -f compose.e13.yaml up -d --wait
for v in A B-other B-none C-key; do for s in 503 429 timeout; do
  docker compose -p itpro-add-e13 -f compose.e13.yaml run --rm harness e13_tags.py run $v $s; done; done
cd ../harness && python e13_tags.py analyse
docker compose -p itpro-add-e13 -f ../stack/compose.e13.yaml down -v
```

### Against another LiteLLM-based gateway

The harness talks to the gateway only over HTTP:
- OpenAI-compatible `/v1/chat/completions`;
- LiteLLM's `/key/*` and `/team/*` management endpoints.

To run it against a different LiteLLM-based gateway:

1. **Build an image** of your gateway and tag it. The compose files expect `itpro-gateway:<tag>`, chosen with
   `GW_IMAGE` in `additions/stack/*.env.example`; the baseline compose file names the tag directly. The container must
   start LiteLLM from a config file.
2. **Config.** `research_config.py` / `research_config5.py` take your gateway's shipped LiteLLM config (`/app/config.yaml`
   in the image), keep its router, callbacks and general settings, and replace only `model_list` with the routes in
   `stack/routes-*.yaml`. Each route points at the mock upstream (`mock5.py` / `mock_upstream.py`), or at the recorder
   for real providers.
3. **Gateway-specific inputs** are the parts to adapt:
   - the tenant policy files (`stack/policy-*.yaml`), consumed by the gateway's hooks;
   - the request header `x-gateway-data-classification`;
   - the response header `x-gateway-served-route`;
   - the per-run key endpoint used by E6 / E12.

   If your gateway has no such feature, the matching measurement is not applicable. Everything else (latency,
   detection and restore scored on what the upstream received, fault behaviour, load, mix-ups) is measured from the
   outside.
4. **Environment.** Copy each `*.env.example` to `*.env` and fill in local values: master key, database passwords,
   tenant id (`<tenant-id-…>` placeholders), ports. In the stack files, provider API bases are placeholders
   (`<OPENAI_API_BASE>`, `<ANTHROPIC_API_BASE>`). Master keys are read from the environment:
   - `ITPRO_MASTER_A` / `_B` / `_REAL`;
   - `ITPRO_MASTER_A_BASE`, `_A_FIX`, `_LOAD`, `_E12`, `_REAL`;
   - `ITPRO_MASTER_E14`, `ITPRO_MASTER_E14REAL`;
   - `STOCK_MASTER_KEY`, `E13_MASTER_KEY`.
5. **Real providers.** `keyenv.py` / `keyenv5.py` read **only** the named provider variables from the file in
   `ITPRO_KEY_FILE`. They pass the keys to a single command as `GW_*` variables and never print or write them. The
   recorder stores request and response bodies, never headers.
6. **Run commands.** The exact sequence for each experiment is in the "Reproduce" sections of `baseline/02_results.md`
   and `additions/05_additions.md`. Results land in the sibling `results/` folder.

Two of the gateway's own helpers are not included: its local token issuer and its tenant-admin script. They are used
only by E3 and by tenant seeding. The compose files reference them through `GATEWAY_LOCAL_ISSUER_DIR` and
`GATEWAY_ADMIN_DIR`.

## Changes made for publication

The scripts and configurations are those that produced the results, with these edits for publication:
- product, company and path names replaced by neutral terms ("the gateway", `baseline/`, `additions/`);
- the E10 gateway arm renamed `gateway`, in file names, arm values and markers;
- tenant ids replaced by `<tenant-id-…>` placeholders;
- provider hosts in stack files replaced by placeholders;
- local throw-away master-key literals replaced by environment variables;
- `.env` files replaced by `.env.example` templates.

The numbers in the reports were not changed.
