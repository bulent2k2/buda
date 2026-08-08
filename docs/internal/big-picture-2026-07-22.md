# The big picture: topo-gen ↔ planner ↔ healer interactions

*2026-07-22. A cross-subsystem design note: how topology generation, the
congestion planner, and the healer stack (`negotiate_congestion` +
`ripup_reroute`) actually interlock — the two forward handoffs and the two
feedback loops — grounded in the functions that do the work and in a live
trace of two hierarchical flows.*

**Interactive version:** a published interaction map with the live-trace charts —
<https://claude.ai/code/artifact/087b0257-4001-4a97-b460-1d08efcb3794>.

Companion reading: [congestion_planner.md](../congestion_planner.md),
[single_source_topo_truth.md](single_source_topo_truth.md),
[seg_junction_coplacement.md](seg_junction_coplacement.md),
[planner_signal_track_capacity.md](planner_signal_track_capacity.md),
[healer_effectiveness_2026-07.md](healer_effectiveness_2026-07.md),
[ksegs_default_audit.md](ksegs_default_audit.md).

Line numbers below are as-of this writing and will drift; treat the function
names as the durable anchors.

---

## 1. The shape of the thing: a control system with one shared coordinate

```
   ┌─────────────┐   candidates    ┌───────────┐  seg_perp +   ┌──────────┐
   │  TOPO-GEN   │ ───(pool)─────▶ │  PLANNER  │ ──layers────▶ │   NUTS   │
   │ topology_   │                 │ congestion│               │  nuts_   │
   │ analysis    │ ◀── flip_mst ── │  planner  │ ◀─inject_band─│  *.cpp   │
   └─────────────┘   (index alts)  └───────────┘   replan      └────┬─────┘
         ▲                              ▲                            │
         │      re-pin / re-select      │  measured metric           │ overlaps /
         └──────────────────────────────┴── (opens, overlaps) +      │ opens /
                     HEALERS              junction_infeasibilities ◀──┘
              (negotiate_congestion, ripup_reroute)
```

The three subsystems talk cleanly because **they all speak one abstraction: the
per-segment perpendicular slide window** `ConnSeg.perp_lo/perp_hi`. Topo-gen
*produces* it (the `topology_analysis` passes derive it from `seg_busterms` /
`seg_conns`); the planner *clamps band capacity to it* and *gates feasibility on
it*; NUTS treats it as the *hard placement bound* `[c_lo,c_hi]`; and the
healer's entire quality signal — `junction_infeasibilities` — is literally
"this window is disjoint from the partner's nominal span." One geometric
quantity is the lingua franca across all three seams. Everything else is what
rides on top of that window.

---

## 2. Forward seam 1 — topo-gen → planner

The planner never scores raw geometry. For every candidate it calls
`ConnTopology::build(topo, floorplan_)` (`congestion_planner.cpp:1025`), which
runs the six analysis passes and turns `seg_busterms` / `seg_conns` /
`feedthru_blocks` into per-segment `ConnSeg{perp_lo, perp_hi, net_pull,
pull_break}`. So a topology's *connectivity truth* becomes *slide windows*
before the planner sees a single number.

What a candidate carries into `plan_bundle`, and where each is consumed:

| Candidate data | Planner use |
|---|---|
| `estimated_wirelength` | `kWL·wl_est` (`:1401`) |
| `wl_lo/wl_hi` envelope | `wl_est += kWLSpread·(wl_hi−wl_lo)` (`:1356`) — demotes wide-realization-risk shapes |
| `seg_bits` (per-bit taper) | `eff_bus_width(seg_n, seg_w, lid)` charges a fan-in branch only `width·n/nbits` (`:961,1337`) |
| `type` string | `BITRUNK_HVH/VHV` **exempted** from the kSegs penalty (`:1378`) |
| `ConnSeg.perp_lo/hi` | band-capacity clamp + slide-feasibility gate |

Per-segment, per-layer the cost stack is `s = cong + span + base + bal + hgt +
pk` (`:1301`), then once per candidate `topo_score += kWL·wl_est` where
`wl_est` already folds in `kSegs·gate·w_segs`. The two seam-relevant terms:

