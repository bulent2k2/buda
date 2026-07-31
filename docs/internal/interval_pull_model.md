# The anchor-interval pull model — issue #523

**Status: LANDED** (was "correct but unaffordable" — re-measured after the
healer-robustness work of #531/#534 and affordable now; see "The measurement
history" below). `net_pull` is derived from a per-segment **anchor-interval
cost model** in `derive_net_pull` (`topology_analysis.cpp`), replacing the two
gated vote paths that produced #523's mis-attributed pulls.

## The defect it repairs

The old `derive_net_pull` scored each perpendicular neighbour through one of
two code paths — a *busterm* path (far end is a block face) and a
*floating-spine* path (looking through the neighbour to a trunk) — and **every
vote was a gain claim**: nothing priced the wire a slide *stretches*.  The
asymmetry fired whenever the two neighbours fell into different paths:

- the filed case: an MST jog between two vertical legs got `pull=+1` although
  its slide is wirelength-neutral (the busterm-path neighbour's gain was
  counted, the spine-path neighbour's equal stretch was gated out);
- the documented **b44 tug-of-war** (`find_tug_of_war_pairs`, the
  `dump_topologies --problems` TUG flag, the `check_design` advisory): two
  riders of one spine pulled apart by gain-only votes, jointly stretching the
  spine between them;
- joint-gain claims: a stub co-located with a sibling read a pull although
  moving it ALONE gains nothing (the sibling holds the trunk's bound — the
  per-segment partial derivative is zero).

## The model

Each perpendicular neighbour contributes `max(0, a_lo - x) + max(0, x - a_hi)`
where `[a_lo, a_hi]` is the hull of the positions its far attachments can
occupy (a busterm face is the degenerate `[face, face]`; a junction partner is
that segment's slide window).  The sum is convex piecewise-linear, so its
argmin is a single interval:

- `ConnSeg::pull_lo / pull_hi` — the flat optimum (every position inside is
  wirelength-identical; NUTS may spend it on packing);
- `ConnSeg::pull_f_lo / pull_f_hi` — the cost slopes just outside it (the
  currency an explicit spreader would need);
- `net_pull` / `pull_break` stay **derived** (sign × slope toward the
  interval; saturation at the interval edge), so every consumer — NUTS's
  placement preference, the tug detector, `--conn`, `charge_pull_target`,
  ripup — keeps its semantics.

`net_pull`'s magnitude is now the **slope**: a dogleg sub-trunk with two
same-side anchors honestly reports 2 (one unit of gain per anchor per unit of
slide), which is what the phase-1 strongest-pull-first ordering should rank by.

NUTS consumes the flat optimum as **placement freedom**: `preferred_fit` has
an interval overload (cost = distance to `[pref_lo, pref_hi]`, zero inside;
degenerate = the point form bit for bit), wired through `build_nuts_maps` →
`place_seg` only where the preference comes from the pull.

## What dissolves, and what the tests now pin

- **The b44 tug is gone pool-wide** (0 of 30 candidates flag): seg3's `+1` was
  the artifact; its nominal sits inside its flat optimum.  The detector and
  the display paths stay — a genuine tug (disjoint optima on a shared spine)
  would still flag, and the positive display tests exercise the
  post-dogleg `plan.seg_net_pull` override path, which is real machinery.
- **`stub_order_swap`'s Δ=1249 books-vs-metal divergence is gone** (its pulls
  were pure artifact; the flow has zero pulled segments now), and `pull1`'s
  Δ=101 likewise.  The diagnostic's counter-check now doctors the books on a
  genuinely-pulled `pull1` segment.
- **The big.buda pull-deviation collapses ≈ 19×** (43.5k → 2.3k): accurate
  targets are almost all exactly reachable, B9/B28 land exactly on target,
  and B20's "aligned-sibling deadlock" dissolves at the model level (its
  pulls were the joint-gain claim).
- `charge_pull_target`'s exact-coordinate contract relaxes to "metal inside
  the flat optimum, divergence below the 100-unit report threshold" (seg1
  packs at 1141.5 inside `[200, 1200]` while the charge sits at the
  breakpoint end 1200 — wirelength-identical).

## The cull-risk refold tier (the last residual's heal)

