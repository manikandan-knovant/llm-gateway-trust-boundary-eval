# After fixes: branch fix/research-findings-2026-11 (NOT the article baseline)

> **Label:** after fixes, branch `fix/research-findings-2026-11`, not the article baseline.
> The article baseline is **baseline build 2026-11-09** (commit `6fb0334`, `02_results.md`). This file does not change it, and `02_results.md` was not edited.

**At a glance** (mock upstream only, tenant A, no real-provider calls, $0):
- **E2 b (429 on the primary):** fallback now serves 100/100 requests during the fault (baseline 90/100), at **133.8 ms median** instead of 4,937 ms.
- **E2 c (restricted + PII, primary returns 503):** still fails closed on 100/100 with nothing sent to the non-BAA route. The error is now a typed `503 no_cleared_route_available` with no internals and the request ID.
- **E4:** detection rose from 89.1% to **99.0%**, "medical record number n" from 8.8% to **100%**, and over-masking fell from 20.4% to **0%**. Restore stayed at 2,500/2,500.

| Item | Value |
|---|---|
| Code | Commit `3c468b0` on branch `fix/research-findings-2026-11` (parent `6fb0334`) |
| Image | `itpro-gateway:fix-research-findings-2026-11`, built from that commit, LiteLLM 1.102.1 (the baseline image `itpro-gateway:baseline-2026-11-09` is unchanged) |
| Harness | `baseline/harness`, **unchanged**: copied byte-identically to a separate re-check folder (checked with `diff -r`) so the baseline's `results/` could not be overwritten. The only difference in the copied stack is the image tag in `stack/compose.tenant.yaml` (one line) |
| Scope | Mock upstream only, tenant A (Presidio + regex), $0. E2 scenarios b and c; E4 detection and restore on the same 500 prompts (seed 2026) |
| Run window (UTC) | 2026-11-09 18:29:00 to 18:39:24 |
| Raw data | `recheck/results/` (CSV; synthetic data only) |

## E2: failover (before / after)

Same setup as the baseline: 5 requests/s for 75 s, fault on the primary from 10 to 30 s, 100 requests during the fault, gateway restarted before each scenario.

### Scenario b: primary returns 429, internal data (a cleared fallback exists)

| Measure | Baseline (02_results.md) | After fixes |
|---|---|---|
| Failed during fault | 0/100 | 0/100 |
| Served by fallback during fault | 90/100 | **100/100** |
| Latency of successful requests during fault, median | 4,937.4 ms | **133.8 ms** |
| Same, p95 | 5,425.0 ms | **137.5 ms** |
| First fallback request's latency | 5,280.1 ms | **137.5 ms** |
| Failed after fault / recovery to primary | 0 / first request after the fault | 0 / first request after the fault |

The 429 on a route that has a cleared fallback now fails over without retries (`GATEWAY_RATE_LIMIT_RETRIES_WITH_FALLBACK`, default 0).

### Scenario c: primary returns 503, requests declared `restricted; PII` (the only fallback has no BAA)

| Measure | Baseline | After fixes |
|---|---|---|
| Failed during fault (fail closed) | 100/100 | 100/100 (unchanged, by design) |
| Sent to the non-BAA fallback | 0/100 | 0/100 (unchanged) |
| Error status / code | 503 / `"503"` (untyped) | 503 / **`no_cleared_route_available`** |
| Error bodies exposing internals (library name, route group, router details, upstream text, URL) | yes (example in 02_results.md) | **0/100** |
| Error bodies with the request ID | no | **100/100** |
| Failed requests, median latency | 91.3 ms | 93.3 ms |
| Audited as `request_blocked` | no | yes (100 events per run) |

New error body:
```
{"error":{"message":"the model provider is unavailable, and no other route is cleared for this request's data; the gateway did not send it elsewhere. Try again later","type":"upstream_error","param":null,"code":"no_cleared_route_available","policy_id":"<tenant-id-a>-policy@v1","request_id":"<id>"}}
```

**PHI/PII safety check.** The mock's fallback target received 200 requests in total. That equals the fault-window traffic of the two scenario-b runs (one discarded, see below). Scenario c sent nothing to it in either run.

## E4: detection and restore (before / after)

Same 500 synthetic prompts (seed 2026), 2,000 identifiers, route without a BAA. Detection is scored on what the mock upstream received (`harness/scoring.py`, unchanged). Rates are 95% Wilson intervals.