- **Taper on charged width.** A fan-in branch carrying 8 of 60 bits books
  `width·8/60`, not the full bus. The planner's congestion math is
  *member-bit-scoped*, the same `seg_bits` that keeps NUTS widths and DNUTS
  emission honest — charging and realization read one source.
- **Slide window ↔ band capacity.** `usable_band_cap` intersects the Hanan
  band `[grid[b],grid[b+1]]` with the segment's `[slide_lo,slide_hi]`
  (`:539–563`). A segment that can only slide within part of a band is charged
  against only that part — the *topology's flexibility* directly sets the
  *planner's effective capacity*.

**The escalation ladder** (`optimize_topologies:1690`) is where the window
becomes a hard gate: `STRICT` requires every candidate to be *slide-feasible*
(bus `eff_width` fits `perp_hi−perp_lo`, `:1324`) **and** *overflow-free*
(every band `score_segment ≤ kOvEps`). If none qualify → **rip-up/replan**
committed victims ranked by `plan_band_overlap` on the contended bands →
`ALLOW_OVERFLOW` (soft price + warn) → `BEST_EFFORT` (no gates). A `set_feedthru`
declaration changes *feasibility itself*: `derive_slide_ranges` keeps the
trunk's BUSTERM landings and widens its perp window instead of splitting it
(`topology_analysis.cpp:1796`), so the STRICT window check passes where a solid
block would force infeasible. A topo-gen decision reaching into the planner's
admission test.

---

## 3. Forward seam 2 — planner → NUTS (the "books vs metal" gap)

This is the most interesting seam, because **the planner writes back almost
nothing** — only per-segment **layer** and **`seg_perp`** (the charged-band
centre). Everything else NUTS recomputes from the same `ConnTopology` windows.
And NUTS honors `seg_perp` *only for face-free segments*:

```cpp
} else if (n_bt == 0 && perp_ok && bw.plan.seg_perp[si] != INT_MIN) {
    pull_map[key] = (double)bw.plan.seg_perp[si];      // nuts.cpp:199–211
```

For a segment with busterm faces or a net-pull, NUTS ignores the planner's
charge and places by its own preference cascade (`place_seg:1621`):

1. cross-layer target (corner resolution) →
2. **alignment sibling** (a placed same-bundle segment off the same connector) →
3. **junction-anchored preference** — for a *single-junction* segment
   (`jn_map[si].size()==1`), if its base isn't inside the partner's placed span,
   move to `clamp(base, plo, phi)` so the junction closes where the wire lands
   rather than by stretching the partner (`:1646–1659`) →
4. `seg_perp` / pull / centre, with the **`pull_break` clamp** so a connector
   on a wide interior window can't overshoot and drag its coupled trunk (the b44
   tug-of-war).

Because the planner charges band X but NUTS may place the wire at Y, the "books"
and the "metal" drift. That drift is *measured and reported* per run
(`[NUTS] books-vs-metal: N/M pulled segment(s) placed >100 units from the
planner's charged band`). `charge_pull_target` closes it by charging a pulled
segment at its **deterministic predicted pull target** — the `ConnSeg.pull_break`
breakpoint where the pull's WL gain saturates — but *occupancy-aware*: it charges
the anchor only if that band is overflow-free, else falls back to the same
`best_band_perp` NUTS would pick (`:1103–1142`). "Target the pull, spread to the
nearest free track" — the planner deliberately mimicking NUTS's `preferred_fit`.

The residual that *can't* be closed at this seam is structural: the planner is
**greedy and sequential** (widest-first), so a wide bundle plans against a
near-empty band map and later arrivals pile in — and DNUTS is all-or-nothing, so
the early bundle strands. `kPeak` steers off soon-to-fill bands, but it "cannot
fix the pre-charge horizon." That residual is exactly what the healers absorb.

---

## 4. Feedback loop 1 — negotiate_congestion (model correction)

