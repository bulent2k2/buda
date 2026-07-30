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

## The sweep

`tools/qor_corpus.py`, 29 flows, baseline = `main` at 3665314. Metric is
`overlaps/unplaced/viol_bundles`.

**Knob off — bit-identical.** 29/29 unchanged, abstract and detailed WL equal
to the byte. The off path delegates to `for_each_band` with weight 1.0, i.e.
literally the old code.

**Knob on (always spread): 3 better, 3 worse, 23 unchanged.**

| flow | base | knob on | |
|---|---|---|---|
| `big_data_test/big` | 0/0/0 | 1/56/1 | WORSE |
| `big_data_test/tc3a` | 0/0/0 | 0/21/1 | WORSE |
| `rnr/mix2` | 0/0/0 | 0/2/1 | WORSE |
| `rnr/mix` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_bottomup` | 1/0/0 | 0/0/0 | BETTER |
| `rnr/mix2_fast_on_aligned_sql` | 0/30/2 | 1/13/1 | BETTER |

Abstract WL +0.40%, detailed +0.27% over the 23 comparable flows.

**Rejected variant — spread only an OVERSIZED bus: 1 better, 4 worse.**
The narrower rule (`eff_width > grid[b+1] - grid[b]`, i.e. spread only when no
perp inside the band could ever hold the bus — exactly #518's condition,
leaving every bus that already fits on the legacy path) measured *worse* than
always-spreading: `tc3a` 0/0/0→0/44/1, `mix` 1/0/0→0/14/1,
`mix2_fast_bottomup` 1/0/0→3/12/2, `mix2_fast_topdown` 0/0/0→1/0/0, with only
`mix2_fast_on_aligned_sql` 0/30/2→1/0/0 improving. Not implemented; this
paragraph is the record.

## Why the principled fix loses

Spreading lowers the **per-band feasibility bar**. Previously a bus had to fit
its full width in one band to pass STRICT; now only its share must fit in each
band it covers. More candidates clear STRICT, the planner packs the region
denser, and the density shows up downstream as real DetailedNUTS opens — which
is exactly the shape of the regressions (`big` picking up 56 unplaced bits from
a clean 0).

So the single-band charge is not merely a crude approximation: its
over-conservatism is **load-bearing**. It buys routability margin that the
honest model gives away. Any future attempt should pair the width-aware charge
with something that restores that margin — a density term, or a track-supply
feasibility test at the chosen perp — rather than assuming the more accurate
model is automatically the better one.

That is the real finding of #518, and it is why the issue stays open with the
default unchanged.

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
