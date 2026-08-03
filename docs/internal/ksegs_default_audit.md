# kSegs-by-default audit — the relative formulation and its gating issues

The question: what stops `kSegs = <relative-to-max-possible-HPWL>` from
being the DEFAULT for all flows?  This documents the `kSegsRel` experiment
(fraction of the design's max-possible HPWL = Hanan grid extent `W + H`,
resolved into the effective per-segment penalty once the grids are known)
and the measured answer.  **Outcome: 0.02 is now the COMPILED DEFAULT**
(`CongestionPlanner::kDefaultKSegsRel`), gated as derived below; env
`BUDA_KSEGS_REL` overrides it for study runs (`"0"` disables).
Background: `set_planner_param kSegs` (absolute units) and the findings
log in `flow/big_data_test/b61.buda`.

## Why relative at all

The absolute `kSegs 500` that healed big2 (max-HPWL ≈ 24k → ~2% relative)
was an effective ~70% on mempool_tile (max-HPWL ≈ 720) and ~46% on
channel_stress — one absolute value was never one knob.  The relative form
bounds the penalty by design scale, and the first result confirms it:
**channel_stress's 3→7-unplaced regression under absolute 500 vanishes at
every α ≤ 0.25** — it was an artifact of absolute units, not of the
penalty idea.

## Corpus sweep (plain pipelines, α = fraction of max-HPWL)

| flow | α=0 | α=0.02 | α=0.05 | α=0.1 | α=0.25 |
|---|---|---|---|---|---|
| b61 (segs/detWL) | 10 / 244.8K | **5 / 250.1K** | 5 / 250.1K | 3 / 307.3K | 3 / 307.3K |
| b44 | 2 / 193.4K | = | = | = | = |
| quickstart | = | = | = | = | = |
| comprehensive_demo (segs/vias) | 26 / 110 | 21 / 88 | **14 / 63** | 14 / 63 | 14 / 63 |
| ariane133_cache | = | = | = | = | = |
| mempool_tile (detWL/unpl) | 529.8K / 2971 | 274.8K / 2102 | 258.0K / 2140 | **245.5K / 1756** | 245.5K / 1756 |
| channel_stress (unpl) | 3 | 3 | 3 | 3 | 3 |
| 05_stress_grid (detWL/vias) | 34.7K / 168 | 31.3K / 144 | 30.4K / 108 | **26.5K / 28** | 26.5K / 28 |
| 07_wide_fan_stress (unpl/ovl) | **0 / 0** | 2 / 0 | 3 / 5 | 3 / 1 | 3 / 2 |
| big2 (unpl/ovl) | 108 / 4 | 8 / 2 | 44 / 3 | 48 / 2 | **0 / 0** |
| bigHalf (detWL/unpl) | 14.78M / 593 | **13.73M / 398** | 14.17M / 151 | 14.60M / 232 | 14.89M / 144 |

α = 0.02 is the Pareto point: every flow improves or holds except
07_wide_fan_stress.

## Suite churn at α = 0.02 (fast+mid, default flipped via env)

10 failures / 1472 tests, fully characterized:

- 2 × `test_planner_ksegs` — definitional (they assert the default is off);
  rewritten in any flip, and now env-shielded.
- 4 × `test_flow_scripts` + 1 × NUTS placement golden — segment-count /
  placement goldens; mechanical churn.
- 1 × `test_planner_charge_pull_target` (demo-b3) — the flow got BETTER:
  the level-1 keepout strand the test needs as its premise no longer
  occurs (0 unplaced at level 1).
- 1 × `test_datapath_multi_trunk_qor` → gating issue G3.
- 1 × `test_planner_kpeak` steer test → gating issue G4.

## The gating issues

- **G1 — a structural loser exists (07_wide_fan_stress) — RESOLVED
  (healer gate).**  Even α=0.02 (effective ~30 units/seg) strands 2 bits;
  the flow's wide-fan tree DOF is load-bearing at any penalty.  No local
  signal separates it from mempool (where compact shapes REDUCE demand
  and heal 1200+ strands) — only the measured-metric loop can:
  `ripup_reroute` heals it at +5.8% detWL / −25% vias.  A default is only
  safe **with healers in the flow** — and now IS gated on exactly that:
  the planner param `healersAhead` (default 0) is declared by the session
  when the flow script contains `ripup_reroute`/`negotiate_congestion`
  (`_healers_in_flow`, scanning `source`d files recursively; explicit
  `set_planner_param healersAhead 1` is the harness escape), and the
  `BUDA_KSEGS_REL` env default stands down without it.  Lock-in:
  `test_ksegs_env_default_healer_gated` (scriptless suppression + note,
  explicit declaration, and script detection through a sourced
  sub-script).