Negotiate treats each **actual** NUTS overlap / DNUTS open as evidence the
planner's cost model was wrong at that band, and corrects the model in place:

- `inject_band_demand` lays synthetic demand on the exact bands where the
  overlap happened — stage a: `((perp_hi−perp_lo)+pitch)·history[key]`; stage b
  (auto-detected once `detailed_result` exists): `(interval·missing/exp)·
  history[key]` (`ripup.py:1367–1405`). The `injected_` entries are re-applied on
  every recharge, so the correction *persists* through subsequent replans.
- **PathFinder history**: a rect that reappears gets its demand re-scaled up
  each iteration — repeat offenders grow monotonically more expensive, breaking
  oscillation.
- It then replans **both offenders unpinned in one pass** (`replan_bundle_ripup`),
  so the corrected model steers them off the contended bands choosing among *all*
  candidates at once — no per-candidate trial. If the target is STRICT-infeasible
  it also rips the top committed blocker (the victim stage) and replans the pair,
  accepting only if both end overflow-free. Iterations accept only strict metric
  improvement (snapshot/restore otherwise).

Negotiate is the cheap workhorse (~0.1–4s, roughly halves the metric in one
iteration). It fixes the *cost model*; it does not search.

---

## 5. Feedback loop 2 — ripup_reroute (measured search) + the topo reach-back

Ripup fixes the planner's *selection* by brute measured search on the real
lexicographic metric `(DNUTS opens, NUTS overlaps)` (`_rr_stage_metric:53`). Two
things make it a genuine cross-subsystem loop, not a local nudge:

- **It reaches back into topo-gen's full pool.** `_rr_candidate_order` ranks a
  bundle's alternates by *farness from the measured contention centres*, and
  **appends the top-8 candidates from beyond the planner's cheap-8 estimate
  window** (`ripup.py:582`). That is how a higher-estimate class the greedy
  planner would never select — an OOB trunk, a BITRUNK tree — becomes
  promotable, committing only on strict `<`.
- **It targets from three signals**: OPEN bundles first, then their NUTS-overlap
  partners, then bundles named in **`junction_infeasibilities`** (`:223`) — the
  soft signal that a topology's junction can only close by stretching a partner.
  A topo-analysis quantity (window disjoint from partner span), surfaced by NUTS
  *from the final accepted state* (`derive_junction_infeasibilities`,
  `nuts.cpp:2142`), becomes a healer target.

The cost machinery keeps the measured search affordable — each trial is an
incremental replan (`replan_bundle`: recharge every *other* committed
assignment, plan the one moved bundle) plus a NUTS solve:

- **fast_trials** (on): skip metric-neutral work — stage-a `tighten_pulls`
  (overlap non-increasing → trial metric is a sound upper bound), stage-b via
  emission.
- **fixed-context screen** (on): place each candidate alone against every other
  bundle frozen as fixed occupancy (`add_fixed_segments_except`), full-trial only
  the top-2, defer the rest to a full-fidelity stall sweep — so the *stop
  certificate* stays a real sweep.
- **warm_trials** (off): a warm single-bundle re-solve pre-filter; cost-neutral
  once screen + fast already cut the volume.
- **global-occupant pass**: when the contender scan stalls above zero, rank the
  committed bundles *holding* each contention site's bands (`band_occupants`) and
  trial their alternates **pinned** — reaching window-infeasible candidates
  because the pinned ladder ends in BEST_EFFORT. The fix a *non-contended*
  blocker can provide that no contender-derived move reaches.
- **converge guard** (stage-b): bail on hopeless over-capacity designs (≥6 iters,
  metric still ≥100, <3% cleared) — provably can't fire on a converging flow.

The genuine **topo-level feedback** — `flip_mst_edge` (flip one MST edge's L to
the opposite corner, re-`annotate_seg_conns`, re-pin the same index) — exists and
is *tried* on real contended edges, but is **measured redundant and off by
default**: an index alternate (a swap to an entirely different topology) always
wins the commit. A result worth keeping in mind — the coarse move dominates the
fine move.

---

## 6. The subtle couplings — where the three are co-designed, not layered

