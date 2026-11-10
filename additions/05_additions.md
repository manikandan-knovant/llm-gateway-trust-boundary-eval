# Research additions E7–E14 (2026-11-09)

**Builds compared.**

| | Baseline | Fix |
|---|---|---|
| Image | `itpro-gateway:baseline-2026-11-09` | `itpro-gateway:fix-3c468b0` |
| Commit | `6fb0334`, the article baseline | `3c468b0`, branch `fix/research-findings-2026-11`, not merged |
| LiteLLM | 1.102.1 | 1.102.1 |

**Base commit.** The baseline build (`6fb0334`) was synced from an upstream code base at `a249179`. That upstream is now at `a0527b6`, **16 commits ahead** (it includes, for example, an audit hash chain). The upstream repository was not changed by this work.

**Where things are.**
- **Stacks:** compose projects `itpro-add-*`, all torn down at the end (0 containers, volumes or networks left).
- **Data:** synthetic only. The one external dataset (E11) was used unchanged and is not copied into this repository.

**Conventions.**
- Every percentage carries its n and a Wilson 95% interval.
- Real-provider spend is in §3.
- Exact reproduction commands are in the scripts' docstrings (`harness/`).

**E7 deviation.** The key file's four `AZURE_OPENAI_*` lines exist but their values are empty, so **Azure OpenAI was not run**. Per the instructions, only that provider was stopped; E7 ran on the other three.