- **G2 — big2's response is jagged — RESOLVED (same gate).**
  Non-monotone in α (8 → 44 → 48 → 0 unplaced across 0.02→0.25): the
  selection landscape is knife-edged, so no tuned α is a reliable "clean
  point" without healers (with rr, every α is clean).  The healer gate
  above is exactly the guard: the default only applies where the loop
  that flattens the jaggedness runs.
- **G3 — the penalty fights deliberately-multi-segment structures.**
  `multi_trunk`'s BITRUNK datapath trees are demoted (1 of the expected 2
  trees selected at α=0.02).  This splits in two:
  - **G3a (RESOLVED — taper-honest weight):** for per-bit TAPERED trees
    (fan-in bundles with `Topology::seg_bits`, derived before planning by
    `_derive_fanin_bits_all`) the penalty now charges each segment its
    MEMBER-BIT share — `Σ seg_bit_count/nbits`, the per-bit average path
    length, which is what junction vias actually scale with.  Untapered
    candidates reduce to `n_segments` exactly (corpus numbers unchanged —
    the big flows bundle STRICT).  Lock-in: a CONVERGENT fan-in trunk
    (3 segments, w_segs 2.0) keeps winning at kSegs=100 where raw-nseg
    pricing flips to a 2-seg shape
    (`test_ksegs_taper_honest_weight_keeps_fanin_tree`).
  - **G3b (RESOLVED — the intent hierarchy):** datapath BITRUNK trees are
    NOT tapered (every bit multicasts to every receiver, so `seg_bits` is
    empty and every bit really does traverse the whole tree).  Measured
    at α=0.02 (effective ~52/seg on the 2.6k-extent datapath): abstract
    WL REGRESSED — col plain 18191→22321 (+23%), multi 17346→20356
    (+17%); row multi 17244→19947 (+16%), losing its edge over plain.
    Two fixes landed, in layers:
    1. *Candidate exemption:* the gated two-level trees (BITRUNK_HVH/VHV
       — they exist only under the `multi_trunk` flag) are exempt from
       the penalty.  Necessary but NOT sufficient: col recovered (trees
       2→3, multi WL −3.1%), but on row the GREEDY COUPLING remained —
       neighbors' penalty-shifted selections strand the field in a
       clean-but-worse optimum (+15.7% WL) that neither refine_passes
       nor ripup touches (healers heal correctness, not WL; the result
       is 0/0).
    2. *Env-default stand-down:* the intent hierarchy is
       `explicit set_planner_param  >  multi_trunk opt-in  >  env
       default` — a design whose pools carry gated trees suppresses the
       BUDA_KSEGS_REL env default entirely (note printed), restoring the
       kSegs=0 result byte-identically; an explicit kSegs/kSegsRel still
       applies in full (with the trees exempt).
    Lock-in: `test_ksegs_env_default_stands_down_for_multi_trunk`; the
    datapath QoR test passes with and without the env default.
- **G4 — a segment penalty is a detour penalty (RESOLVED — intent
  hierarchy).**  kPeak's routability steering (U-detour off a loaded
  band) is overwhelmed even at effective ~30 units/seg in the synthetic
  steer test: detours cost 2 extra segments, so kSegs systematically
  biases INTO contended corridors that kPeak prices away — exactly the
  sub-capacity band where kPeak is the only signal (an overflowing
  straight shape already loses STRICT, so the ladder covers the
  over-capacity case).  Resolution: kPeak is an explicit routability
  opt-in, so it outranks the env default the same way multi_trunk does
  — `BUDA_KSEGS_REL` stands down when kPeak > 0 (note printed), and an
  explicit kSegs/kSegsRel set alongside kPeak is the user's own
  calibration and applies in full.  Lock-in:
  `test_ksegs_env_default_stands_down_for_kpeak` (detour kept under the
  env flip; explicit-both goes straight — the owned trade).
- **G5 — α tension across flows.**  b61 wants ≤0.05 (0.1 over-compacts to
  the 3-seg shape at +25% WL), mempool/05 want ≥0.1 for their full wins,
  big2 wants 0.25-or-healers.  0.02 is safe-Pareto, not optimal-everywhere.
- **G6 — mechanical churn — DISSOLVED by the gates.**  The original 10
  suite failures under the env flip existed because the default applied
  unconditionally.  With the G1–G4 gates in place, the FULL fast+mid
  suite passes **identically with and without `BUDA_KSEGS_REL=0.02`**
  (1527/1527): scriptless test sessions suppress via the healer gate, and
  the multi_trunk / kPeak flows via their own.  Nothing to absorb.