1. **The planner changes its cost model based on whether a healer will run.**
   `kSegsRel=0.02` (compiled default), `kPeak`, and dead-span auto-escalation are
   all **gated on "healers ahead"** — a scan of the sourced scripts for
   `ripup`/`negotiate`. Rationale: a segment-count penalty biases routes into
   contended corridors, safe only if a downstream healer cleans up; its per-flow
   jaggedness needs the healer loop to smooth. The planner literally trusts the
   healer to exist. This is the strongest evidence the three are one system —
   and §7 shows it changing a routing outcome with *zero healing executed*.
2. **Dead-span escalation bypasses topology entirely.** A LOW segment with zero
   keepout-clear signal tracks is a guaranteed DNUTS open *no re-pin can reach* —
   it's a layer-assignment fault, not a topology fault. So `run_nuts` (when a
   healer is ahead) and both healers escalate it to TOP *before* the hill-climb
   (`_heal_dead_spans`), correcting the planner's layer decision at final
   geometry. A feedback path that skips the topology loop.
3. **`topo_uid` content identity is the glue.** Pins, selections,
   snapshot/restore, and additive `generate_more_topologies` all key off the
   content-fingerprint uid (which *is* the `topology_analysis` cache key). That is
   what lets the healer mutate the candidate pool and still restore a rejected
   trial exactly, and lets a Hanan-loci regeneration renumber indices without
   breaking a pinned selection.

---

## 7. Live trace — two hierarchical flows

Two flows, run headless (`buda_cli.py --no-viz`). Numbers are from this repo at
the branch this doc lands on; they will move with the corpus but the *shape* is
the point.

### 7a. `flow/hbundles/07_wide_fan_stress.buda` — the gating effect, isolated

24 HBundles (D0:12 D1:12), 690 candidates, `run_planner hier 5` (no
`signal_tracks`). Toggle *only* the presence of the two healer commands in the
script:

| Script | Planner note | NUTS | DNUTS |
|---|---|---|---|
| **no healers** | `kSegsRel default suppressed: no healer … in the flow` | 176 segs, 0 overlaps | **11 bits unplaced** |
| **healers present** | kSegsRel=0.02 engages | 127 segs, 0 overlaps | **0 bits unplaced** |

With the healers present the metric was **already 0 before either healer ran**
("metric already 0 — nothing to do"). The healers *executed nothing*. Their mere
presence flipped the planner onto the compact-topology cost model (127 vs 176
segments — fewer, wider trunks instead of many thin stubs), and those compact
topologies avoid the discrete-track shortage that stranded 11 bits. This is
coupling §6.1 in the purest possible form: **a routing outcome decided by a
lookahead for a pass that never had to run.**

(NUTS is clean at 0 overlaps in both cases: 07's residual is a pure *stage-b*
signal-track shortage, not an abstract overlap — which is why the width model's
over-promise only surfaces at DNUTS.)

### 7b. `flow/rnr/mix2_fast_bottomup.buda` — the full 4-stage cascade

Bottom-up template planning (`set_bottom_up` on dnuts1/2, dogleg1/2), 100
expanded wrappers, `run_planner hier 5 signal_tracks`. Forward vs. the full
`NCa → RRa → (DNUTS) → NCb → RRb` sequence:

| | NUTS overlaps | DNUTS opens | check_design (dnuts) |
|---|---|---|---|
| **forward (no healers)** | 17 | 196 | 208 viol / 11 bundles |
| **+ healers** | 21 → **2** | 140 → **~14** | 20 viol / 3 bundles |

Stage by stage, with the real timings:

- `run_nuts` → 21 overlaps (kSegsRel now engaged, so the *selection* differs from
  the forward run — 248 vs 259 segments).
- **NCa** `negotiate_congestion`: `metric 21->9 after 1 accepted iteration` (3.6s)
  — model correction, cheap.
- **RRa** `ripup_reroute`: `metric 9->2 after 7 move(s), 307 trial(s)` (16.2s) —
  measured search, expensive, clears the last abstract overlaps *before* DNUTS.