**Contents**
1. [Results](#1-results): [E7](#e7--real-provider-restore-across-vendors-fix-build), [E8](#e8--safe-harbor-coverage-both-builds-mock-0), [E9](#e9--load-fix-build-mock-with-a-fixed-200-ms-0), [E10](#e10--stock-litellm-vs-the-gateway-under-the-e2-faults-mock-0), [E11](#e11--independent-external-benchmark-asq-phi-both-builds-mock-0), [E12](#e12--long-agent-runs-fix-build), [E13](#e13--stock-litellm-with-its-own-tag-based-routing-under-the-e10-faults-mock-0), [E14](#e14--scale-out-soak-and-a-real-provider-spot-check-fix-build)
2. [Surprising or worse than expected](#2-surprising-or-worse-than-expected)
3. [Spend](#3-spend)
4. [Tests and fingerprint](#4-tests-and-fingerprint)
5. [Files created](#5-files-created)

---

## 1. Results

### E7 — Real-provider restore across vendors (fix build)

**Models:**

| Provider | Model ID | Parameters |
|---|---|---|
| OpenAI | `gpt-5-mini-2025-08-07` | `reasoning_effort: minimal`, as in E4-real |
| OpenAI | `gpt-5-2025-08-07` | same |
| Anthropic | `claude-sonnet-5-5` | `thinking: {type: "between_tools"}`, which turns thinking off unless tools are used |
| Azure OpenAI | — | not run: `AZURE_OPENAI_*` values empty |

- **Why Sonnet's thinking setting.** In a smoke test, Sonnet 5.5 with only `max_tokens` spent the whole 200-token budget on its default adaptive thinking and returned a truncated answer.
- **Sonnet pricing.** `claude-sonnet-5-5` is not in LiteLLM 1.102.1's price map, so its route is priced explicitly at $2 / $10 per million input / output tokens.

**Prompts**, 200 per provider:
- **Set A:** the E4-real set (seed 4040), 100-token answers.
- **Set B:** the first 100 PHI notes of E8 (seed 8), 400-token answers.

All routes have no BAA. Each call went through a body recorder, which shows what the provider received, the raw answer and the provider's own time.

| Measure | GPT-5 mini | GPT-5 | Claude Sonnet 5.5 | Baseline E4-real (GPT-5 mini / GPT-5) |
|---|---|---|---|---|
| Placeholders carried back intact (both sets) | 723/723 = 100% [99.5, 100] | 723/723 = 100% [99.5, 100] | 723/723 = 100% [99.5, 100] | 373/373 each |
| Placeholders altered or dropped | 0 | 0 | 0 | 0 |
| Exact restore, set A | 100/100 = 100% [96.3, 100] | 100/100 = 100% [96.3, 100] | 100/100 = 100% [96.3, 100] | 100/100 each |
| Exact restore, set B | 99/100 = 99.0% [94.6, 99.8] | 100/100 = 100% [96.3, 100] | 100/100 = 100% [96.3, 100] | — |
| Exact restore, prompts with every identifier masked | 104/104 = 100% [96.4, 100] | 104/104 = 100% [96.4, 100] | 104/104 = 100% [96.4, 100] | 63/63 each |
| **Doubled MRN label ("MRN MRN"), set A** | **0/95 = 0.0% [0.0, 3.9]** | **0/95 = 0.0% [0.0, 3.9]** | **0/95 = 0.0% [0.0, 3.9]** | **37/75 = 49.3% / 20/75 = 26.7%** |
| Identifiers masked before the provider, set A / set B | 396/400 = 99.0% [97.5, 99.6] / 327/500 = 65.4% [61.1, 69.4] | same | same | 360/400 = 90.0% (set A) |
| Gateway-added latency, median / p95 (ms) | 23.9 / 31.3 | 14.1 / 19.2 | 14.2 / 18.2 | — |
| Spend (USD) | 0.0331 | 0.1761 | 0.3352 | — |

- **The doubled-label fix holds on real models.** On the same E4-real prompts, GPT-5 mini fell from 49.3% to 0/95 and GPT-5 from 26.7% to 0/95, and Claude Sonnet 5.5 is at 0/95 too.
- **The one restore failure (GPT-5 mini, set B) is not a placeholder failure.** A space-separated SSN was only partly masked, because the SSN rule expects dashes, and the model reformatted the part left in clear.
- **Gateway-added latency** is client latency minus the provider's own time as the recorder measured it. GPT-5 mini ran first after start-up, which likely explains its higher figure.

### E8 — Safe Harbor coverage (both builds, mock, $0)

**Setup.** 400 synthetic notes (seed 8) were sent through tenant A on the non-BAA route and scored on what the upstream received.
- **PHI notes:** 300 notes, each with 5 identifiers from 5 different categories, 1,500 identifiers in all. That is 71 or 72 per category key, with formats rotating.
- **PHI-free notes:** 100, clearly marked (`neg*`).

**How the status column is decided.**
- **handled:** a detector in code targets the category, and the fix build's recall has a lower 95% bound of at least 90%.
- **partially:** a detector exists but recall is below that.
- **not in scope:** no detector in code (photos, biometric IDs, device IDs, ages over 89). These rows still show what was measured, since the NER model sometimes masks such values by chance.

| # | Category | Detector in code | Recall, baseline | Recall, fix | Wrong type (base → fix) | Status (fix) |
|---|---|---|---|---|---|---|
| 1 | Names | NER PERSON | 37/72 = 51.4% [40.1, 62.6] | 37/72 = 51.4% [40.1, 62.6] | 0 → 0 | partially |
| 2 | Geographic, all four | | 190/288 = 66.0% [60.3, 71.2] | 190/288 = 66.0% [60.3, 71.2] | 29 → 29 | partially |
| 2 | — street address | NER LOCATION | 20/72 = 27.8% [18.8, 39.0] | 20/72 = 27.8% [18.8, 39.0] | 6 → 6 | partially |
| 2 | — city | NER LOCATION | 72/72 = 100% [94.9, 100] | 72/72 = 100% [94.9, 100] | 0 → 0 | handled |
| 2 | — county | NER LOCATION | 58/72 = 80.6% [70.0, 88.0] | 58/72 = 80.6% [70.0, 88.0] | 1 → 1 | partially |
| 2 | — ZIP | regex (needs "zip"/"postal" nearby) | 40/72 = 55.6% [44.1, 66.5] | 40/72 = 55.6% [44.1, 66.5] | 22 → 22 | partially |
| 3 | Dates (except year) | regex + NER | 63/72 = 87.5% [77.9, 93.3] | 66/72 = 91.7% [83.0, 96.1] | 0 → 0 | partially |
| 3 | Ages over 89 | none | 47/72 = 65.3% [53.8, 75.2] | 47/72 = 65.3% [53.8, 75.2] | 47 → 47 | not in scope (masked by chance, as DATE) |
| 4 | Telephone | regex | 72/72 = 100% [94.9, 100] | 72/72 = 100% [94.9, 100] | 2 → 2 | handled |
| 5 | Fax | regex (phone shape) | 72/72 = 100% [94.9, 100] | 72/72 = 100% [94.9, 100] | 0 → 0 | handled |
| 6 | E-mail | regex | 48/71 = 67.6% [56.1, 77.3] | 48/71 = 67.6% [56.1, 77.3] | 0 → 0 | partially |
| 7 | SSN | regex (dashed form) | 25/71 = 35.2% [25.1, 46.8] | 25/71 = 35.2% [25.1, 46.8] | 1 → 1 | partially |
| 8 | MRN (employer-specific label) | synthetic rule (OQ-062) | 24/71 = 33.8% [23.9, 45.4] | **48/71 = 67.6% [56.1, 77.3]** | 0 → 2 | partially |
| 9 | Health plan / member ID (employer-specific) | synthetic rule (OQ-062) | 23/71 = 32.4% [22.7, 43.9] | **38/71 = 53.5% [42.0, 64.6]** | 5 → 2 | partially |
| 10 | Account numbers | NER bank / card / IBAN | 49/71 = 69.0% [57.5, 78.6] | 49/71 = 69.0% [57.5, 78.6] | 2 → 2 | partially |
| 11 | Certificate / licence | NER driver's licence / medical / passport | 47/71 = 66.2% [54.6, 76.1] | 47/71 = 66.2% [54.6, 76.1] | 2 → 2 | partially |
| 12 | Vehicle | regex VIN (needs "vin" nearby); no plate rule | 36/71 = 50.7% [39.3, 62.0] | 36/71 = 50.7% [39.3, 62.0] | 3 → 3 | partially |
| 13 | Device IDs / serials | none | 1/71 = 1.4% [0.2, 7.6] | 1/71 = 1.4% [0.2, 7.6] | 1 → 1 | not in scope |
| 14 | URLs | regex (http/https only) | 51/71 = 71.8% [60.5, 81.0] | 51/71 = 71.8% [60.5, 81.0] | 3 → 3 | partially |
| 15 | IP addresses | regex IPv4 | 71/71 = 100% [94.9, 100] | 71/71 = 100% [94.9, 100] | 3 → 3 | handled |
| 16 | Biometric IDs | none | 6/71 = 8.5% [3.9, 17.2] | 6/71 = 8.5% [3.9, 17.2] | 6 → 6 | not in scope |
| 17 | Full-face photos | none (non-text parts are refused on non-BAA routes) | not measured (not text) | not measured | — | not in scope |
| 18 | Other unique numbers: claim number (employer-specific) | synthetic rule (OQ-062) | 34/71 = 47.9% [36.7, 59.3] | **57/71 = 80.3% [69.6, 87.9]** | 16 → 3 | partially |
| | **All planted identifiers** | | **896/1500 = 59.7% [57.2, 62.2]** | **961/1500 = 64.1% [61.6, 66.5]** | **120 → 106** | |

**Recall by format** (fix build; full table in `results/e8_by_format.csv`). Every format a rule targets scores 100% (n = 10–36 each). The misses are formats with no rule:

| Category | Format | Recall |
|---|---|---|
| Names | "ALVAREZ, MARIA" | 0/24 |
| Names | "Maria A." | 13/24 |
| SSN | with spaces | 0/24 |
| SSN | with no separator | 1/23 |
| MRN | "Chart #" | 0/12 |
| MRN | "MR#" | 0/11 |
| Member ID | insurance ID ("XQZ…") | 1/18 |
| Member ID | Medicaid ID | 1/17 |
| Account numbers | plain bank account | 2/24 |
| Licences | passport number | 1/18 |
| Vehicles | licence plates | 0/35 |
| ZIP codes | without a "zip" label | 4/36 |
| URLs | without a scheme | 3/23 |
| E-mail | obfuscated ("name at example dot net") | 0/23 |
| Claim numbers | "Claim ref …" | 3/17 |
| Dates | dotted ("03.14.1962") | 4/10 |

**Over-masking:**

| | Baseline | Fix |
|---|---|---|
| PHI-free notes with anything masked | 99/100 = 99.0% [94.6, 99.8] (305 spans, all DATE) | 65/100 = 65.0% [55.3, 73.6] (105 spans, all DATE) |
| PHI notes with a non-identifier span masked | 150/300 = 50.0% [44.4, 55.6] | 73/300 = 24.3% [19.8, 29.5] |

Of the 20 PHI-free sentence types:
- **Masked on both builds:** "54-year-old", "72-year-old", "2019", "58" (a lab value), "3 months", "six weeks", "this season".
- **Masked on the baseline only:** weekdays, "next week", "today", "tomorrow", "daily", "2 days ago" and similar.

**Wrong placeholder type.** Over all 1,500 identifiers this fell from 120 (baseline) to 106 (fix). The largest share is ages over 89 masked as `DATE` (47), and ZIP codes masked as `DATE` or `LOCATION` (22). The fix report's 11 → 28 was measured on the MRN-only E4 set; here MRN went 0 → 2.

### E9 — Load (fix build, mock with a fixed 200 ms, $0)

**Setup.** Closed loop with 60 s per level. The gateway runs as shipped: one worker process.
- **Masking ON:** route without a BAA, so every call is scanned, masked and restored.
- **Masking OFF:** a relaxed team on a BAA route, so no scan.

Gateway-added latency is client latency minus the mock's own service time, which the gateway passed on as a provider header on every response.

| Masking | Concurrency | Throughput (req/s) | Added p50 / p95 / p99 (ms) | Errors | Gateway CPU mean / max (% of one core) | Gateway memory mean / max (MiB) | Detector CPU mean / max (%) |
|---|---|---|---|---|---|---|---|
| off | 1 | 4.6 | 13.5 / 17.4 / 24.9 | 0/279 [0, 1.4%] | 11 / 29 | 465 / 480 | 1 / 8 |
| off | 2 | 9.0 | 22.4 / 27.4 / 32.5 | 0/538 [0, 0.7%] | 13 / 32 | 483 / 499 | 1 / 7 |
| off | 4 | 16.6 | 39.1 / 49.4 / 71.1 | 0/1000 [0, 0.4%] | 23 / 48 | 508 / 528 | 1 / 9 |
| off | 8 | 28.6 | 76.9 / 96.8 / 138.2 | 0/1715 [0, 0.2%] | 37 / 61 | 530 / 537 | 1 / 8 |
| off | 16 | 47.6 | 133.1 / 212.6 / 459.6 | 0/2872 [0, 0.1%] | 61 / 96 | 545 / 562 | 1 / 8 |
| off | 32 | 66.6 | 275.3 / 459.2 / 684.2 | 0/4014 [0, 0.1%] | 81 / 113 | 565 / 576 | 1 / 10 |
| off | 64 | 80.3 | 575.8 / 916.5 / 1005.2 | 0/4860 [0, 0.1%] | 100 / 127 | 577 / 592 | 2 / 9 |
| off | 128 | 84.0 | 1233.0 / 1649.6 / 3352.5 | 0/5170 [0, 0.1%] | 106 / 131 | 596 / 614 | 2 / 9 |
| on | 1 | 4.5 | 23.6 / 27.6 / 39.6 | 0/268 [0, 1.4%] | 12 / 29 | 599 / 610 | 5 / 11 |
| on | 2 | 8.5 | 33.3 / 42.7 / 59.2 | 0/512 [0, 0.7%] | 16 / 36 | 597 / 609 | 7 / 13 |
| on | 4 | 16.1 | 45.9 / 60.9 / 84.2 | 0/969 [0, 0.4%] | 31 / 64 | 595 / 600 | 14 / 21 |
| on | 8 | 28.7 | 78.8 / 106.9 / 146.3 | 0/1726 [0, 0.2%] | 47 / 72 | 604 / 612 | 25 / 37 |
| on | 16 | 45.9 | 148.2 / 213.6 / 519.2 | 0/2789 [0, 0.1%] | 74 / 96 | 618 / 628 | 37 / 50 |
| on | 32 | 60.5 | 327.3 / 571.5 / 754.3 | 0/3650 [0, 0.1%] | 99 / 125 | 618 / 632 | 50 / 67 |
| on | 64 | 67.0 | 707.4 / 1149.1 / 1227.8 | 0/4075 [0, 0.1%] | 107 / 132 | 627 / 640 | 51 / 71 |
| on | 128 | 69.3 | 1535.5 / 2032.4 / 3856.6 | 0/4225 [0, 0.1%] | 113 / 305 | 641 / 663 | 56 / 86 |

- **Correctness under load:** 38,662 requests, each with a unique MRN and marker. **Every response restored exactly its own request's text: 38,662/38,662, and 0 cross-request mix-ups.**
- **Throughput saturates at about 84 req/s with masking off and 69 req/s with it on.** Beyond that, added latency is queueing in the single gateway process, which runs at about one full core.
- **No errors at any level.**
- **Masking adds about 10 ms at concurrency 1** (23.6 vs 13.5 ms p50).

### E10 — Stock LiteLLM vs the gateway under the E2 faults (mock, $0)

**Setup.** Both arms use the same LiteLLM 1.102.1 and the same mock routes.
- **Stock:** a plain fallback from `fo-primary` to `fo-fallback`, with LiteLLM's defaults for everything else and no gateway hooks.
- **Gateway:** the fix build, where the fallback route has no BAA and is cleared for internal data only.
- **Load:** 5 requests/s for 75 s with the fault from 10 to 30 s, alternating PHI-labelled requests (synthetic patient note, `x-gateway-data-classification: restricted; PII`) and non-PHI ones. 100 requests fell in the fault window: 50 PHI, 50 non-PHI.

| Fault | Measure | Stock LiteLLM | Gateway (fixed build) |
|---|---|---|---|
| 503 | **PHI requests reaching the non-BAA fallback** | **45/50 = 90.0% [78.6, 95.7]** | **0/50 = 0.0% [0.0, 7.1]** |
| 503 | Non-PHI served by the fallback | 44/50 | 50/50 |
| 503 | Latency of successful requests during the fault, median / p95 | 4,763 / 5,390 ms | 134 / 139 ms |
| 503 | Primary attempts per request, mean / max | 2.96 / 3 | 1.0 / 1 |
| 503 | Failed requests; error bodies exposing internals | 0 | 50 PHI, typed `503 no_cleared_route_available`; 0/50 bodies expose internals |
| 429 | **PHI requests reaching the non-BAA fallback** | **45/50 = 90.0% [78.6, 95.7]** | **0/50 = 0.0% [0.0, 7.1]** |
| 429 | Latency of successful requests, median / p95 | 4,732 / 5,358 ms | 135 / 1,356 ms |
| 429 | Failed requests | 0 | 45 PHI, typed `429 upstream_rate_limited` after the shipped retries (~4.8 s; no cleared fallback) |
| timeout | **PHI requests reaching the non-BAA fallback** | **19/50 = 38.0% [25.9, 51.8]** | **0/50 = 0.0% [0.0, 7.1]** |
| timeout | **Retries of a timed-out call** | **up to 3 attempts (mean 2.71)**; timeouts then failed over | **none** (max 1 attempt) |
| timeout | Latency, median | 12,474 ms (successful requests, after retries) | 5,042 ms (every request failed, typed `504 upstream_timeout`) |

**Error bodies.** Stock LiteLLM returned no errors in these runs, because it absorbed every fault by retrying and failing over. That is exactly how PHI reached the non-BAA route. The gateway returned 195 typed errors, and none exposes internals.

**Attribution.** In the gateway's timeout run, 14 of 375 requests carried a marker the PHI scan masked, so the mock's log could not attribute them. The gateway's own `x-gateway-served-route` header confirms that **no PHI-labelled request was served by the fallback in any gateway run.**

### E11 — Independent external benchmark: ASQ-PHI (both builds, mock, $0)

> **Independent external benchmark.** Kept separate from our own synthetic results. These are not our data and not our labels.

**Source.**
- ASQ-PHI, "An Adversarial Synthetic Benchmark for Clinical De-Identification and Search Utility". Mendeley Data, DOI **10.17632/csz5dzp7nx.1**, version 1, https://data.mendeley.com/datasets/csz5dzp7nx/1. Article: Data in Brief 2026, DOI **10.1016/j.dib.2026.112586**. Licence **MIT**.
- File: `synthetic_clinical_queries.txt`, SHA-256 `cf00e424b8d2347d019f9f34e2ad1510cb4d853605410f8314bef44df8021fc8`, which matches the hash in the repository's file listing. Used unchanged, kept outside this repository, never copied in. Results keep only query indices, tag types and outcomes.

**Parsing.** As the authors' own validator does: split on `===QUERY===`, then `===PHI_TAGS===` and one JSON tag per line. Counts match the authors' published statistics exactly: 1,051 queries, 832 with PHI, 219 hard negatives, 2,973 PHI elements, with the same per-type counts. One tag value does not occur verbatim in its query; it is reported, not scored. 2,975 tagged occurrences were scored, because some values occur twice in their query.

| Type (dataset label) | n | Recall, baseline | Recall, fix | Leaked / partial (fix) |
|---|---|---|---|---|
| GEOGRAPHIC_LOCATION | 828 | 184 = 22.2% [19.5, 25.2] | 185 = 22.3% [19.6, 25.3] | 532 / 111 |
| NAME | 814 | 723 = 88.8% [86.5, 90.8] | 723 = 88.8% [86.5, 90.8] | 1 / 90 |
| DATE | 806 | 477 = 59.2% [55.8, 62.5] | **793 = 98.4% [97.3, 99.1]** | 10 / 3 |
| MEDICAL_RECORD_NUMBER | 305 | 99 = 32.5% [27.5, 37.9] | 100 = 32.8% [27.8, 38.2] | 198 / 7 |
| HEALTH_PLAN_BENEFICIARY_NUMBER | 91 | 7 = 7.7% [3.8, 15.0] | 7 = 7.7% [3.8, 15.0] | 83 / 1 |
| PHONE_NUMBER | 45 | 45 = 100% [92.1, 100] | 45 = 100% [92.1, 100] | 0 / 0 |
| SOCIAL_SECURITY_NUMBER | 33 | 33 = 100% [89.6, 100] | 33 = 100% [89.6, 100] | 0 / 0 |
| EMAIL_ADDRESS | 31 | 30 = 96.8% [83.8, 99.4] | 30 = 96.8% [83.8, 99.4] | 1 / 0 |
| UNIQUE_IDENTIFIER | 14 | 1 = 7.1% [1.3, 31.5] | 1 = 7.1% [1.3, 31.5] | 10 / 3 |
| ACCOUNT_NUMBER | 4 | 1 = 25.0% [4.6, 69.9] | 1 = 25.0% [4.6, 69.9] | 3 / 0 |
| FAX_NUMBER | 2 | 2 = 100% [34.2, 100] | 2 = 100% [34.2, 100] | 0 / 0 |
| CERTIFICATE_LICENSE_NUMBER | 1 | 0 = 0% [0.0, 79.3] | 0 = 0% [0.0, 79.3] | 1 / 0 |
| IP_ADDRESS | 1 | 1 = 100% [20.7, 100] | 1 = 100% [20.7, 100] | 0 / 0 |
| **All** | **2,975** | **1,603 = 53.9% [52.1, 55.7]** | **1,921 = 64.6% [62.8, 66.3]** | 839 / 215 |

| Measure | Baseline | Fix |
|---|---|---|
| PHI queries with at least one tagged value reaching the upstream in clear | 755/832 = 90.7% [88.6, 92.5] | 718/832 = 86.3% [83.8, 88.5] |
| **Hard negatives (no PHI) with anything masked** | **199/219 = 90.9% [86.3, 94.0]** | **199/219 = 90.9% [86.3, 94.0]** |
| — masked spans on hard negatives | 357 | 328 |
| — of those: age phrases ("55-year-old") / bare years / durations / other | 177 / 61 / 32 / 87 | 176 / 61 / 4 / 87 |
| Wrong placeholder type | 69 | 69 |

- **Why MRN recall is low.** Most of the dataset's MRNs are alphanumeric with dashes (shapes "AA-999999" ×44, "AAA-999999" ×18, "#AA-999999" ×10, "AA-99999" ×9). The synthetic MRN rule accepts digits only, so these leak while the digit-only MRNs are masked (`results/e11_mrn_shapes.csv`; shapes only, no values).
- **Why geographic recall is low.** The dataset labels facility names ("… Hospital", "… Clinic") as geographic locations, and the NER model rarely tags them.

### E12 — Long agent runs (fix build)

**Setup.**
- Each run has its own per-run key (issuer-minted; TTL 3,600 s).
- Each turn resends the full history plus one new synthetic note (seed 12; 5 identifiers from 5 Safe Harbor categories), on a route without a BAA.
- **Mock:** the route is priced like GPT-5 mini, so budgets are realistic. That pricing is simulated; the mock costs $0.
- **Real:** GPT-5 mini, `reasoning_effort: minimal`.

| Turn | Mock (20 runs): exact restore | Mock: detection-cache hit rate | Mock: identifier values in clear at the upstream | Real (5 runs): exact restore | Real: cache hit rate | Real: values in clear |
|---|---|---|---|---|---|---|
| 1 | 20/20 | 19/40 = 47.5% [32.9, 62.5] | 31/100 | 5/5 | 4/10 = 40.0% [16.8, 68.7] | 8/25 |
| 2 | 20/20 | 60/80 = 75.0% [64.5, 83.2] | 67/200 (31 repeats) | 5/5 | 10/20 = 50.0% | 15/50 (8 repeats) |
| 5 | 20/20 | 180/200 = 90.0% [85.1, 93.4] | 163/500 (130 repeats) | 5/5 | 41/50 = 82.0% | 35/125 (30 repeats) |
| 10 | 20/20 | 380/400 = 95.0% [92.4, 96.7] | 316/1000 (277 repeats) | 5/5 | 90/100 = 90.0% | 82/250 (68 repeats) |
| 15 | 19/20 | 580/600 = 96.7% [94.9, 97.8] | 491/1500 (449 repeats) | 4/5 | 141/150 = 94.0% [89.0, 96.8] | 134/375 (116 repeats) |
| 20 | 20/20 | 780/800 = 97.5% [96.2, 98.4] | 658/2000 (624 repeats) | 5/5 | 190/200 = 95.0% [91.0, 97.3] | 172/500 (165 repeats) |
| 30 | 20/20 | 1179/1200 = 98.2% [97.3, 98.9] | 999/3000 (958 repeats) | 5/5 | 290/300 = 96.7% [94.0, 98.2] | 260/750 (248 repeats) |
| **All turns** | **595/600 = 99.2% [98.1, 99.6]** | | | **146/150 = 97.3% [93.3, 99.0]** | | |

All 30 turns are in `results/e12_per_turn.csv`. Latency per turn: mock median 79 ms (p95 88); real median 1,757 ms (p95 2,459).

**Do misses repeat?** Yes, almost always.
- On the mock, 999 values reached the upstream in clear at least once. **958 of them leaked again on every later turn.**
- On the real model, 260 leaked, and 248 leaked on every later turn.

A value the detector misses once is resent in clear on every remaining turn of the run.

**Restore failures.**
- **Mock (5 of 600 turns):** the same number appeared twice in one history in two spellings, for example "(503) 555-0153" and "503-555-0153". The gateway maps values that are equal after normalisation to one placeholder and restores the first-seen spelling, so the right number came back in another format. Two of the five were replayed and inspected; both were this phone-format case.
- **Real (4 of 150 turns):** every one involved identifiers of that note that were sent **in clear** (ages over 89, a biometric ID, MRNs, a member ID, a vehicle ID), which the model then reworded. None was a placeholder failure.

**Budget overshoot at realistic budgets.** The check runs before each call, so the last allowed call can overshoot.

| Budget | Runs | Refused (`429 budget_exceeded`) | Overshoot at refusal (gateway's own run usage) |
|---|---|---|---|
| Mock $0.005 | 5 | 5/5, at turns 17–19 | 2.2%, 6.7%, 13.1%, 14.7%, 18.0% |
| Mock $0.01 | 5 | 5/5, at turn 26 or 27 | 7.5%, 10.7%, 11.8%, 12.4%, 15.0% |
| Mock $0.02 / $0.03 | 5 / 5 | 0 (a full 30-turn run costs about $0.015) | — |
| Real $0.01 (GPT-5 mini) | 3 | 1/3, at turn 29 | 6.9% |
| Real $0.02 | 2 | 0 (a full run cost $0.0083–$0.0115) | — |

Overshoot ranged 2.2–18.0% (median 11.8%, n = 11 refused runs). It is bounded by the cost of the last allowed call, which grows with the history.

**Revocation** (mock, n = 20). A per-run key was used for calls back-to-back, and the issuer revoked it mid-run.

| Measure | Result |
|---|---|
| First call refused | 20/20, with `401` |
| Time from the revoke **returning** to the refusal | median 55.7 ms, p95 69.1 ms, max 69.7 ms |
| Time from the revoke **starting** to the refusal | median 112.1 ms, max 127.8 ms |
| Calls that started after the revoke returned and still succeeded | **0** |

### E13 — Stock LiteLLM with its own tag-based routing, under the E10 faults (mock, $0)

**Question.** E10 compared the gateway with a plain stock fallback. Can stock LiteLLM 1.102.1 keep PHI off the non-BAA fallback with its own documented tag routing?

**Setup.** Everything is as in E10: same LiteLLM 1.102.1, same mock routes, 5 s route timeout, same fault script and load (100 requests in the fault window: 50 PHI, 50 non-PHI). The only change is tag routing.
- **LiteLLM build.** The same PyPI wheel inside the baseline image. All 3,319 files match the wheel's `RECORD` hashes, so it is unmodified. None of the gateway's callbacks are loaded.
- **Config** (`stack/e13-litellm-tags.yaml`, identical for every variant):
  - `fo-primary` (BAA): `litellm_params.tags: ["phi", "default"]`;
  - `fo-fallback` (no BAA): `tags: ["default"]` only;
  - `router_settings.enable_tag_filtering: true`;
  - `router_settings.fallbacks: [{fo-primary: [fo-fallback]}]`;
  - everything else LiteLLM's default (retries, cooldown, `tag_filtering_match_any` default).
  - A Postgres database holds the virtual keys.
- **Documentation used** (read 2026-11-09):
  - https://docs.litellm.ai/docs/proxy/tag_routing (sections "Tag Based Routing", "Setting Default Tags", "Required Tags (AND)", "Fail-Open Fallback (`allow_fail_open`)", "Team based tag routing (Enterprise)");
  - https://docs.litellm.ai/docs/proxy/reliability (fallbacks).
  - Settings used: `tags` (deployment), `enable_tag_filtering`, `fallbacks`, the request-body field `tags`, key `metadata.tags`, and the required-tag prefix `&`. `allow_fail_open` was not set, so it stays off.
  - The behaviour was also checked against the 1.102.1 source (`litellm/router_strategy/tag_based_routing.py`).
- **Variants.** PHI requests differ as below. Non-PHI requests always use a key without tags and send no tags.

| Variant | Tag on the key | Tag the client sends |
|---|---|---|
| A (brief) | none | `"tags": ["phi"]` in the request body |
| B-other (brief) | `["phi"]` | a different tag, `["internal"]` |
| B-none (brief) | `["phi"]` | none |
| C-key (supplementary, not in the brief) | `["&phi"]`, the documented "required tag" form | none |

- **Key-level tags need Enterprise.** The documented `/key/generate` field `tags` is refused without an Enterprise licence: `403 "This feature is only available for LiteLLM Enterprise users: tags."` (`results/e13_key_setup.json`).
  - The proxy stores that field as the key's `metadata.tags`, and `/key/info` shows `metadata.tags` exactly as set. So Variants B and C set `metadata.tags` directly.
  - Without that workaround, Variant B cannot be configured on open-source 1.102.1.

**Results** (fault window, n = 50 PHI and 50 non-PHI per cell):

| Fault | Measure | A: tag in request | B-other: key tag, client sends other tag | B-none: key tag, no client tag | C-key: key `&phi` (supplementary) |
|---|---|---|---|---|---|
| 503 | **PHI reaching the non-BAA fallback** | **44/50 = 88.0% [76.2, 94.4]** | **45/50 = 90.0% [78.6, 95.7]** | **44/50 = 88.0% [76.2, 94.4]** | **0/50 = 0.0% [0.0, 7.1]** |
| 503 | PHI failed / non-PHI served by the fallback | 0 / 44 | 0 / 43 | 0 / 43 | 44 / 45 |
| 503 | Latency, successful requests, median / p95 | 4,853 / 5,445 ms | 4,804 / 5,418 ms | 4,847 / 5,415 ms | 4,668 / 5,416 ms |
| 503 | Latency, failed requests, median | – | – | – | 9,328 ms |
| 503 | Primary attempts per request, mean / max | 2.96 / 3 | 2.96 / 3 | 2.96 / 3 | 2.95 / 3 |
| 429 | **PHI reaching the non-BAA fallback** | **45/50 = 90.0% [78.6, 95.7]** | **45/50 = 90.0% [78.6, 95.7]** | **44/50 = 88.0% [76.2, 94.4]** | **0/50 = 0.0% [0.0, 7.1]** |
| 429 | PHI failed / non-PHI served by the fallback | 0 / 44 | 0 / 43 | 0 / 44 | 46 / 43 |
| 429 | Latency, successful, median / p95; failed, median | 4,892 / 5,506 ms; – | 4,763 / 5,400 ms; – | 4,902 / 5,570 ms; – | 4,717 / 5,344 ms; 9,470 ms |
| timeout | **PHI reaching the non-BAA fallback** | **20/50 = 40.0% [27.6, 53.8]** | **19/50 = 38.0% [25.9, 51.8]** | **18/50 = 36.0% [24.1, 49.9]** | **0/50 = 0.0% [0.0, 7.1]** |
| timeout | **Retries of a timed-out call** (primary attempts, mean / max) | **2.71 / 3** | **2.71 / 3** | **2.71 / 3** | **2.72 / 3** |
| timeout | PHI failed; latency successful median / p95; failed median | 0; 12,465 / 20,241 ms | 0; 12,496 / 20,025 ms | 0; 12,370 / 20,206 ms | 20; 12,189 / 20,017 ms; 24,340 ms |
| all | PHI reaching the fallback outside the fault window | 0 | 0 | 0 | 0 |

- **Error bodies.**
  - A, B-other and B-none returned **no errors**: every PHI request was either served by the primary or failed over.
  - C-key returned 110 errors (44 + 46 + 20). Each has **HTTP status 401**, `"type":"internal_server_error"` and `"code":"401"`, and **all 110 expose internals**: the exception class, the provider message, the model-group names and the tag. Example (503 run, complete):
    ```
    {"error":{"message":"litellm.ServiceUnavailableError: ServiceUnavailableError: OpenAIException - mock upstream unavailable. Received Model Group=fo-primary\nAvailable Model Group Fallbacks=['fo-fallback']\nError doing the fallback: Not allowed to access model due to tags configuration. Passed model=fo-fallback and tags=['&phi']","type":"internal_server_error","param":null,"code":"401"}}
    ```
- **Why A and B leak.** In each variant, the fallback call carries the request's tags into `fo-fallback`, where no deployment has the tag `phi`. For plain tags, LiteLLM then serves the group's `default`-tagged deployment. The docs say so ("Plain-tag exhaustion keeps its existing behavior: fall back to the default pool if one exists"), and so does the code.
  - Tagging the non-BAA route `default` is what the docs recommend for untagged traffic, and the brief asked for it.
  - In B-other the request carries both tags (`phi` from the key, `internal` from the client); neither exists in `fo-fallback`, so the result is the same.

**Plain statement.** With the tag set-ups in the brief (A, B-other, B-none), **stock LiteLLM did not block the PHI fallback**: 36–90% of PHI requests reached the non-BAA route, the same as E10 without tags. **Stock LiteLLM did block it** in the supplementary set-up C-key (documented required tag `&phi` on the key, `allow_fail_open` off): **0/50 in every fault**. That set-up was not tuned to fail or to pass. It costs:
- an Enterprise-gated key field, set here through key metadata;
- a mis-coded 401 error whose body exposes internals;
- the same retry behaviour as E10: a timed-out call is retried up to 3 times. As a result, a failed PHI request takes a median 9.3 s on 503 and 24.3 s on timeout. The gateway's typed errors took 134 ms and 5.0 s in E10.

### E14 — Scale-out, soak and a real-provider spot check (fix build)

**Host.** All E14 numbers come from this one machine (`results/e14_host.json`):

| | |
|---|---|
| CPU | Intel Core Ultra 9 185H: 16 physical cores, 22 logical threads |
| RAM | 63.5 GiB |
| OS | Windows 11 Home 10.0.26200 |
| Docker | Docker Desktop, client and engine 29.0.1, WSL2 backend (Linux 6.6.87.2), cgroup v2 |
| Docker VM | 22 CPUs, 31.1 GiB. These are WSL2 defaults: there is no `.wslconfig` and no CPU or memory setting in Docker Desktop |
| Container limits | **none** on the gateway, detector or mock: CPU quota, shares, cpuset and memory are all 0 (unlimited) |
| Other load | 3 unrelated idle containers (about 0% CPU) |

**Worker setting.**
- The gateway's worker count is LiteLLM's CLI flag **`--num_workers`**. It can also come from the environment variable `NUM_WORKERS`, but an explicit flag overrides it.
- The shipped image sets **`--num_workers 1` in its Dockerfile `CMD`**. The shipped local compose file passes the same flag, and the cloud template does not override the command.
- E14 uses `stack/compose.e14.yaml`, a copy of the E9 compose file in which only that flag changes, to `${GW_WORKERS}`.
- With N > 1, uvicorn starts N server processes under a supervisor, and each worker gets its own Prisma engine. The harness counted 0, 2 and 4 spawned workers for N = 1, 2 and 4.
- The PHI detector keeps its shipped local setting: 2 gunicorn workers (`WORKERS=2`, `--preload`). The tenant policy keeps `pseudonymization.max_concurrency: 2`.

**Same settings as E9** for A and B:
- the mock adds a fixed 200 ms (`MOCK_DELAY_MS=200`);
- every request is sent to the gateway as model `phi`, using the `phi-app` team key;
- that model maps only to `phi--mock`, which has `baa: false`, so masking is mandatory on every call;
- gateway-added latency is client latency minus the mock's `x-mock-service-ms` header, which the gateway passes on.

During A, the mock's own records confirmed this setup:
- the mock reported `delay_ms 200.0`;
- every request it received was on the `phi` route;
- its last received body read `Patient <<PERSON_1>> (MRN: <<MRN_1>>, DOB <<DATE_1>>) called <<PHONE_1>> …`.

Every request carries a unique MRN and a unique marker, and the marker contains no 8-digit run, so the E9 marker artefact cannot recur. A mix-up follows the corrected E9 rule: a response that is not its own request's text and that holds an MRN or marker issued to another request in the same run.

**A — Scale-out** (mock, $0; 60 s per level). Each line is one worker count at one concurrency level. CPU is in % of one core, and each value is the mean / max of `docker stats` samples taken about every 2 s (30 per level).

| Workers | Concurrency | Throughput (req/s) | Added p50 / p95 / p99 (ms) | Errors | Gateway CPU (%) | Gateway memory (MiB) | Detector CPU (%) | Load-generator CPU (%) |
|---|---|---|---|---|---|---|---|---|
| 1 | 16 | 45.6 | 145.9 / 241.8 / 517.5 | 0/2745 | 80 / 108 | 521 / 535 | 41 / 63 | 6 / 9 |
| 1 | 32 | 61.0 | 324.0 / 539.6 / 697.1 | 0/3675 | 99 / 181 | 551 / 577 | 50 / 69 | 8 / 12 |
| 1 | 64 | 67.9 | 711.4 / 1132.4 / 1194.8 | 0/4109 | 108 / 130 | 594 / 611 | 58 / 72 | 10 / 17 |
| 1 | 128 | 66.6 | 1626.6 / 2127.8 / 2990.2 | 0/4098 | 108 / 143 | 612 / 626 | 69 / 100 | 9 / 33 |
| 2 | 16 | 52.9 | 102.9 / 171.1 / 456.0 | 0/3184 | 86 / 126 | 1379 / 1403 | 43 / 55 | 7 / 8 |
| 2 | 32 | 70.2 | 287.2 / 475.0 / 713.5 | 0/4236 | 121 / 164 | 1451 / 1472 | 60 / 76 | 10 / 14 |
| 2 | 64 | 111.0 | 349.8 / 631.1 / 899.7 | 0/6703 | 197 / 289 | 1518 / 1554 | 99 / 121 | 17 / 25 |
| 2 | 128 | **114.8** | 1110.4 / 1730.4 / 1855.0 | 0/6993 | 206 / 238 | 1576 / 1590 | 101 / 138 | 21 / 47 |
| 4 | 16 | 50.8 | 120.3 / 187.0 / 455.5 | 0/3064 | 96 / 141 | 2210 / 2234 | 46 / 60 | 7 / 8 |
| 4 | 32 | **82.0** | 214.2 / 335.0 / 641.0 | 0/4946 | 148 / 205 | 2276 / 2304 | 73 / 86 | 12 / 15 |
| 4 | 64 | 89.7 | 635.6 / 1062.3 / 1216.9 | 0/5429 | 169 / 350 | 2383 / 2431 | 79 / 102 | 13 / 18 |
| 4 | 128 | 112.2 | 1178.9 / 1885.4 / 2502.0 | 0/6836 | 202 / 246 | 2447 / 2474 | 102 / 137 | 19 / 31 |

- **Correctness:** 56,018 requests, all 200. **Every response restored exactly its own request's text: 56,018/56,018. 0 cross-request mix-ups at every worker count and level.**
- **The 1-worker rows reproduce E9.** E9's masking-ON rows at concurrency 16 / 32 / 64 / 128 gave 45.9 / 60.5 / 67.0 / 69.3 req/s, with added p50 of 148.2 / 327.3 / 707.4 / 1535.5 ms. A gives 45.6 / 61.0 / 67.9 / 66.6 req/s and 145.9 / 324.0 / 711.4 / 1626.6 ms.
- **More workers help, but only to about 113 req/s.**
  - The ceiling rises from about 68 req/s with 1 worker to 114.8 with 2 and 112.2 with 4.
  - 4 workers are no faster than 2 at the top.
  - At concurrency 64, the 4-worker run (89.7 req/s) was slower than the 2-worker run (111.0 req/s). One run per cell cannot separate that from noise.
- **Is the detector the next limit? Probably, but this is not proven.**
  - With 2 and 4 gateway workers, throughput levels off at about 113 req/s. At that point neither container uses all the CPU it could. The gateway runs at about 200% with 4 workers, half of the 400% that 4 processes could use. The detector runs at about 100% mean and 137% max, out of 200% for its 2 workers.
  - Its gunicorn workers are synchronous: each serves one scan at a time, so 2 workers cap it at 2 scans in flight. A ceiling of about 113 scans/s means each scan holds a worker for about 17 ms.
  - Detector CPU is the only resource that doubled from the 1-worker to the 2-worker ceiling (about 60–70% mean at 1 worker → about 100% at 2 and 4 workers).
  - The detector's worker count was **not** varied, so whether it is the limit is an inference, not a measurement.
- **The load generator was not the limit:** at most 47% of one core.
- **Gateway memory grows with workers:** about 0.6 GiB with 1 worker, 1.5 GiB with 2 and 2.4 GiB with 4. Each worker loads the full proxy and its own Prisma engine.

**Worker count chosen for B and C: 4.**
- **The rule:** at the soak concurrency (32), rank the worker counts by throughput. Take the highest whose added p95 is no more than 10% above the p95 of the next-best option by throughput; otherwise step down.
- **Applied:** 4 workers give 82.0 req/s at p95 335.0 ms; the next-best, 2 workers, give 70.2 req/s at p95 475.0 ms. The p95 for 4 workers is lower, so 4 is chosen (`results/e14_worker_choice.json`).
- **Applied to peak throughput instead,** the same rule would pick 2 workers: 114.8 against 112.2 req/s, a 2.3% gap.

**B — Soak** (mock, $0): 4 workers, concurrency 32, 30 minutes.
- **Fresh start:** the gateway container was recreated just before the soak, so it had served no earlier traffic.
- **First attempt discarded:** the soak was first started on the gateway left over from A's 4-worker pass. I stopped it within seconds and deleted its partial files.

| Window | Requests | Throughput (req/s) | Added p50 / p95 / p99 (ms) | Errors | Gateway CPU (%) | Gateway memory mean (MiB) | Detector CPU (%) | Detector memory mean (MiB) |
|---|---|---|---|---|---|---|---|---|
| 0–5 min | 24,805 | 82.7 | 216.9 / 341.5 / 664.4 | 0 | 149 / 210 | 2365 | 72 / 90 | 956 |
| 5–10 min | 25,135 | 83.8 | 203.6 / 335.3 / 728.7 | 0 | 150 / 188 | 2452 | 72 / 89 | 971 |
| 10–15 min | 25,256 | 84.2 | 206.5 / 325.3 / 761.6 | 0 | 147 / 243 | 2495 | 71 / 87 | 992 |
| 15–20 min | 25,183 | 83.9 | 208.2 / 322.1 / 803.1 | 0 | 149 / 203 | 2530 | 71 / 88 | 1006 |
| 20–25 min | 25,415 | 84.7 | 196.9 / 321.5 / 811.4 | 0 | 149 / 187 | 2558 | 71 / 92 | 1025 |
| 25–30 min | 25,319 | 84.4 | 199.0 / 320.9 / 812.0 | 0 | 148 / 212 | 2578 | 71 / 86 | 1065 |
| **All** | **151,124** | **84.0** | **204.9 / 327.9 / 766.3** | **0/151,124 [0.00, 0.003%]** | | | | |

- **Correctness:** 151,124/151,124 restored exactly. **0 cross-request mix-ups.** No errors of any kind.
- **Throughput and p95 were steady:** 82.7–84.7 req/s and p95 320.9–341.5 ms in every window. **p99 rose from 664 to 812 ms** over the run.
- **Memory grew for the whole 30 minutes and had not levelled off when the soak ended.** Per-minute values are in `results/e14b_memory_per_minute.csv`.

  | Container | Minute 0 (MiB) | Minute 29 (MiB) | Change | Slope after minute 5 |
  |---|---|---|---|---|
  | Gateway (4 workers) | 2,296 | 2,586 | +290 MiB (+12.6%) | about 378 MiB/hour |
  | Detector | 952 | 1,075 | +123 MiB (+12.9%) | about 266 MiB/hour |

  - The gateway's growth slowed over time: +132 MiB in minutes 0–5, then +39 MiB in minutes 20–29.
  - The detector's growth sped up near the end: +31 MiB from minute 25 to minute 29 alone.
  - These are `docker stats` figures, which exclude page cache. 30 minutes cannot tell a leak from a slow warm-up, so this needs a longer soak before any claim either way.

**C — Real-provider spot check** (GPT-5 mini).
- **Request settings:** `gpt-5-mini-2025-08-07`, `reasoning_effort: minimal`, `max_completion_tokens: 100`.
- **Route and masking:** masking ON (`real-mini` has `baa: false`), 4 workers, concurrency 8, 300 s.
- **Path:** every call went through the gateway and the body recorder.
- **Prompt:** the E9 note, with E7 set A's system message plus "and reference code".
- **Matching:** every call was matched to its recorder entry by the provider's response id (1471/1471).
- **Smoke run first:** a 15 s run of 72 calls (`results/e14c_smoke_*`), all 200, all restored exactly, 0 identifier values in clear, $0.010533.

| Measure | Result |
|---|---|
| Calls | 1,471, all `200` |
| Throughput | 4.89 req/s; at concurrency 8 this is set by provider latency (client p50 1,565 ms, p95 2,094 ms) |
| **Gateway-added latency** (client time minus the recorder's provider time) | **p50 25.0 ms, p95 36.2 ms, p99 48.3 ms**; maximum below 500 ms; first 20 s p95 53.7 ms (warm-up) |
| **Provider 429s** (at the recorder; not counted as gateway errors) | **0**; the recorder saw 1,471 exchanges, all `200`, and no request needed a second provider attempt |
| Gateway errors | 0/1471 = 0.00% [0.00, 0.26] |
| **Exact restore** (name, MRN, DOB and phone verbatim in the answer; no placeholder left) | **1471/1471 = 100% [99.74, 100]** |
| Planted identifier values in clear at the provider | **0/5,884** (4 per call) |
| Reference marker kept in the answer | 1471/1471 (the detector did not mask it) |
| **Cross-request mix-ups** | **0**. A real answer is never its request's text, so the rule reduces to "holds another request's MRN or marker" |
| Answers cut off at the token limit | 0 |
| Tokens in / out | 192,448 / 82,822 |
| **Spend** | **$0.213756** at route prices. The gateway's team spend after the run was $0.224289, which is the smoke's $0.010533 plus $0.213756 exactly. E14 total: **$0.224289**, under the $5 cap. A $5 team budget was set as a safety net, and it was never reached |

- **Comparison with E7:** E7 measured GPT-5 mini one call at a time, with gateway-added latency of p50 23.9 ms and p95 31.3 ms. At concurrency 8 with 4 workers it is 25.0 / 36.2 ms.

**Worse than expected (E14).**
- More gateway workers stop helping at about 113 req/s on this host. 4 workers were no faster than 2 at the top and used 0.9 GiB more memory.
- Gateway and detector memory grew about 12–13% over the 30-minute soak and had not levelled off when it ended.
- The soak's p99 rose from 664 to 812 ms while its p95 stayed flat.

---

## 2. Surprising or worse than expected

1. **Detection on varied text is far below the template results.**
   - All identifiers: E8 64.1% [61.6, 66.5] and E11 64.6% [62.8, 66.3] on the fix build, against 99.0% on the E4 templates.
   - Every format a rule targets scores 100%. The gaps are formats with no rule: names written "LAST, FIRST", SSNs without dashes, MRNs as "Chart #" or "MR#", alphanumeric MRNs (most of ASQ-PHI's), plain bank accounts, passport numbers, licence plates, URLs without a scheme, ZIP codes without a label, street addresses (27.8%), facility names (ASQ-PHI geographic 22.3%).
   - **The E4 numbers describe the E4 formats, not clinical text in general.**
2. **Over-masking is still common.**
   - 65% of our PHI-free notes and 90.9% of ASQ-PHI's hard negatives had something masked on the fix build.
   - The main causes are things Safe Harbor allows: ages under 90 ("55-year-old"), bare years ("2019"), durations and lab values. All are labelled `DATE` by the NER model.
   - The 2d deny-list fixed weekdays and relative phrases, but these remain.
3. **Misses are sticky in agent loops.** 958 of 999 (mock) and 248 of 260 (real) values that leaked once leaked again on every later turn. One detector miss means the value is resent in clear for the rest of the run.
4. **Ages over 89 have no detector.** They were masked only by chance (65.3%, always under the wrong type `DATE`), and leaked in clear otherwise.
5. **Device IDs and biometric IDs have no detector** (1.4% and 8.5%, by chance).
6. **One restore is value-correct but not byte-exact.** When the same number appears in two spellings in one history, the first-seen spelling is restored: 5 of 600 mock turns.
7. **A single gateway process tops out at about 84 req/s (masking off) and 69 req/s (on)** on this host, at one CPU core. E14 varied the workers: 2 or 4 raise the masking-on ceiling to about 113 req/s and no further (see item 12).
8. **Stock LiteLLM sent PHI-labelled requests to the non-BAA fallback in 90% of 503 and 429 faults, and 38% of timeouts, and retried timed-out calls up to 3 times.** This is the "without the gateway policy" baseline. The gateway: 0/50 in every fault.
9. **Budget overshoot is up to 18% at realistic budgets** (median 11.8%), larger than the per-call share at the start of a run, because each call resends a longer history.
10. **Azure OpenAI was not tested** (empty key-file values), and Claude Sonnet 5.5 had to be priced by hand (not in LiteLLM 1.102.1's price map).
11. **Stock LiteLLM's own tag routing does not keep PHI off the fallback in the brief's set-ups (E13).** With the tag in the request or on the key, 36–90% of PHI requests still reached the non-BAA route, because a tag that matches nothing in the fallback group falls back to its `default` deployment. Only the documented required-tag form `&phi` on the key blocked it (0/50 in every fault, supplementary variant). That needs an Enterprise-gated key field (set here through key metadata) and returns a mis-coded 401 whose body exposes internals.
12. **Scale-out stops at about 113 req/s with masking on (E14).** Moving from 1 to 2 gateway workers raised the ceiling from about 68 to 114.8 req/s. 4 workers gave 112.2 req/s and used 0.9 GiB more memory than 2. Neither the gateway nor the detector used all its CPU at the top. That fits the detector's 2 synchronous workers as the limit, but the detector's worker count was not varied, so this is not proven.
13. **Memory grew during the 30-minute soak (E14) and had not levelled off when it ended.** The gateway (4 workers) went from 2,296 to 2,586 MiB (+12.6%) and the detector from 952 to 1,075 MiB (+12.9%), with the detector's growth speeding up near the end. Throughput and p95 were flat, but p99 rose from 664 to 812 ms. 30 minutes cannot tell a leak from warm-up.

## 3. Spend

| Experiment | Real calls | USD | Source |
|---|---|---|---|
| E7 (GPT-5 mini, GPT-5, Claude Sonnet 5.5) | 600 | 0.5443 | tokens × route prices; matches the gateway's spend log |
| E12 real (5 runs × 30 turns + 5 budget runs) | 298 | 0.1004 | the gateway's own run usage per run |
| Smoke calls (2 Claude parameter checks, 1 GPT-5 mini) | 3 | ~0.005 | remainder |
| E14 C (GPT-5 mini: 72 smoke calls + 1,471 calls) | 1,543 | 0.224289 | the E14 real tenant's team spend; equals tokens × route price (0.010533 + 0.213756) |
| E8, E9, E10, E11, E12 mock, E13, E14 A and B | 0 | 0 | mock upstream |
| **Total real-provider spend** | **2,444** | **0.873576** | E7/E12 real tenant's spend log (0.649287, sum over all rows) + E14 real tenant's team spend (0.224289) |

- Budget cap: $75 for E7–E13, and $5 for E14 (E14 used $0.224289). The E7/E12 spend log has 902 rows: the 901 provider calls plus one $0 row for the budget refusal, which never reached the provider.
- The E12 real list-price estimate from token counts ($0.115 for the 5 full runs) is about 2.3 × the gateway's figure. That fits OpenAI billing the repeated history prefix as cached input at a lower rate. The table uses the gateway's figures.
- **Not checked against provider invoices.**

## 4. Tests and fingerprint

Unit tests for the new harness code, pytest 9.2.0:
- `harness/tests/test_additions.py`: generator spans, balance and synthetic ranges; ASQ-format parser on a hand-written sample; placeholder fate; load-prompt uniqueness; Wilson.
- `harness/tests/test_e14.py`: E14 markers have no 8-digit run, values are unique, MRN ranges don't overlap, and the foreign-value count ignores a request's own values.

Full result line (re-run after E14):
```
10 passed in 0.07s
```

**Analysis corrections** (data unchanged, re-analysed):
- **E9 mix-ups.** The first mix-up count matched the 8-digit worker number inside each request's own marker at 16+ workers. The corrected rule counts a mix-up only for a response that differs from its own request text and holds a foreign value: 0. The raw count is kept in `results/e9_summary.csv` for transparency.
- **Generator test.** One unit-test check was wrong (it read the e-mail domain incorrectly); the test was fixed, not the generator.
- **Generator dealing.** A first version dealt categories unevenly; it was fixed before any run.

**Fingerprint.**
- Re-checked after E14: every file of the baseline research folder outside the additions folder is byte-identical to the snapshot taken before this work: **58 files**, which are the 56 baseline files, `01_inventory.md` (edited earlier, in Part A) and `04_after_fixes.md`.
- The 56 baseline files also still match the **original** baseline fingerprint.

**Other checks.**
- **Leak check** (re-run after E14), full line: `leak check: 122 files scanned, 0 contain a key, a 12-character key piece or the endpoint host`.
- **Name scan:** clean.
- **Dataset strings:** searching for ASQ-PHI strings in `additions/` finds none.
- **Upstream repository:** unchanged, at `7577bb1` with no working-tree changes.

## 5. Files created

All under `additions/`:

**Report:** `05_additions.md` (this file).

**`harness/`:**

| File | Purpose |
|---|---|
| `keyenv5.py` | key loader; named variables only, lengths only |
| `mock5.py` | mock upstream with a request log and service-time header |
| `recorder5.py` | body recorder with provider timing |
| `research_config5.py` | route list with explicit prices |
| `common5.py` | shared helpers |
| `notes5.py` | Safe Harbor note generator |
| `e7_vendors.py`, `e8_coverage.py`, `e9_load.py`, `e9_sampler.py`, `e10_stock.py`, `e11_asqphi.py`, `e12_agent.py`, `e13_tags.py`, `e14_scale.py` | experiment runners (`e14_scale.py` also samples `docker stats`) |
| `smoke7.py` | Claude parameter smoke test |
| `stats.py`, `scoring.py`, `synthetic_prompts.py` | unchanged copies from `baseline/harness/` |
| `tests/test_additions.py`, `tests/test_e14.py` | unit tests |

**`stack/`:**

| File | Purpose |
|---|---|
| `compose.add.yaml`, `compose.stock.yaml` | compose files for the gateway tenants and stock LiteLLM |
| `a-base.env`, `a-fix.env`, `load.env`, `e12.env`, `real.env` | per-project settings |
| `policy-a.yaml` (copy, tenant id changed), `policy-real5.yaml` | tenant policies |
| `routes-mock.yaml` (copy), `routes-e12.yaml`, `routes-real5.yaml` | route lists |
| `stock-litellm.yaml` | stock LiteLLM config |
| `compose.e13.yaml`, `e13-litellm-tags.yaml` | E13: stock LiteLLM with tag routing, plus Postgres for virtual keys |
| `compose.e14.yaml`, `e14.env`, `e14-real.env` | E14: `compose.add.yaml` with the gateway's `--num_workers` taken from `GW_WORKERS`; mock and real tenants |

**`results/`** (CSV unless noted; 13 MB; synthetic data only; no ASQ-PHI text):

| Experiment | Files |
|---|---|
| E7 | `e7_calls`, `e7_identifiers`, `e7_summary` |
| E8 | `e8_entities_{a-base,a-fix}`, `e8_notes_{a-base,a-fix}`, `e8_coverage`, `e8_by_format`, `e8_overmasking` |
| E9 | `e9_requests_{off,on}`, `e9_levels_{off,on}`, `e9_summary`, `e9_resource_samples.jsonl` |
| E10 | `e10_{stock,gateway}_{503,429,timeout}`, `e10_summary` |
| E11 | `e11_occurrences_{a-base,a-fix}`, `e11_queries_{a-base,a-fix}`, `e11_recall`, `e11_overmasking`, `e11_hardneg_shapes`, `e11_mrn_shapes` |
| E12 | `e12_turns_{mock,mock-budget,real,real-budget}`, `e12_runs_*`, `e12_per_turn`, `e12_budget`, `e12_revocation` |
| E13 | `e13_{A,B-other,B-none,C-key}_{503,429,timeout}`, `e13_summary`, `e13_key_setup.json` |
| E14 | `e14_host.json`, `e14_worker_choice.json`, `e14a_w{1,2,4}_{requests,levels}`, `e14a_w{1,2,4}_samples.jsonl`, `e14a_summary`, `e14b_{requests,levels,windows,memory_per_minute,memory_trend}`, `e14b_samples.jsonl`, `e14c_{requests,run,summary}`, `e14c_spend.json`, `e14c_smoke_{requests,run,summary}`, `e14c_smoke_spend.json` |

**Outside the repository (scratchpad, not committed):** the ASQ-PHI download (3 files, hashes verified). The image `itpro-gateway:fix-3c468b0` remains in the local Docker image store.
