# kPeak — measured results across the flow/demo corpus

The experimental record behind the `set_planner_param kPeak` knob: what it
does per testcase, the mechanism findings, and the alternatives that were
implemented, measured, and rejected. Companion to the *"Selection basis"*
section of [wishlist-planner.md](wishlist-planner.md) (levers 1+2 and the
default decision) and the `kPeak` row in
[../script_reference/planner.md](../script_reference/planner.md).

**State measured:** branch `claude/kpeak-supply-aware` (PR #257 —
lever 1 `kPeak` from #252 + lever 2 ripup class re-rank from #255 + the
absolute-supply floor), 2026-07-11, x86 Linux host.

## Methodology

Each flow is copied to a temp file with `set_planner_param kPeak <v>`
injected immediately before its first `run_planner` (and `visualize`
lines stripped), then run via `buda_cli.py --no-viz`. Metrics are the
LAST `Track overlaps: N` / `N bits unplaced` lines the flow itself
prints — i.e. its own `run_nuts` / `run_detailed_nuts` summaries.

Two consequences to keep in mind:

- For the `flow/rnr/` family — the only corpus flows that run
  `negotiate_congestion` / `ripup_reroute` — these are **pre-heal**
  numbers: the heal loops' internal re-runs print through a silenced
  stdout, so the last printed summary precedes them. Healed endpoints
  were measured separately (below).
- Everything else runs the plain pipeline, so the numbers are true end
  states.

## Corpus sweep: kPeak 0 vs 0.1 (with the supply floor)

Numbers are `NUTS overlaps / DNUTS unplaced bits`.

### Clear beneficiaries

| Testcase | kPeak=0 | kPeak=0.1 | Note |
|---|---|---|---|
| `flow/hbundles/06_multipin_stress` | 2 / 34 | 2 / **2** | −94% opens |
| `flow/hbundles/05_stress_grid` | 0 / 47 | 0 / **13** | −72% opens |
| `flow/hbundles/07_wide_fan_stress` | 1 / 0 | **0** / 0 | residual overlap clears |
| `flow/channel_stress` | 0 / 3 | 0 / **0** | heals from 0.05 up; keepout-narrowed corridor — the supply-floor class |
| `demo/ariane133_cache` | 0 / 24 | 0 / **8** | −67% opens |
| `flow/rnr/mix` † | 15 / 190 | 20 / **86** | −55% opens pre-heal |
| `flow/rnr/slowdown_rnr` † | 15 / 190 | 20 / **86** | same design as mix |
| `flow/rnr/mix2_fast` † | 28 / 259 | **21** / **193** | both metrics improve |
| `flow/rnr/mix2` † | 15 / 179 | 17 / **141** | opens −21%, overlaps +2 |
| `demo/mempool_tile` | 61 / 2971 | **50** / **2834** | modest — heavily over-congested design |

† pre-heal (see Methodology); healed endpoints below.

### Regressions — leave the knob off here

| Testcase | kPeak=0 | kPeak=0.1 | Why |
|---|---|---|---|
| `flow/big_data_test/big2/big2_noviz` | 5 / 0 | 6 / **116** | the pre-charge horizon (below); heals to 0 / 0 with negotiate+ripup |
| `demo/comprehensive_demo` | 0 / 0 | 0 / **8** | small clean-design regression |

### No effect

Clean and stays clean: `flow/big_data_test/big.buda`,
`flow/big_data_test/tc3a_flat` (clean at every tested value — the floor
removed the pre-floor 0.2 regression), `flow/hbundles/09`,
`flow/corner_touch_blocks`, `flow/xlayer_short`, `demo/quickstart`,
`demo/keepout_demo`, `demo/bp_tile`, `demo/mempool_group`,
`demo/nvdla_cbuf`, `demo/user_guide`.

Dirty but unchanged: `demo/ariane136` and `demo/ariane_buda5`
(128 = 128), `demo/large_scale_demo` (128), `demo/mempool_cluster`
(4 / 512).

Unmeasurable: `flow/big_data_test/tc3a.buda` (opens a hardcoded
`/Users/ben/…` BDB path; fails identically at every kPeak value).

The pattern: kPeak pays off on **congested multi-bundle designs with
shared corridors** (hbundles stress, rnr family, ariane133_cache,
channel_stress) and is neutral-to-harmful on clean or trunk-dominated
ones — the measured basis for keeping it opt-in (default 0).

## Value sweeps (0 / 0.05 / 0.1 / 0.2)

With the supply floor (pre-floor values in parentheses where they
differ):