The model's one stubborn corpus residual (`mix2_topdown_refine`, 16 bits) was
a **keepout-cull doom**: b61 seg6 on LOW M3 with span-clear pool 0 and
midpoint pool 19 — DNUTS admits all 16 bits via the midpoint pool, then
`cull_keepout_crossers` strands them all.  No admission-mirror predicate can
see it (#534's escalation correctly does not fire — admission succeeds).

Ripup's post-climb refold now runs **two independently-measured tiers**
(snapshot / escalate + re-solve / accept only on a strictly better metric):

1. **dead** — the exact DNUTS admission predicate (the entry heal's);
2. **cull-risk** — MEASURED stranding (bits missing from the live detailed
   result) + the SURVIVAL predictor (bounded span-clear pool < member bits,
   no midpoint retry).

The measured-stranding gate is what makes tier 2 surgical: span-pool
shortfall alone is the NORM for surviving segments (the junction snap
routinely shortens bits off a keepout), and a predictor-only batch escalated
22 segments on the mix2 vehicle and wrecked the placement (16 → 140 opens,
correctly rejected).  With the gate it escalates exactly the one doomed
segment: `HEAL-REFOLD (cull-risk): 16 (ovl 1) -> 0 (ovl 1)`.  Only the
refold uses this tier — its accept contract prices the residual uncertainty
at zero, which the unconditional entry/auto heals cannot.

## The measurement history

| stack | corpus verdict | notes |
|---|---|---|
| pre-#531 (as prototyped) | **1 better / 4 worse** | whole mix2 family broken (mix2 0/0/0 → 0/44/2); "correct but unaffordable" |
| + unscoped width gate (lab) | 0 better / 4 worse | gate healed mix2 but path-chaos moved mix |
| **today: #531 + #534 + cull-risk refold** | **2 better / 1 worse / 29 unchanged** | `mix2_topdown_refine` 3/0/0 → **1/0/0**, `chip_bottomup` 490/2285/110 → **485/2207/106**; `mix2` absorbs the model at 0/0/0 with WL −0.53%, `mix2_fast_bottomup` −4.1% WL |

The one WORSE is `chip_topdown` (255/3185/163 → 267/3319/158): the
**healerless-by-design** giant stress flow — overlaps/unplaced +4%, while
`viol_bundles` (electrical integrity) *improves* 163→158.  With no healers,
nothing absorbs the spreading loss the accurate pull creates (the old
overshoot-to-window-edge was incidentally scattering segments).  This is the
known cost, the same class the 2026-07-30 lab notes predicted, now confined
to one healerless stress vehicle.

**What changed the verdict was never the model** — it was healer search
robustness: #531's width gate keeps the climb out of statically-infeasible
corners, #534's member-bits escalation heals partial-supply strands, and the
cull-risk refold tier (this work) heals cull-dooms.  Each is measured-accept
or predicate-exact, so none can regress a flow that never needed it.

## Measured and rejected: exact net slopes (Codex P2 on #539)

`pull_f_lo/hi` are deliberately GROSS same-side anchor counts, not the net
cost derivative.  The review correctly observed they overcount when anchors
lie on both sides of the optimum (anchors 0/10/20: optimum {10}, net slope
1, gross count 2).  The exact netting — finite differences of the cost
itself, sound and direction-preserving — was implemented and **measured
worse**: it flips which knife-edge mix2 flow lands clean (`rnr/mix2`
0/0/0 → 2/8/1 against `mix2_topdown_refine` 1/0/0 → 0/0/0; A/B isolated —
the repack-interval fix from the same review is QoR-free and kept).  As a
phase-1 priority signal, "how many anchors want this move" beats the true
gradient on the corpus; only the SIGN of the derived `net_pull` carries
model semantics, and the optimum interval fixes that exactly either way.
Recorded in the code comment at the counts.

## The spreader, resolved (measured redirect)

The imagined mechanism — staggering segments that share a pull target,
priced by `pull_f_lo/hi` — was probed on the motivating vehicle and
**measurably cannot win**: on bigHalf no-rr, 14 of 15 span-starved segments
have NO supply-rich seat anywhere in their slide window (and the 15th's is
already inside its flat optimum).  Spreading segments apart manufactures no
keepout-clear tracks.  The healerless concentration loss is the
**keepout-cull class** — 102/102 of the vehicle's opens were bits admitted
via the midpoint pool and then stranded by `cull_keepout_crossers` — and
the affordable levers are supply-honest:

1. **Span-clear-first ranking** (`detailed_nuts.cpp`, Path A): when the
   midpoint fallback engages, the pool mixes guaranteed-survivor tracks
   (clear across the whole abstract span) with midpoint-only ones;
   nearest-to-anchor was blind to the difference (b21: 8 clear tracks idle,
   all 36 bits culled).  Clear tracks rank first; byte-identical when the
   span pool suffices.

2. **The cull heal** (`_final_cull_heal`, hooked at `run_detailed_nuts`):
   the refold's cull-risk tier for flows that never reach ripup, under a
   **componentwise** accept — opens strictly down AND overlaps not up (the
   refold's lexicographic trade is safe only when a later ripup grinds the
   collateral; on chip scale it bought −735 opens with +230 overlaps and
   was rejected) — bisecting the batch to its worst-cull half on rejection.
   Scoped out of bottom-up sessions (any `hier.locked` wrapper, the width
   gate's boundary): the accept protects THIS stage, but a locked-template
   flow's downstream healers re-roll from the changed start state
   (aligned_sql walked 2/16/1 → 6/58/9 before the scope-out; its motivating
   corners are healerless and unlocked, so the boundary costs nothing).

Measured (base = post-#542 main): **2 better / 0 worse / 32 unchanged** —
bigHalf no-rr 102 → **0/0** (better than the pre-interval-model 39, better
than any recorded state of that fixture), `chip3_topdown` 123/1993/141 →
**121/1731/133** (unplaced −13%), `chip_topdown` 3311 → 3307.  The chip
flows' remaining residual mass is the **reservation-conflict class** (1652
warnings on chip_topdown — occupancy at scale, the #536-adjacent decision),
not culls; the `pull_f` slopes remain available as pricing currency if a
future mechanism attacks that class.