- `run_detailed_nuts` → 140 opens (down from 196: better selection alone).
- **NCb** `negotiate_congestion`: `metric 134 (ovl 11)->120 (ovl 11)` (4.5s).
- **RRb** `ripup_reroute`: `metric 120 (ovl 12)->20 (ovl 6)` (23.7s) — the
  finisher and the cost centre.

Every mechanism in §§2–6 left a fingerprint in the mix2 log:

```
[NUTS] books-vs-metal: 15/176 pulled segment(s) placed >100 units from the planner's charged band (worst Δ=1326)   (§3)
[NUTS] dead-span escalation: moved 2 dead LOW segment(s) to a TOP layer and re-solved.                             (§6.2, at run_nuts)
[heal] dead-span escalation: moved 3 dead LOW segment(s) to a TOP layer ... before the hill-climb.                 (§6.2, in the healer)
[NUTS] junction infeasible: bundle 11 seg 0 cannot reach seg 2 within its slide window (closed by partner stretch; ripup may re-pin).  (§5)
```

mix2 does *not* reach 0/0 — it is a genuinely over-capacity bottom-up design
(the locked template copies are immovable keepouts the top-level bundles must
detour). The healers take it 208→20 violations, and the remaining 20 are the
honest residual of a floorplan the router cannot fully satisfy. That is the
system behaving correctly: reduce hard, report loud, never silently pretend.

---

## 8. Where the seams are still soft (open questions)

- **The pre-charge horizon is unowned.** The planner can't price arrivals that
  come after a bundle plans; the healers clean up reactively; `kPeak`'s
  pre-charge `usage/cap` is a proxy. Is there a two-pass demand *forecast* that
  belongs between topo-gen and the planner — so the widest bundles plan against
  a predicted-full map rather than an empty one?
- **`seg_perp` is honored only for `n_bt==0`.** The majority of face-bearing
  segments diverge (mix2: 15/176 pulled segs >100 units off, worst Δ1326), and
  `charge_pull_target` only patches the *pulled* ones. The books-vs-metal gap is
  narrowed, not closed.
- **`flip_mst_edge` being redundant** suggests the per-edge DOF is in the wrong
  place — the real datapath winners (BITRUNK trees) carry no `edge_id`. Is there
  a topology-level DOF the healer *should* have but doesn't?
- **`kSegsRel` gating is binary** (healer present / absent) — and a corpus sweep
  (2026-07-22) says that's fine, closing this one. The worry was that a flow with
  a healer but light congestion pays the compact-topology bias for nothing;
  measured on the metric that matters, `(opens, overlaps)`, `kSegsRel=0.02` is
  **neutral-or-better across every active-healer flow** — real wins on mix2 (59→18
  opens) and slowdown_rnr (28→0 opens / 3→0 ovl), ties on mix (−3.8% WL), big2,
  and bigHalf. The penalty is naturally self-gating: it only flips a selection
  when a compact and a multi-segment candidate are in a *close WL race* — the
  congested regime — so a scalar congestion-aware gate has nothing to separate,
  and adds complexity for no measured benefit. (An intermediate reading of this
  sweep suggested a "non-monotone regression"; that was an artifact of counting
  *total* `check_design` violations rather than opens — on the opens metric the
  response is orderly. Sweep harness: `BUDA_KSEGS_REL` env override, α∈{0, 0.02}.)
- **One residue the sweep did surface** — `DISCONNECTED` below the healer metric.
  At α≥0.02, bigHalf grows exactly one `DISCONNECTED` bundle (bundle 67,
  `Seg 3<->7`, 48 bits) that α≤0.01 doesn't — deterministic. Its compact
  selection yields a topology whose junction NUTS can't close, and `ripup_reroute`
  optimizes only `(opens, overlaps)` — it is *blind to* `DISCONNECTED`, so the
  break is reported by `check_design` but never healed. This is a
  healer-*coverage* gap, not a gating-policy problem: should the stage-b metric be
  lexicographic `(opens, disconnected, overlaps)`?
