# Width-aware band charging (`band_span_charge`) — issue #518

**Status: implemented, measured, and deliberately left OPT-IN (default off).**
The corpus says it is not default-worthy. This note records the mechanism, the
sweep, and — more usefully — *why* the obvious fix does not pay off, so the
next person does not re-derive it.

## The defect

`CongestionPlanner::score_segment` charges a segment's whole bus width into the
**single** Hanan band holding the segment's perp:

```cpp
for_each_band(seg, layer_id, perp_pos_override, [&](int ci, int b) {
    double cap = usable_band_cap(c, b, is_vcut_dir, slide_lo, slide_hi);
    double ov  = (c.usage(b) + eff_width + track_pitch_) - cap;
    if (ov > peak) peak = ov;
});
```

`eff_width` is the bus's full perpendicular extent, but `cap` is one band's.
So a bus wider than the tightest band it crosses reports overflow **by
construction, on a completely empty layer** — `usage` never enters into it.

Issue #516's repro is the pure case: one bundle planned, nothing else on the
layer, and the planner still warned. The arithmetic matched the model to the
digit — bus 10 bits × 2.75 = 27.5, tightest band 3 signal tracks × 2.75 =
8.25, reported overflow 19.25.

This is not only log noise. Overflow is a **hard constraint** in the STRICT
stage of the escalation ladder, so a phantom overflow can push a bundle
through rip-up into `ALLOW_OVERFLOW`/`BEST_EFFORT` and distort selection on a
design where nothing is congested.

## The mechanism

`for_each_band_w` is the width-aware sibling of `for_each_band`. A bus of
`eff_width` centred at `pp` occupies `[pp - w/2, pp + w/2]`; the iterator
visits the bands that footprint covers and hands each one its share:

```
knob off:  one band charged                       61.0
knob on:   seven bands charged  [5.5, 10, 10, 10, 10, 10, 5.5]  = 61.0
```

Weights are **normalized to sum to 1 per cut**, so total charge is exactly
`eff_width + pitch` either way. This redistributes demand; it never creates or
destroys it. Without normalization a footprint overhanging the grid edge would
silently under-charge and mask real congestion.

The delicate closed-interval cut-matching rule (issue #22 — a segment ending
exactly on a cut must still count) now lives once, in `for_each_cut_`;
`for_each_band` and `for_each_band_w` are both thin wrappers over it.

Converted call sites — all of them width-sensitive, and they must agree or the
books and the scorer disagree about where demand went:
`score_segment`, `collect_overflow_bands` (must stay score_segment's exact
predicate, or victim ranking chases bands the scorer never charged),
`plan_band_overlap`, `apply_segment`, `cong_cost_segment`.

Deliberately **not** converted:

- `peak_util_segment` (kPeak) — takes no `eff_width`; it prices existing fill,
  and its absolute-supply floor already reasons about the span's real track
  pool. Both knobs are opt-in, so the pairing is an edge case.
- `span_hits_dead_band` — the junction-extension gate; centre-band semantics
  are what it was measured with.
- `inject_band_demand` / `band_occupants` — healer-side, and they already take
  an explicit perp *range* rather than a bus width. Spreading those over the
  measured overlap rectangle is a separate, plausible follow-up.

## How the bus is positioned, and what "optimum band" means

Worth stating plainly, because it is not what one might assume:

- **The planner does not choose the band by net pull.** `best_band_perp`
  ranks candidate perps by `cong_cost_segment` (+ the `kPeak` term when
  enabled), tie-broken by distance to the slide-window centre. `net_pull` is
  a *NUTS* placement preference and never enters the planner's band choice.
  The existing opt-in `charge_pull_target` is precisely the knob that makes
  the CHARGE follow the predicted pull target instead — that is the
  books-vs-metal arc, and what the `[NUTS] books-vs-metal` line measures.
  So under `charge_pull_target`, spreading centres on the pull target; by
  default it centres on the cost-chosen band.

- **`best_band_perp` skips any band narrower than the bus**
  (`if (win_hi - win_lo < eff_width) continue;`). For a bus wider than every
  band that rejects *all* candidates and falls back to the raw slide-window
  centre — which the single-band charge then prices as overflow. That is the
  other half of #518's pathology.

  Relaxing that skip under `band_span_charge` looks obviously right (the bus
  may now occupy neighbours, so a narrow band is a legitimate centre). It was
  implemented and measured, and it **lost on both axes**: QoR 3 better/1 worse
  → 2 better/1 worse, and runtime **+12% → +207%**, because every band then
  enters the candidate loop and runs a full cost evaluation. The cheap
  fallback is also the better spreading centre. The skip stays.

