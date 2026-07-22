# `set_drop_dangling` modes — corpus sweep (2026-07-22)

A controlled sweep motivated by the `set_drop_dangling` mode expansion (PR
[#391](https://github.com/bulent2k2/buda/pull/391)): the original knob only had
`on`/`off`, where `on` **dropped** every candidate carrying a dangling segment
or an unbounded slide window. Dropping is the most aggressive response — it
evicts OOB detour / MST-relay candidates outright, which congested flows
sometimes need — so two gentler modes were added:

- **`clamp`** — bound every unbounded slide window to the design extent (all
  blocks' bbox + all candidate segment nominals, grown by a margin: the
  reserved `detour_channel` width if any, else a quarter-span) via a per-segment
  `Segment::perp_clamp_lo/hi`, then invalidate that candidate's cached analysis
  (`Topology::clear_analysis_cache()` — `perp_clamp` is deliberately excluded
  from the content fingerprint / `topo_uid`, so a post-generation clamp is the
  one analysis input that needs explicit invalidation). **Drops nothing**, keeps
  all candidates, does not renumber indices — so `select_topology` pins survive.
- **`clamp_drop`** — clamp the windows AND drop only the *truly* dangling
  candidates (a ConnSeg with a single connection that is not a block tap — a
  wire end to nothing). Merely-unbounded OOB detours survive, clamped.
- **`drop`** (= `on`, unchanged) — drop any dangling/unbounded candidate.

All three stay opt-in, default `off` (bit-identical). None ever touches a USER
candidate or drops the pinned candidate, and none strands a bundle.

This doc records how the five settings compare on the same corpus the
[healer-effectiveness sweep](healer_effectiveness_2026-07.md) used, so the
mode choice is grounded in measured QoR rather than intuition.

## Method

Five configs per flow, all controls held constant, driven through the full
pipeline to `run_detailed_nuts` exactly as written in each flow file (healers
included where the flow has them):

| config | `_dedup_loci` | `_drop_dangling_mode` |
|---|---|---|
| **base** | off | `off` |
| **dedup** | on | `off` |
| **drop** | off | `drop` |
| **clamp** | off | `clamp` |
| **clamp_drop** | off | `clamp_drop` |

`dedup` (the already-shipped `set_dedup_loci`) is carried along as a reference
point. Corpus = every flow with a `run_detailed_nuts` under
`flow/big_data_test/`, `flow/big_data_test/big2/`, `flow/hbundles/`,
`flow/rnr/` (34 flows). `visualize*`, `report_wl*`, and `exit` lines are
stripped so the sweep runs headless.

**Caveats.** Counts are host-sensitive (`-march=native` FP); trends, not
absolute values, are the result. Runtimes are single-run wall (healer-heavy
`rnr`/`bigHalf` flows dominate). Harness:
`scratchpad/modes_corpus.py` (measurement host, this session).

## Results (ov / un = NUTS overlaps / DNUTS opens; t = flow wall)

Of the 34 flows, ~18 route `0/0` under **every** config — omitted here. The
flows where at least one config differs from **base**:

| flow | base | dedup | drop | clamp | clamp_drop |
|---|---|---|---|---|---|
| `bigHalf` | 0/0 84s | 0/0 20s | 0/0 **9s** | 0/0 101s | 0/0 98s |
| `big_3bundles_sel_pure_mst` | 0/12 | **0/0** | **0/0** | 0/12 | 0/12 |
| `tc3a` | 870/9179 | 975/8644 | 776/**8147** | 870/9179 | 854/9361 |
| `big2/big2_noviz` | 2/28 | 2/28 | 2/**60** | 2/28 | 2/28 |
| `big2/tc3b_flat` | 2/28 | 2/28 | 2/**60** | 2/28 | 2/28 |
| `hbundles/06_multipin_stress` | 4/48 | 13/58 | 2/**36** | 4/48 | 10/54 |
| `hbundles/07_wide_fan_stress` | 0/11 | 2/2 | 0/**4** | 0/11 | 2/1 |
| `hbundles/10_chip_units_blocks_leaf` | 0/0 | 0/0 | **8/64** | 0/0 | 0/0 |
| `rnr/mix` | **0/0** | 0/0 | 0/16 | **1/2** | 0/32 |
| `rnr/mix2` | 6/73 | 2/13 | 4/68 | 6/73 | 6/73 |
| `rnr/mix2_repro` | 6/73 | 2/13 | 4/68 | 6/73 | 6/73 |
| `rnr/mix2_fast` | 33/256 | 28/242 | 26/203 | 33/256 | 32/241 |
| `rnr/mix2_fast_on_aligned_sql` | 33/256 | 28/242 | 26/203 | 33/256 | 32/241 |
| `rnr/mix2_fast_bottomup` | 17/196 | 18/158 | 18/197 | 17/196 | 17/196 |
| `rnr/mix2_fast_topdown` | 16/175 | 16/213 | 15/**258** | 16/175 | 16/175 |
| `rnr/slowdown_rnr` | 0/32 | **0/0** | **0/0** | 1/36 | 1/44 |

(The remaining flows — `b44`, `b61`, `big`, the other `big2/*`, `hbundles/01–05`,
`08`, `09`, `big_3bundles_sel_trunk+mst` — are `0/0` across all five.)

## Analysis

**`clamp` behaves as designed — the low-risk bound.** Bit-identical to base on
**32 of 34** flows. The only divergences are two healer-heavy `rnr` flows
(`mix` 0/0→1/2, `slowdown_rnr` 0/32→1/36) where bounding a slide window the
healer leaned on being wide perturbs its search slightly. It never blows
anything up — but it also does **not** deliver `drop`'s congestion wins. It is
the safe default recommendation when an expert just wants the ±2³⁰ windows
tamed without evicting candidates or renumbering indices (pins survive).

**`drop` (original) is high-variance.** Real wins — `slowdown_rnr` 0/32→**0/0**
(and 4.5× faster), `bigHalf` 9× faster at 0/0, `mix2_fast` opens 256→203 — but
real losses too: `hbundles/10` 0/0→**8/64**, `rnr/mix` 0/0→0/16,
`big2_noviz`/`tc3b_flat` 28→60, `mix2_fast_topdown` opens 175→258. It evicts OOB
detours that some congested flows depend on. Keep it opt-in; it is a tool for a
specific flow, not a corpus-wide default.

**`clamp_drop` is the worst of the three here.** Its truly-dangling drops
perturb several flows badly (`rnr/mix` 0/0→**0/32**, `hbundles/06` 48→54,
`hbundles/07` gains an overlap, `slowdown_rnr` 0/32→1/44) while rarely helping
(`mix2_fast` 256→241, `tc3a` a wash). The truly-dangling candidates it removes
turn out to be load-bearing more often than not. Not recommended as a default.

**`dedup`** (reference) is mostly a runtime win at neutral-to-better QoR
(`bigHalf` 84s→20s, `slowdown_rnr` 0/32→0/0, `mix2` 73→13) with a couple of
`hbundles` QoR shifts (`06` 48→58, `07` gains an overlap).

## Bottom line

`clamp` is the mode worth reaching for by default when an OOB / MST-relay
candidate has an unbounded window: it removes the infinite-slide pathology
without the routing damage `drop` causes on congested designs, is pin-safe, and
is a no-op almost everywhere. `drop` stays the aggressive per-flow lever.
`clamp_drop` is documented for completeness but not recommended — dropping the
truly-dangling candidates costs more than it saves on this corpus. All modes
remain opt-in, default `off`.

See also: [`docs/script_reference/topologies.md`](../script_reference/topologies.md)
(`set_drop_dangling`), [healer effectiveness sweep](healer_effectiveness_2026-07.md)
(same corpus).