## Verdict

All six gates are addressed:

1. ~~bits-weighted penalty (G3a)~~ — DONE: `Σ seg_bit_count/nbits`;
2. ~~multicast-tree tension (G3b)~~ — DONE: candidate exemption + the
   intent hierarchy (`explicit param > opt-in > env default`);
3. ~~congestion subordination (G4)~~ — DONE: the env default stands down
   for explicit kPeak steering;
4. ~~healer gating (G1/G2)~~ — DONE: `healersAhead`, session-declared
   from the flow script;
5. ~~golden churn (G6)~~ — dissolved: zero suite churn under the flip.

`kSegsRel = 0.02` is a SAFE standing default: it engages only in
healer-running flows without multi_trunk/kPeak opt-ins — exactly where it
was measured to only win (big2 108 unpl/4 ovl → clean-or-healed at +1.8%
WL −19% vias, bigHalf −3.1% WL −29% vias, mempool −48% WL, 07 healed at
+5.8% WL −25% vias).

**PROMOTED — the 0.02 is now COMPILED IN**
(`CongestionPlanner::kDefaultKSegsRel`), gated identically to the env
hook it replaces.  Resolution order for an UNSET `kSegsRel`:
`BUDA_KSEGS_REL` if present (the study override — `"0"` disables the
default outright, even in a healer flow), else the compiled 0.02; either
way the value is non-explicit and passes through the G1–G4 gates
(healers ahead, no multi_trunk trees in the pools, no kPeak).  An
explicit `set_planner_param kSegs/kSegsRel` bypasses the gates entirely,
including an explicit 0.  Verified at the flip: fast+mid 1529 passed
with zero churn, slow tier green.  Lock-in:
`test_ksegs_compiled_default_engages_gated` (scriptless-env healer flow
selects the 5-seg shape; `BUDA_KSEGS_REL=0` restores the 10-seg
WL-cheapest tree).

## The six healing flows that deliberately do NOT declare `healersAhead`

**Measured 2026-08-03 — declaring is 0 better / 3 worse / 3 QoR-neutral.**

Six corpus flows run healers WITHOUT `set_planner_param healersAhead 1`,
while their parent `flow/rnr/mix2.buda` declares it: the `mix2_fast*` family
plus `mix2_topdown_refine`.  That reads as an oversight — and it is not.
Adding the declaration turns on BOTH gated behaviors (this document's
`kSegsRel` proactive default, which changes SELECTION, and the `run_nuts`
dead-span auto-escalation), and on these congested vehicles the trajectory
lands worse:

| flow | baseline (ov/unpl/viol) | +`healersAhead` | detailed WL | verdict |
|---|---|---|---|---|
| `mix2_fast_bottomup_shared` | 0 / 0 / 0 | **10 / 112 / 13** | +5.3% | WORSE (badly) |
| `mix2_fast_bottomup_caps` | 2 / 0 / 0 | **2 / 16 / 1** | −3.6% | WORSE |
| `mix2_fast_on_aligned_sql` | 2 / 16 / 1 | **3 / 30 / 2** | +0.1% | WORSE |
| `mix2_fast_bottomup` | 0 / 0 / 0 | 0 / 0 / 0 | +0.6% | QoR-neutral |
| `mix2_fast_topdown` | 0 / 0 / 0 | 0 / 0 / 0 | **+8.0%** | QoR-neutral, worse WL |
| `mix2_topdown_refine` | 0 / 0 / 0 | 0 / 0 / 0 | **−5.2%** | QoR-neutral, BETTER WL |

This is not a counter-example to the G1–G4 gating conclusion above: that
conclusion is that the default only ENGAGES where it was measured to win,
and these ablation variants were never in the measured set.  Their
checked-in QoR reflects the UNDECLARED configuration, so flipping them now
is a QoR change, not a cleanup.

Each of the six carries a NOTE at its `run_planner` line pointing here, so
the omission is not "fixed" by a later reader.  `mix2_topdown_refine` is
called out honestly as the one open judgment call — declaring would hold its
QoR and improve detailed WL 5.2%; it is left undeclared only for family
consistency, and taking that win is a legitimate future change.

Method note: these are QoR metrics (deterministic per host), so single-run
A/B is sound evidence here — unlike wall-time, see
[`qor_corpus.md`](qor_corpus.md) "Measuring a per-flow COST".