## Allocation: proportional vs greedy fill

Two ways to split a bus across the bands it covers:

- **Proportional** — each band gets its geometric share of the footprint.
  Simple, but blind to occupancy: it dumps demand into neighbours that may
  already be full, manufacturing overflow there.
- **Greedy fill** — saturate the preferred band, then spill to the nearest
  band with room, reading CURRENT usage. This mirrors NUTS's own
  `preferred_fit` ("target the pull, spread to the nearest free track"), and
  routes demand around a locally-saturated neighbour instead of piling
  phantom overflow onto it.

Greedy fill measures better (see below). Its spill reach is deliberately
**unbounded** to the grid edges: capping it at one bus-width beyond the
footprint was measured and lost QoR (2 better/1 worse → 0 better/3 worse)
while saving no runtime at all (+209% vs +207%) — confirming the cost was
never this walk.

## The sweep

`tools/qor_corpus.py`, 29 flows, baseline = `main` at 3665314. Metric is
`overlaps/unplaced/viol_bundles`.

**Knob off — bit-identical.** 29/29 unchanged, abstract and detailed WL equal
to the byte. The off path delegates to `for_each_band` with weight 1.0, i.e.
literally the old code.

**All four modes, same build, same baseline.** Runtime is single-run and
noisy; the ±9% figures are not meaningful, the +207% one was.

| mode | when | allocation | QoR | runtime |
|---|---|---|---|---|
| 1 | oversized-only | proportional | 1 better / 4 worse | −8.7% |
| 2 | always | proportional | 3 better / 3 worse | −8.5% |
| **3** | **always** | **greedy fill** | **3 better / 1 worse** | **+11.9%** |
| 4 | oversized-only | greedy fill | 2 better / 3 worse | +35.6% |

Mode 3 per flow:

| flow | base | mode 3 | |
|---|---|---|---|
| `rnr/mix` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_bottomup` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_on_aligned_sql` | 0/30/2 | 2/14/1 | BETTER |
| `rnr/mix2` | 0/0/0 | 1/6/1 | WORSE |

Two results stand out.

**Greedy fill beats proportional** (mode 3 vs 2: 3 better/1 worse vs
3 better/3 worse). Reading occupancy before spilling is what removes the
regressions — proportional spreading's phantom overflow on already-full
neighbours was causing them.

**Gating on "oversized only" is consistently WORSE than always spreading** —
mode 1 vs 2, and mode 4 vs 3, both lose. This is counter-intuitive: mode 1
touches strictly fewer segments, all of them genuinely mis-charged. The
likely reason is discontinuity — two nearly-identical candidates, one just
over the band width and one just under, get charged under different models,
so the comparison between them is no longer apples-to-apples and selection
gets noisy. A uniform rule, even a less targeted one, ranks candidates
consistently.

## Why it is still not the default

Mode 3 is a genuine net win (3 better / 1 worse, +11.9% runtime), but it
still breaks one clean flow: `rnr/mix2` 0/0/0 → 1/6/1. The corpus guard exits
non-zero on any regression, and turning a clean design broken is exactly the
failure the guard exists to catch.

The mechanism behind that one regression is the same one that sinks modes 1,
2 and 4 more broadly: spreading lowers the **per-band feasibility bar**.
Previously a bus had to fit its whole width in one band to pass STRICT; now
only its share must fit in each band it covers. More candidates clear STRICT,
the planner packs denser, and the density comes back as real DetailedNUTS
opens. Greedy fill mitigates this (it will not claim space a neighbour has
already committed) but does not eliminate it.

So the single-band charge is not merely a crude approximation: its
over-conservatism is partly **load-bearing**, buying routability margin the
honest model gives away. The remaining work for a default flip is to restore
that margin explicitly — a density term, or a track-supply feasibility test
at the chosen perp — rather than assuming the more accurate model is
automatically better.

That is the finding of #518, and why the issue stays open with the default
unchanged.

## Using it

```
set_planner_param band_span_charge 1     # per-flow, before run_planner
BUDA_BAND_SPAN_CHARGE=1 …                # env, for corpus sweeps (mirrors BUDA_KSEGS_REL)
```

An explicit `set_planner_param` wins over the env hook, since it runs after
construction.

Tests: `test/tests/test_planner_band_span_charge.py` — off-path identity, a
narrow bus being unaffected (so any off/on difference is attributable to the
spread), a wide bus still planning under both settings (the spread must
re-price, never hard-block), and total-charge conservation.