| Flow | 0 | 0.05 | 0.1 | 0.2 |
|---|---|---|---|---|
| `rnr/mix` opens † | 190 | **110** (182) | **86** (128) | 158 (169) |
| `channel_stress` opens | 3 | **0** (3) | 0 | 0 |
| `tc3a_flat` | clean | clean | clean | **clean** (regressed) |
| `big2_noviz` (ovl/opens) | 5/0 | 6/116 | 6/116 | 6/104 |

Take-aways: mix's optimum is 0.1 and non-monotonic (0.2 backslides);
the floor moved channel_stress's healing threshold from 0.1 down to
0.05 and removed tc3a's 0.2 regression; big2 is untouched by the floor
(its mechanism is not supply — below).

## Healed endpoints (kPeak × negotiate/ripup)

| Flow | baseline healed | kPeak 0.1 healed |
|---|---|---|
| `rnr/mix` | 1 overlap / 0 opens | 0 overlaps / **16 opens** |
| `big2` (loops added manually) | 0 / 0 | 0 / 0 |

Same with and without the supply floor on mix (the 16 residual opens
are kPeak's own routes resisting the heal, with mix's configured loop
budget). So even "kPeak + the loops" is a per-design option to validate
with `check_design`, not a blanket recommendation.

## Mechanism findings

**The supply-floor class (real, fixed).** `usage/cap` is purely
relative: a band whose track pattern — or a *region override*, which the
width capacity model structurally cannot see (`eff_bus_width` uses the
layer's GLOBAL pattern) — leaves too few real signal tracks reports
util=0 and looks maximally attractive. The floor prices such a band as
full (`count_signal_tracks_in_span` over the routed extent × Hanan band
∩ slide window, only when kPeak > 0 and the layer has a
`def_track_pattern`). Deterministic repro:
`test/tests/test_planner_kpeak_supply.py` (8-bit bus through an
override-starved 3-track corridor: blind default strands all 8 bits,
the floor detours it clean).

**The big2 stranding (real, NOT fixable at plan time).** Two successive
hypotheses were falsified by measurement:

1. *"Double-steer with the feedback loops"* — rejected: `big2_noviz`
   runs the plain pipeline; no loops are involved.
2. *"Absolute-supply blindness"* — rejected: the stranded trunks'
   NUTS windows hold **153 / 93** real signal tracks for their 60 / 56
   bits; the floor never fires, and the DNUTS failure path is the
   `reserved`-tracks exhaustion (no `insufficient signal tracks`
   warning). Bundle 23 isn't even party to any NUTS overlap.

The actual mechanism is the **pre-charge horizon**: a wide bundle plans
early (widest-first order) against a nearly-empty congestion map, so
its kPeak term sees pristine bands; later bundles' intervals pile into
the same window; DetailedNUTS's all-or-nothing reservation then strands
the widest late-processed segment (60 + 56 = the 116 opens). No
per-bundle term evaluated at plan time — relative or absolute — can
price arrivals that come after it. This is intrinsically a feedback
problem, and `negotiate_congestion` + `ripup_reroute` are the fix
(they heal big2 to the baseline endpoint). Identical in
`signal_tracks` mode, so not a width-model artifact.

## Implemented, measured, rejected

1. **Post-charge utilization** (`(usage+eff)/cap`, #252): on
   uncongested designs it degenerates into an intrinsic
   "narrow channel" penalty that biases against the column channels
   BITRUNK datapath trees use — datapath WL regressed 2–20% across the
   sweep. → pre-charge `usage/cap`.
2. **Farness-first over the whole candidate pool** (ripup lever 2,
   #255): commits a far expensive candidate over a cheap same-effect
   one via the equal-metric tie (mix bundle 85: +2% abstract WL).
   → legacy cheap pool first, beyond-cap extras appended; strict-`<`
   commit keeps ties on the cheaper move.
3. **Midpoint-pool fallback in the supply floor** (Codex suggestion on
   #257): mirroring DetailedNUTS's admission retry un-floors bands
   whose span-pool shortfall is real — DNUTS's retry is backstopped by
   `cull_keepout_crossers` (admitted bits whose final span crosses the
   keepout are culled into opens), and the planner inherits the
   optimism without the cull. Measured at kPeak 0.1: mix opens
   86 → 134, `hbundles/06` 2 → 42 (worse than its 34 baseline).
   → strict span-clear pool; the false-positive/false-negative
   asymmetry is lexicographic (a longer route vs opens).

## Guidance

- Default stays **0 (off)** — decided 2026-07-11, confirmed after the
  supply floor shipped.
- Enable `kPeak 0.05–0.1` on congested multi-bundle designs with shared
  corridors, ideally together with `negotiate_congestion` /
  `ripup_reroute`, and validate the endpoint with `check_design`.
- Skip it on clean or trunk-dominated designs (no signal to price;
  only the regression risk remains).
