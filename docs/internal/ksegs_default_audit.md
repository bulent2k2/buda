# kSegs-by-default audit — the relative formulation and its gating issues

The question: what stops `kSegs = <relative-to-max-possible-HPWL>` from
being the DEFAULT for all flows?  This documents the `kSegsRel` experiment
(fraction of the design's max-possible HPWL = Hanan grid extent `W + H`,
resolved into the effective per-segment penalty once the grids are known;
env `BUDA_KSEGS_REL` supplies the default for study runs) and the measured
answer.  Background: `set_planner_param kSegs` (absolute units) and the
findings log in `flow/big_data_test/b61.buda`.

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

- **G1 — a structural loser exists (07_wide_fan_stress).**  Even α=0.02
  (effective ~30 units/seg) strands 2 bits; the flow's wide-fan tree DOF
  is load-bearing at any penalty.  No local signal separates it from
  mempool (where compact shapes REDUCE demand and heal 1200+ strands) —
  only the measured-metric loop can: `ripup_reroute` heals it at +5.8%
  detWL / −25% vias.  A default is only safe **with healers in the flow**.
- **G2 — big2's response is jagged.**  Non-monotone in α (8 → 44 → 48 → 0
  unplaced across 0.02→0.25): the selection landscape is knife-edged, so
  no tuned α is a reliable "clean point" without healers (with rr, every
  α is clean).
- **G3 — the penalty fights deliberately-multi-segment structures.**
  `multi_trunk`'s BITRUNK datapath trees are demoted (1 of the expected 2
  trees selected at α=0.02).  Root cause is the penalty's implicit cost
  model: it charges `nseg × (all bits)`, but per-bit TAPERED trees route
  each bit over only its own path — their real via cost does NOT scale
  with raw segment count.  Refinement path: weight the penalty by
  bits-per-segment (`Topology::seg_bits`) instead of nseg.
- **G4 — a segment penalty is a detour penalty.**  kPeak's routability
  steering (U-detour off a loaded band) is overwhelmed even at effective
  ~30 units/seg in the synthetic steer test: detours cost 2 extra
  segments, so kSegs systematically biases INTO contended corridors that
  kPeak/kCong try to price away.  Same mechanism as G1.  Any default must
  keep the penalty subordinate to the congestion terms (or exempt
  same-endpoint detour variants).
- **G5 — α tension across flows.**  b61 wants ≤0.05 (0.1 over-compacts to
  the 3-seg shape at +25% WL), mempool/05 want ≥0.1 for their full wins,
  big2 wants 0.25-or-healers.  0.02 is safe-Pareto, not optimal-everywhere.
- **G6 — mechanical churn.**  The golden/behavioral updates above, plus
  docs and the demo-b3 test premise.

## Verdict

`kSegsRel ≈ 0.02` **with healers in the flow** loses nowhere measured and
wins broadly; without healers, G1/G2 are real correctness risks and G3/G4
are QoR distortions.  The principled path to default-on:

1. bits-weighted penalty (G3) — charge `Σ seg_bits`, not `nseg × bits`;
2. keep it subordinate to congestion terms or exempt detour variants (G4);
3. gate the default on healer presence (G1/G2), i.e. flows running
   `negotiate_congestion`/`ripup_reroute`;
4. then absorb the golden churn (G6).
