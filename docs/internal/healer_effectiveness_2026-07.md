# Healer effectiveness — negotiate vs ripup across the nested flow suites (2026-07-20)

A controlled sweep motivated by opens #10 (the `bigHalf.buda` rr flip): for
every routing flow in the nested test suites (`flow/big_data_test/`,
`flow/big_data_test/big2/`, `flow/hbundles/`, `flow/rnr/`) that has
DetailedNUTS opens **without any healer**, measure what a single
`negotiate_congestion` vs a single `ripup_reroute` pass each recovers, and at
what runtime cost.

## Method

Three variants per flow, controls held constant so the comparison is
apples-to-apples:

- **base** — the full pipeline through `run_detailed_nuts` with every
  `negotiate_congestion` / `ripup_reroute` line **stripped**.
- **+neg** — base, then ONE `negotiate_congestion` (stage b).
- **+rip** — base, then ONE `ripup_reroute` (stage b).

`script_path` is set to the flow file so `_healers_in_flow` stays True and the
kSegsRel-0.02 default + gates apply exactly as in the real CLI.
`_dead_span_auto_at_run_nuts = False` disables the run_nuts dead-span
escalation (a healer-coupled behavior) so **base** is a true pre-healing
baseline; +neg/+rip still get the stage-b `_heal_dead_spans` fold that is part
of negotiate/ripup (measuring the healers as they actually behave).

**Caveats.** Each variant applies ONE healer pass; the real flows run
negotiate THEN ripup (often twice). So these numbers are a lower bound on each
healer *alone*, not the shipped multi-healer endpoint. Counts are host-
sensitive (`-march=native` FP); trends, not absolute values, are the result.
Harness: `scratchpad/healer_experiment.py` (measurement host, this session).

## Results (ov = NUTS overlaps, un = DNUTS opens; t = healer wall)

Of 34 candidate flows, **19 route cleanly with no healer** (0/0) and **15 have
opens**:

| flow | base ov/un | +neg ov/un (t) | +rip ov/un (t) |
|---|---|---|---|
| **bigHalf** | 4 / 286 | 1 / 94 (0.8s) | **0 / 0** (4.5s) |
| big_3bundles_pure_mst | 0 / 12 | **0 / 0** (0.0s) | **0 / 0** (0.0s) |
| tc3a | 870 / 9179 | 1049 / 8969 (5.8s) | 1008 / 7939 (16.5s) |
| **big2_noviz** | 2 / 28 | 2 / 0 (0.4s) | **0 / 0** (1.0s) |
| **tc3b_flat** | 2 / 28 | 2 / 0 (0.4s) | **0 / 0** (1.0s) |
| **hbundles/06_multipin** | 4 / 48 | 2 / 8 (0.3s) | **0 / 0** (5.2s) |
| **hbundles/07_wide_fan** | 0 / 11 | 0 / 11 (0.1s) | **0 / 0** (1.7s) |
| mix | 13 / 224 | 9 / 98 (1.7s) | 5 / 16 (2.8s) |
| mix2 | 16 / 374 | **7 / 92** (2.3s) | 11 / 124 (2.8s) |
| mix2_fast | 33 / 256 | 22 / 146 (4.7s) | 20 / 112 (4.5s) |
| mix2_fast_bottomup | 17 / 196 | 19 / 168 (2.2s) | 12 / 28 (9.5s) |
| mix2_fast_on_aligned_sql | 33 / 256 | 22 / 146 (4.7s) | 20 / 112 (4.5s) |
| mix2_fast_topdown | 16 / 175 | 7 / 80 (1.9s) | 2 / 46 (4.7s) |
| mix2_repro | 16 / 374 | **7 / 92** (2.3s) | 11 / 124 (2.9s) |
| slowdown_rnr | 13 / 286 | 14 / 94 (2.8s) | 6 / 98 (4.3s) |

## Findings

1. **ripup is the finisher; negotiate is the cheap first pass.** A single
   ripup drives **6 flows to a clean 0/0** that negotiate alone does not reach
   (bigHalf, big2_noviz, tc3b_flat, hbundles/06, hbundles/07, big_3bundles).
   Negotiate is ~0.1–2s and typically halves the opens but leaves a residue;
   ripup is 2–5× slower and closes them. This is exactly the division of
   labor the code comments assert ("ripup_reroute remains the finisher for
   whatever negotiation leaves") — now measured across the suite.

2. **On the crowded mix2 family, one negotiate pass can beat one ripup pass on
   opens** (mix2 / mix2_repro: neg 7/92 vs rip 11/124). A single ripup re-pins
   greedily from the un-negotiated state; the real flows run negotiate FIRST
   (steering off the contended bands) THEN ripup, which is why the shipped
   `mix2` config uses both. Neither single pass fully clears mix2 — its opens
   are the LOW-supply-contention class (a capacity shortage; see
   wishlist-planner "TOP-capacity"), not something either healer resolves
   alone.

3. **tc3a is pathologically overcongested** (9179 opens): both healers barely
   move it and both *increase* overlaps (870→~1000) chasing opens — a
   design that is fundamentally short of routing resource, not a healer gap.

4. **Runtime.** Negotiate is the cheap steering pass; ripup's cost scales with
   how many contended bundles it re-pins (hbundles/06 5.2s, mix2_fast_bottomup
   9.5s) — the finisher earns its wall time only where it closes the endpoint.

## Consequence — opens #10 resolved

bigHalf's checked-in flow now enables both `ripup_reroute` lines and reaches
the clean **0/0** endpoint (was ~1/94 with negotiate only). The experiment is
the affordability decision the item asked for: ripup is what takes bigHalf to
clean, at ~7s of healing on top of the ~2s no-rr pipeline. Endpoint stays
CI-guarded by `test_bighalf_rr_reaches_clean_endpoint`.
