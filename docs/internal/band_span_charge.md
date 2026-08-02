# Width-aware band charging (`band_span_charge`) — issue #518

**Status: mode 1 is the DEFAULT** (`band_span_charge = 1`, oversized-only +
proportional). `band_span_charge 0` is the escape hatch back to the legacy
single-band charge. This note records the mechanism, the sweep across all five
modes, the two accounting bugs that invalidated an earlier version of the
table, and the three rejected attempts to remove the regressions — so none of
it is re-derived.  **Known cost as filed: two corpus flows** (`rnr/mix2` and
`rnr/mix2_topdown_refine`), both the same mix2 design and the same 16 bits.
**Both are clean on main as of 2026-08-02** — see the two updates below; the
residual cost is now ~0.44% wirelength on one flow, not overlaps or opens.

> **Known-cost update (2026-07-31, post-#531).** The recorded regressions
> have largely healed on main: #531's ripup width gate + post-climb dead-span
> refold recover `rnr/mix2` to **0/0/0** (fully clean) and heal
> `rnr/mix2_topdown_refine`'s 16 opens, leaving **3/0/0** (3 overlaps vs the
> pre-flip 0/0/0 — the stage-b lexicographic trade: opens healed at overlap
> cost, then the climb stalls).  Separately, the **escalation-threshold gap**
> behind the 16-bit shape is closed at the source: the dead-span escalation
> predicate (`_escalate_dead_low_segments`, shared by the run_nuts auto path,
> the stage-b heal fold, and the refold) historically fired only on ZERO
> surviving tracks, while DetailedNUTS admission strands the WHOLE segment on
> `n_sig < member bits` — so a 16-bit segment over a keepout-carved window
> with 1..15 surviving tracks passed every heal predicate and still lost all
> 16 bits.  The predicate now uses the admission arithmetic itself
> (`max(span-clear pool, midpoint pool) < member bits`), of which the zero
> threshold was the 1-bit special case.  Byte-identical on all 32 corpus
> flows (the current instances are healed by #531's gate; the widened
> predicate is the structural cover for the next partial-supply placement),
> discriminator-tested in `test_dead_span_escalate.py`.
>
> Note for future triage: `rnr/mix2_fast_on_aligned_sql`'s residual (2/16/1,
> `bundle 61 seg 0`) is **not** this blind spot — its tracks exist but are
> RESERVED by locked bottom-up fixed copies (`reservation conflict` in the
> DNUTS log, 8 unreserved of 16 needed).  That is RELEASE-pass territory
> (`check_template_tracks on_mismatch independent`), which the flow's default
> `stop` policy deliberately gates off — a design-intent choice, not a bug.
> *(Superseded 2026-08-02: the RELEASE pass provably cannot reach it either —
> the opens sit on an UNLOCKED bundle, and per-bit forensics show the unlocked
> b18 alone holds 10 of the 17 candidate tracks, so freeing every locked copy
> still leaves 7 against 16 bits.  See issue #536 and `tools/doomed_seat_forensics.py`.)*

> **Known-cost update (2026-08-02): `mix2_topdown_refine` is CLEAN — the last
> overlap was a search-neighbourhood limit, not a budget or metric one.**
> The flow drifted 3 → 1 overlap on later planner/healer work, and the final
> one now clears: its trailing `refine_selection` runs in `chase_overlaps`
> (plain lexicographic accept).  **Endpoint 0/0/0, `check_design` Success**,
> for **abstract WL 68200 → 68500 (+0.44%)**, detailed +0.15%, +2.6s.
>
> Why nothing else reached it (issue #535's directions, both now measured):
> the winning move — `bundle 62 topo 1->30`, ovl 1→0 — **costs +301 WL**.
> refine's DEFAULT componentwise accept demands WL strictly lower with
> everything else parity-or-better, so it structurally refuses that move no
> matter how many rounds run; and ripup never reaches `topo 30` because its
> trial pool is contention-derived and screened, while refine sweeps every
> eligible bundle's alternates.  Measured dead ends, all **byte-identical**
> to the 1-overlap baseline (same WL, same everything): `ripup_reroute 30`,
> `ripup_reroute 60`, a 3rd healer round, a 4th round, and 3rd round + rr 30.
> So issue #535's "larger budget / more rounds" direction is disproved, and
> its proposed new "overlap-targeted stall pass" is unnecessary —
> `chase_overlaps` already is that lever.
>
> This is NOT the rejected `band_span_charge 0` pin: the charging model stays
> at its default and the flip's cost is still paid and visible, now as
> wirelength rather than as an overlap.

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

All five modes were swept against the SAME baseline, which predated the
`mix2_topdown_refine` corpus row — so every QoR figure in this table
undercounts by one "worse" (see the correction below the per-flow table).
Measured on that flow afterwards, every spreading mode regresses it relative
to mode 0's clean 0/0/0, so the table's relative RANKING is not disturbed:

| mode | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `mix2_topdown_refine` | 0/0/0 | 3/16/1 | 1/0/0 | 1/15/2 | 0/2/1 | 1/11/2 |

Worth noting for any future attempt: on THIS vehicle mode 1 is the *worst* of
the five, and modes 2 and 4 are much milder (1 overlap and 2 stranded bits
respectively). Mode 1 still wins across the 29-flow corpus — this is one flow,
not a ranking — but it shows the oversized-only+proportional choice is not
uniformly the gentlest, and a future fix for the blind spot might be easier to
find from mode 4's shape.

| mode | when | allocation | QoR (as swept) | runtime |
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
| `rnr/mix2_topdown_refine` | 0/0/0 | 3/16/1 | WORSE |

> **Correction.** The sweep above was taken against a baseline that predated
> `flow/rnr/mix2_topdown_refine.buda`, which PR #526 added to the corpus while
> this work was in flight — so that row was in NO sweep, and the flip was
> proposed and approved on "3 better / 1 worse".  Against today's corpus the
> honest tally is **3 better / 2 worse**.  Both regressions are the same mix2
> design, the same 16 bits (`bundle 61 seg 2`), and the same blind spot.

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

## Mode 1 as the default — what flipping it actually did

Mode 1 is **3 better / 2 worse at +7.1%** with WL −0.35%/−0.41% (it was
measured and approved as 3 better / 1 worse — see the correction above). Two
flows go from broken to fully clean (`mix`, `mix2_fast_bottomup` both 1/0/0 →
0/0/0) and a third improves a lot (`mix2_fast_on_aligned_sql` 0/30/2 →
2/16/1). The costs are `rnr/mix2` 0/0/0 → 0/16/1 and
`rnr/mix2_topdown_refine` 0/0/0 → 3/16/1 — the same design, the same 16 bits,
the same blind spot.

Both regressions are **understood and concentrated**, and are the same fault:
one segment, 16 bits, "no track in DetailedNUTS" (`bundle 61 seg 2` on
`mix2`; the corresponding segment on `mix2_topdown_refine`). It is not a healer
budget artifact — the hill-climb runs to a genuine stall (6 iterations, 511
trials, "no improving re-route"). It is the **absolute-supply blind spot**:
the width books say the spread bus fits, but DetailedNUTS still needs real
SIGNAL tracks at the placed position. That margin is what the single-band
charge was implicitly buying.

Three attempts to recover it, all measured, all rejected:

| attempt | result |
|---|---|
| `kPeak 1` alongside mode 1 | heals mix2 completely (0/0/0) but kPeak alone is 2 better/4 worse corpus-wide, and the pair is 1 better/5 worse |
| Contiguity (mode 5) | 2 better/3 worse — no better than plain greedy fill |
| Spread-local supply guard (shortfall priced as overflow inside `score_segment`, only when the charge spread) | 1 better/4 worse, WL +1.6% |

The narrow guard is worth a note: it is the *right-shaped* idea — kPeak's
global floor prices every segment, whereas this one fires only on buses that
actually spread — and it still lost. Pricing a span-supply shortfall as
overflow pushes those buses onto other layers, and the displacement costs more
than the stranding it prevents.

The regression resists targeted repair, so the flip was made as a judgment
call: net +1 flow, WL −0.35%/−0.41%, +7.3% runtime, and #518's phantom
overflow stops distorting selection by default. `mix2`'s clean state was also
knife-edge — its own script comments record the exact 4-iteration rip-up trace
that walks it to 0/0, so any selection change re-rolls that sequence. That
does not make 16 stranded bits acceptable; it is the known cost, and
`set_planner_param band_span_charge 0` reverts it per flow.

**What the flip surfaced beyond the corpus metric.** Three pinned tests moved,
and every one moved in the right direction:

| vehicle | before | after |
|---|---|---|
| `channel_stress` (golden) | 172 segs, **7 unplaced** | 163 segs, **0 unplaced** |
| `rnr/mix` (golden) | 280 segs, **1 overlap** | 248 segs, **0 overlaps** |
| `big` (golden) | 266 segs, 8680 netsegs | 261 segs, 8672 netsegs |
| `comprehensive_demo` (golden) | 37 segs, 265 netsegs | 35 segs, 233 netsegs |
| `comprehensive_regression` | 26 segs, 153 bit-wires, **overflow WARNING** | 24 segs, 121 bit-wires, **no warning** |
| `bigHalf` no-rr opens | **566** (gate off) | **39** (gate off) |

`comprehensive_regression` is the cleanest demonstration: that flow's test
preamble documented a planner overflow WARNING on a route that nevertheless
finished clean — precisely #518's phantom. It is now gone.

**One consequence worth flagging:** `nontop_dead_span_gate` was measured
cutting bigHalf's no-rr opens 566 → 135. With the flip the gate-OFF baseline
is already ~39, and the gate now measures neutral-to-slightly-negative there
(39 → 40) — the honest charge subsumed most of what the gate was recovering.
`test_planner_nontop_dead_span.py` was rewritten to assert the weaker,
still-true invariant (baseline is good, gate does not damage it) rather than a
reduction the gate no longer delivers. The gate stays opt-in and may still pay
on other designs; that is not measured.

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