| Entity / format | Baseline | After fixes |
|---|---|---|
| **All identifiers** | 1,783/2,000 = 89.1% [87.7, 90.4] | **1,979/2,000 = 99.0% [98.4, 99.3]** |
| PHONE | 500/500 = 100% [99.2, 100] | 500/500 = 100% [99.2, 100] |
| PERSON | 485/500 = 97.0% [95.1, 98.2] | 485/500 = 97.0% [95.1, 98.2] (not addressed) |
| DATE (all) | 412/500 = 82.4% [78.8, 85.5] | **494/500 = 98.8% [97.4, 99.4]** |
| — `March 14, 1962` | 64/100 = 64.0% [54.2, 72.7] | **94/100 = 94.0% [87.5, 97.2]** |
| — `14 Mar 1962` | 48/100 = 48.0% [38.5, 57.7] | **100/100 = 100% [96.3, 100]** |
| — numeric formats (3) | 100/100 each | 100/100 each |
| MRN (all) | 386/500 = 77.2% [73.3, 80.7] | **500/500 = 100% [99.2, 100]** |
| — **`medical record number n`** | 11/125 = 8.8% [5.0, 15.1] | **125/125 = 100% [97.0, 100]** |
| — `MRN: n`, `MRN#n`, `MRN n` | 125/125 each | 125/125 each |
| Leaked (no character masked) | 141 | 10 (all names) |
| Partial (some characters in clear) | 76 | 11 (5 names, 6 dates; see below) |
| **Over-masking** (prompts with a non-identifier span masked) | 102/500 = 20.4% [17.1, 24.2], all "Monday" as DATE | **0/500 = 0.0% [0.0, 0.8]** |
| **Restore**, all 5 variants (exact, single angle, brackets, bare, SSE split across chunks) | 2,500/2,500 = 100% [99.85, 100] | 2,500/2,500 = 100% [99.85, 100] |

**What the remaining misses are:**
- **The 6 partial dates leak no date digits.** All six are `March 14, 1962`-style dates. The NER detector labelled a span such as "1976, MRN#7331643" as `PERSON`. That span is longer than the regex date span, so it wins the overlap, and the date is split across `<<DATE_1>>` and `<<PERSON_2>>`. Only the separators ", " or " " stay in clear: checked by re-sending each of these prompts and aligning what the upstream received.
- **The 15 name misses are unchanged.** Ten names leaked fully and five partly ("Aisha "), all in the same templates as in the baseline. None of the fixes target the NER model.
- **More MRNs are masked under the wrong type.** 28 of 500 MRNs were masked under a placeholder of another type (12 `LOCATION`, 12 `PERSON`, 4 `DATE`), against 11 in the baseline. The value is hidden in every case and restore is exact. The cause: the MRN span is now the value only, which is shorter, so a longer mislabelled NER span wins the overlap more often.

## Discarded runs

- **The first E2 b and E2 c runs (18:29–18:33 UTC) completed their load but could not save their CSV:** the re-check copy had no `results/` folder yet, and `e2_failover.py` does not create it (E4's writer does). No numbers from them are used. Both scenarios were re-run after the folder existed (18:36:12–18:39:24 UTC), with the gateway restarted first; those are the numbers above.
- The first run's 100 requests in scenario c are why the audit table shows 200 `no_cleared_route_available` events.

## Tests (fix branch, offline, no Postgres)

Full gateway suite, `python -m pytest -q` in `gateway/`, LiteLLM 1.102.1:
```
1679 passed, 45 skipped, 61 warnings in 259.49s (0:04:19)
```
The baseline was `1558 passed, 45 skipped`. The 121 new tests are in three new test files (detection fixes, upstream errors, rate-limit fallback), plus the updated error-contract tests.

## Limitations

- **The re-check covers less than the baseline.** It is the mock only, one host, one tenant, and only E2 b/c and E4. E1, E3, E1-real, E4-real and E6 were not re-run on the fix branch.
- **No real model was called.** The doubled-label effect of the baseline (49% of GPT-5 mini answers) is addressed in code (label left in clear), but was **not** re-measured with a real model here.
- **The E4 prompts are the same 500 synthetic template prompts the fixes were designed against.** The detection gain is measured on these formats only, and the fix rules were written with them in view. A held-out set or real formats (OQ-062) would be needed to claim generalisation.
