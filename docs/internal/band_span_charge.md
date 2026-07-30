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
  implemented and measured, and it **lost on both axes**: QoR fell and runtime
  went **+12% → +207%**, because every band then enters the candidate loop and
  runs a full cost evaluation. The cheap fallback is also the better spreading
  centre. The skip stays. (The QoR half of this comparison was measured
  pre-fix; the runtime half is unaffected by the accounting bugs and is the
  decisive one.)

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

Greedy fill was expected to win, and did under the buggy accounting. With the
books corrected it does **not**: proportional mode 1 (3 better/1 worse) beats
greedy mode 4 (2 better/2 worse) and mode 3 (2 better/3 worse). Reading live
occupancy turns out to matter far less than only touching the buses that were
actually mis-charged.

Greedy's spill reach is unbounded to the grid edges. Capping it at one
bus-width beyond the footprint was measured (pre-fix) and lost QoR while
saving no runtime at all (+209% vs +207%), confirming the cost was never this
walk.

## The sweep

`tools/qor_corpus.py`, 29 flows, baseline = `main` at 3665314. Metric is
`overlaps/unplaced/viol_bundles`.

**Knob off — bit-identical.** 29/29 unchanged, abstract and detailed WL equal
to the byte. The off path delegates to `for_each_band` with weight 1.0, i.e.
literally the old code.

**All modes, same build, same baseline.** Runtime is single-run and noisy.

> **These numbers are the POST-FIX ones.** An earlier revision of this table
> was measured with two accounting bugs live (see "Two accounting bugs" below)
> and every conclusion drawn from it was wrong — including the mode ranking.
> Corrupted band usage reads back as free capacity that does not exist, which
> flattered the always-spread modes.

| mode | when | allocation | QoR | runtime |
|---|---|---|---|---|
| **1** | **oversized-only** | **proportional** | **3 better / 1 worse** | **+7.1%** |
| 2 | always | proportional | 0 better / 6 worse | +34.0% |
| 3 | always | greedy fill | 2 better / 3 worse | −25.3% |
| 4 | oversized-only | greedy fill | 2 better / 2 worse | −1.9% |
| 5 | always | greedy + contiguity | 2 better / 3 worse | −8.3% |

Mode 1 per flow (abstract WL −0.35%, detailed −0.41%):

| flow | base | mode 1 | |
|---|---|---|---|
| `rnr/mix` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_bottomup` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_on_aligned_sql` | 0/30/2 | 2/16/1 | BETTER |
| `rnr/mix2` | 0/0/0 | 0/16/1 | WORSE |

**Targeting beats blanket spreading.** Modes 1 and 4 (oversized-only) beat
modes 2 and 3 (always). Spreading a bus that already fits in its band was
never fixing a mis-charge — it only lowered that bus's feasibility bar for
free, and the planner then over-packs. Only the genuinely mis-charged bus
should be touched.

(An earlier revision of this document asserted the *opposite*, calling it a
counter-intuitive result. That claim came from the buggy measurements and is
retracted.)

**Contiguity did not help.** Mode 5 stops the greedy walk at a zero-free band,
on the reasoning that metal cannot hop over a saturated band to claim capacity
on its far side. It measures the same as plain greedy fill (2 better/3 worse).

## Two accounting bugs (found in review of PR #524)

Both were real, both silently corrupted the corpus measurements above, and
both are fixed:

1. **Rip-up did not reverse a spread charge.** `commit_plan` passes the demand
   NEGATED (`sign * (eff + pitch)`), and a negative width fell through
   `for_each_band_w`'s `eff_width <= 0` guard onto the legacy single-band
   path. Removal therefore subtracted the whole amount from the centre band
   while the original had been spread across several — stranding usage in the
   neighbours and driving the centre band NEGATIVE. Negative usage reads back
   as free capacity that does not exist, and every later replan prices against
   it. Fixed by taking the footprint from `std::fabs(eff_width)` so the sign
   rides only the weights, plus a `charge_log_` recording each committed
   distribution so a rip-up replays its exact inverse — necessary because the
   greedy modes allocate against LIVE occupancy, which has moved on by the
   time the rip-up runs, so re-deriving could not reproduce the split.

2. **The scorer and the committer used different footprints.**
   `score_segment` derived weights from raw `eff_width` but charged
   `eff_width + track_pitch_`, while `apply_segment` was handed the combined
   width and derived weights from that. Near a band boundary STRICT could
   score one set of bands and commit another. Fixed by deriving weights from
   the same `eff + pitch` that is charged, in all three scorers.

`charge_log_` is cleared in `rebuild_cuts_` and `recharge_committed_`, where
band indices and the usage baseline respectively move out from under it.

**Test-coverage limitation, stated plainly:** the rip-up fix is verified by
inspection and by the corpus re-measure, NOT by a unit test. Three attempts to
exercise `commit_plan(..., -1.0)` from Python — via `replan_bundle`,
`replan_bundle_ripup`, and a deliberately contended two-bus fixture — all
failed to reach the victim path, and each candidate test passed unchanged
against a build with both bugs deliberately reintroduced. A test that cannot
fail on broken code is worse than no test, so none was kept. What the corpus
*does* prove is that the bugs were live: fixing them moved mode 3 from
3 better/1 worse to 2 better/3 worse.

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
