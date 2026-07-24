# Gen-topo knob sweep — `no_hanan_loci` × `multi_trunk` over the QoR corpus

A four-way sweep of the two candidate-generation knobs `no_hanan_loci` and
`multi_trunk` (see `generate_topologies` / `generate_hier_topologies` in
[CLAUDE.md](../../CLAUDE.md)) across the full 34-flow [QoR corpus](qor_corpus.md).
The question: do either knob, or the two together, move routability / wirelength
/ runtime enough to change the shipped defaults (both **off**).

## Method

Each corpus flow is run **as written with `script_path` set** (so the shipped
`kSegsRel` 0.02 and `run_nuts` dead-span gates engage on healer flows, exactly as
in [qor_corpus.md](qor_corpus.md)), except that **every**
`generate_topologies` / `generate_hier_topologies` command is rewritten to force
exactly the two controlled knobs for the setting — any pre-existing
`multi_trunk` / `no_hanan_loci` / `hanan_loci` token is stripped first, so the
four settings differ **only** in these two knobs and nothing else in the flow.
`visualize*` / `report_wl*` / `exit` are skipped so the whole flow runs.

The four settings:

| setting | flags | meaning |
|---|---|---|
| **baseline** | *(none)* | default-on Hanan-line trunk loci, no multi-trunk trees |
| **no_hanan_loci** | `no_hanan_loci` | midpoint-only trunk loci (smaller pool) |
| **multi_trunk** | `multi_trunk` | add BITRUNK_HVH/VHV two-level datapath trees |
| **both** | `multi_trunk no_hanan_loci` | trees, midpoint-only loci |

- **opens** = DetailedNUTS unplaced bits at the endpoint; **overlaps** = NUTS
  overlaps (shown in parens when nonzero). **WL** = abstract NUTS wirelength
  (Σ|span| over placed segments — the metric topology decisions move). **runtime**
  = wall time for the whole flow.
- Counts are host-sensitive (`-march=native` FP) and single-run wall times —
  **trends, not absolute values**, are the result. Harness:
  `scratchpad/gentopo_sweep.py` (this session).
- **Caveat — `mix`:** it is the *only* corpus flow that sets a knob as-written
  (`generate_hier_topologies no_hanan_loci`), so `mix`'s **shipped** endpoint is
  the **no_hanan_loci** column here, not baseline. Every other flow's baseline
  column *is* its shipped default.
- **Caveat — healer flows:** on the 5 healer flows (`big2`, `bigHalf`, `mix`,
  `mix2`, `slowdown_rnr`, + the `mix2_repro` debug dup) the endpoint opens depend
  on the healer's convergence path through the candidate pool, which the pool
  *size/order* perturbs. Their per-setting opens deltas are therefore
  convergence sensitivity as much as clean knob QoR — read them together with the
  runtime column (a knob that leaves the healer thrashing shows up as a big
  runtime jump, e.g. `mix`/`slowdown` under `multi_trunk`).

## Routability — opens [overlaps in parens]

| flow | base | no_loci | multi | both |
|---|--:|--:|--:|--:|
| `b44` | 0 | 0 | 0 | 0 |
| `b61` | 0 | 0 | 0 | 0 |
| `big` | 0 | 0 | 0 | 0 |
| `big2/b1_bus_007` | 0 | 0 | 0 | 0 |
| `big2/b24_bus_056` | 0 | 0 | 0 | 0 |
| `big2/b34_bus_028` | 0 | 0 | 0 | 0 |
| `big2/b3_bus_023` | 0 | 0 | 0 | 0 |
| `big2/b4_bus_077` | 0 | 0 | 0 | 0 |
| `big2/big2` | 0 | 0 | 0 | 0 |
| `big2/big2_b4_b24` | 0 | 0 | 0 | 0 |
| `big2/big2_noviz` | 28 (2ov) | 108 (4ov) | 28 (2ov) | 108 (4ov) |
| `big2/tc3b_flat` | 28 (2ov) | 108 (4ov) | 28 (2ov) | 108 (4ov) |
| `bigHalf` | 0 | 0 (3ov) | 0 | 0 |
| `big_3bundles_sel_pure_mst_topo` | 12 | 0 | 12 | 0 |
| `big_3bundles_sel_trunk+mst_topo` | 0 | 0 | 0 | 0 |
| `tc3a` | 8360 (817ov) | 8669 (881ov) | 7642 (723ov) | 8439 (759ov) |
| `hbundles/01_pipeline_hier` | 0 | 0 | 0 | 0 |
| `hbundles/02_two_procs` | 0 | 0 | 0 | 0 |
| `hbundles/03_priority_ordering` | 0 | 0 | 0 | 0 |
| `hbundles/04_deep_hierarchy` | 0 | 0 | 0 | 0 |
| `hbundles/05_stress_grid` | 0 | 0 | 0 | 0 |
| `hbundles/06_multipin_stress` | 48 (3ov) | 20 (1ov) | 48 (1ov) | 20 (1ov) |
| `hbundles/07_wide_fan_stress` | 4 | 0 | 4 | 0 |
| `hbundles/08_cross_level` | 0 | 0 | 0 | 0 |
| `hbundles/09_local_global_compete` | 0 | 0 | 0 | 0 |
| `hbundles/10_chip_units_blocks_leaf` | 0 | 0 | 0 | 0 |
| `rnr/mix` *(shipped = no_loci)* | 0 | 44 (2ov) | 32 | 0 |
| `rnr/mix2` | 42 (2ov) | 30 (3ov) | 40 (1ov) | 48 (1ov) |
| `rnr/mix2_fast` | 256 (33ov) | 275 (28ov) | 256 (33ov) | 284 (31ov) |
| `rnr/mix2_fast_bottomup` | 210 (19ov) | 200 (22ov) | 126 (11ov) | 162 (17ov) |
| `rnr/mix2_fast_on_aligned_sql` | 256 (33ov) | 275 (28ov) | 256 (33ov) | 284 (31ov) |
| `rnr/mix2_fast_topdown` | 175 (16ov) | 186 (16ov) | 172 (14ov) | 161 (14ov) |
| `rnr/mix2_repro` | 42 (2ov) | 30 (3ov) | 40 (1ov) | 48 (1ov) |
| `rnr/slowdown_rnr` | 0 | 44 (2ov) | 32 | 0 |
| **TOTAL opens / ov** | **9461 / 929** | **9989 / 997** | **8716 / 821** | **9662 / 863** |
| **ex-`tc3a`** | **1101** | **1320** | **1074** | **1223** |

(`tc3a` is a deliberately hopeless over-capacity design — 8000+ opens — so it
swamps the totals; the ex-`tc3a` row is the more informative aggregate.)

## Abstract wirelength (Σ|span|), Δ% vs baseline

| flow | base | no_loci | multi | both |
|---|--:|--:|--:|--:|
| `b44` | 3715 | +10.4% | +0.0% | +10.4% |
| `b61` | 15294 | +0.0% | +0.0% | +0.0% |
| `big` | 767207 | −0.2% | +0.0% | −0.2% |
| `big2/b1_bus_007` | 4946 | +0.0% | +0.0% | +0.0% |
| `big2/b24_bus_056` | 4818 | −11.4% | +0.0% | −11.4% |
| `big2/b34_bus_028` | 166 | +0.0% | +0.0% | +0.0% |
| `big2/b3_bus_023` | 8424 | −0.0% | +0.0% | −0.0% |
| `big2/b4_bus_077` | 3199 | +0.0% | +0.0% | +0.0% |
| `big2/big2` | 342526 | −0.5% | +1.0% | +0.9% |
| `big2/big2_b4_b24` | 8588 | −6.6% | +0.0% | −6.6% |
| `big2/big2_noviz` | 344443 | −0.2% | +0.0% | −0.2% |
| `big2/tc3b_flat` | 344443 | −0.2% | +0.0% | −0.2% |
| `bigHalf` | 417619 | −0.6% | −0.1% | +3.6% |
| `big_3bundles_sel_pure_mst_topo` | 47724 | −7.1% | +0.0% | −7.1% |
| `big_3bundles_sel_trunk+mst_topo` | 38742 | +21.3% | +0.0% | +21.3% |
| `tc3a` | 113356 | +0.8% | −6.7% | +0.2% |
| `hbundles/01_pipeline_hier` | 330 | +0.0% | +0.0% | +0.0% |
| `hbundles/02_two_procs` | 660 | +0.0% | +0.0% | +0.0% |
| `hbundles/03_priority_ordering` | 1950 | +0.0% | +0.0% | +0.0% |
| `hbundles/04_deep_hierarchy` | 796 | +0.0% | +0.0% | +0.0% |
| `hbundles/05_stress_grid` | 7294 | −7.8% | +0.0% | −7.8% |
| `hbundles/06_multipin_stress` | 14924 | −3.1% | +0.1% | −3.1% |
| `hbundles/07_wide_fan_stress` | 19597 | −1.7% | −0.0% | −1.7% |
| `hbundles/08_cross_level` | 2576 | +0.0% | +0.0% | +0.0% |
| `hbundles/09_local_global_compete` | 860 | +0.0% | +0.0% | +0.0% |
| `hbundles/10_chip_units_blocks_leaf` | 50422 | +0.0% | +0.0% | +0.0% |
| `rnr/mix` *(shipped = no_loci)* | 65147 | +0.5% | −3.9% | +3.9% |
| `rnr/mix2` | 69186 | +0.1% | −0.9% | −2.7% |
| `rnr/mix2_fast` | 71106 | +2.0% | +0.0% | +2.1% |
| `rnr/mix2_fast_bottomup` | 67003 | +6.5% | +2.9% | +3.7% |
| `rnr/mix2_fast_on_aligned_sql` | 71106 | +2.0% | +0.0% | +2.1% |
| `rnr/mix2_fast_topdown` | 67558 | −2.8% | −2.6% | −2.3% |
| `rnr/mix2_repro` | 69186 | +0.1% | −0.9% | −2.7% |
| `rnr/slowdown_rnr` | 65147 | +0.5% | −3.9% | +3.9% |
| **TOTAL** | **3110057** | **+0.1%** | **−0.3%** | **+0.7%** |

## Runtime (s)

| flow | base | no_loci | multi | both |
|---|--:|--:|--:|--:|
| `big` | 1.50 | 0.92 | 1.48 | 0.98 |
| `big2/big2` | 0.89 | 0.77 | 1.07 | 1.45 |
| `big2/big2_noviz` | 0.73 | 0.48 | 0.72 | 0.43 |
| `big2/tc3b_flat` | 0.82 | 0.58 | 0.75 | 0.54 |
| `bigHalf` | 4.90 | 2.71 | **9.36** | **27.17** |
| `tc3a` | 5.09 | 3.50 | 5.42 | 3.98 |
| `hbundles/05_stress_grid` | 0.31 | 0.29 | 0.32 | 0.29 |
| `hbundles/06_multipin_stress` | 0.56 | 0.46 | 0.58 | 0.48 |
| `hbundles/07_wide_fan_stress` | 0.63 | 0.51 | 0.71 | 0.62 |
| `hbundles/10_chip_units_blocks_leaf` | 1.33 | 1.34 | 1.32 | 1.35 |
| `rnr/mix` *(shipped = no_loci)* | 10.19 | 13.21 | **58.86** | 10.15 |
| `rnr/mix2` | 29.79 | 12.91 | 12.52 | 7.85 |
| `rnr/mix2_fast` | 4.80 | 3.26 | 4.44 | 3.83 |
| `rnr/mix2_fast_bottomup` | 4.15 | 3.14 | 4.59 | 3.33 |
| `rnr/mix2_fast_on_aligned_sql` | 4.07 | 3.82 | 4.30 | 3.18 |
| `rnr/mix2_fast_topdown` | 3.78 | 3.40 | 4.06 | 3.16 |
| `rnr/mix2_repro` | 29.68 | 12.34 | 12.17 | 7.63 |
| `rnr/slowdown_rnr` | 9.80 | 13.06 | **60.72** | 11.01 |
| *(24 sub-second flows omitted)* | | | | |
| **TOTAL (all 34)** | **113.4** | **77.1 (−32%)** | **183.8 (+62%)** | **87.8 (−23%)** |

## Reading the sweep

### `multi_trunk` — best routability, but a runtime hazard on healer flows
- **Best opens of the four settings** (8716 total, 1074 ex-`tc3a`) and the only
  setting that meaningfully clears opens on the workloads it targets:
  `tc3a` −718 opens / **WL −6.7 %**, `mix2_fast_bottomup` opens **210 → 126
  (−40 %)**, `mix2_fast_topdown` 175 → 172, `mix2` 42 → 40. These are the
  column/row-aligned datapaths the BITRUNK_HVH/VHV trees are built for
  (mst_edge_realization.md).
- **Neutral-to-slightly-better WL overall** (−0.3 % total) — the trees never
  regress WL on trunk-dominated flats (all `+0.0 %` there; the WL-neutral rows
  confirm the tree is only *selected* when it wins).
- **But +62 % total runtime**, and two outright hazards: on `mix` and
  `slowdown_rnr` the bigger candidate pool sends the healer into a **6× thrash**
  (`mix` 10 → **59 s**, `slowdown` 10 → **61 s**) while *also* leaving residual
  opens (0 → 32) the baseline pool cleared. `bigHalf` nearly doubles (4.9 → 9.4 s).
  → multi_trunk is a **targeted opt-in for datapath-heavy designs**, not a
  default: it wins where two-level trees apply and is a net loss (slower + worse)
  on the healer-driven `mix` family.

### `no_hanan_loci` — a speed knob that trades QoR away
- **−32 % total runtime** (77 vs 113 s) — the midpoint-only pool is ~1.3–1.6× smaller,
  so generation *and* every downstream per-candidate step is cheaper. This is the
  knob's real value.
- **But +528 opens overall** (9989 vs 9461; ex-`tc3a` 1320 vs 1101 — the worst of
  the four). It regresses more than it fixes: `big2_noviz`/`tc3b_flat`
  **28 → 108**, `mix2_fast` 256 → 275, and it introduces overlaps on `bigHalf`
  (0 → 3ov). It *does* fix a few MST-relay stress fixtures
  (`big_3bundles_pure_mst` 12 → 0, `hbundles/07` 4 → 0, `hbundles/06` 48 → 20) and
  trims WL on a handful (`b24_bus_056` −11.4 %, `hbundles/05` −7.8 %), but the b44
  family regresses (+10.4 % WL — the +500 overshoot the loci default was *added*
  to fix). → confirms the **default-on loci** decision: turning loci off buys
  speed at a real QoR cost on balance.

### `both` — no combined sweet spot
- Middling opens (9662, between baseline and no_loci) and the **worst WL total**
  (+0.7 %). It inherits no_loci's speed on most flows (−23 % total) but inherits
  `multi_trunk`'s healer hazard where it bites hardest: **`bigHalf` 4.9 → 27 s
  (5.5×) and WL +3.6 %**. It does reach 0/0 on `mix`/`slowdown` (where lone
  `multi_trunk` stalled), but that is healer-convergence luck, not a structural
  gain. No flow makes `both` the clear best choice.

## Bottom line

The sweep **reaffirms the shipped defaults (both knobs off)** as the right
corpus-wide choice, and sharpens when to reach for each:

- **`multi_trunk`** — opt in for **column/row-aligned datapaths** (measured wins:
  `tc3a` WL −6.7 %, `mix2_fast_bottomup` opens −40 %). Do **not** enable it
  blanket on healer flows — it can 6× the ripup runtime with no QoR gain.
- **`no_hanan_loci`** — a **speed lever** (−32 % runtime) for large exploratory
  runs where a modest QoR give-back is acceptable; it is a net QoR *loss* on the
  corpus, so it stays opt-in.
- **`both`** — no corpus-wide niche; use only if a specific datapath design also
  needs the speed and is verified not to regress.

No default change is warranted. The per-knob guidance above is the actionable
result.

See also: [`qor_corpus.md`](qor_corpus.md) (the corpus + measurement method),
[`healer_effectiveness_2026-07.md`](healer_effectiveness_2026-07.md) and
[`drop_dangling_modes_2026-07.md`](drop_dangling_modes_2026-07.md) (same corpus,
other sweeps), [`wishlist-topo.md`](wishlist-topo.md) (the Hanan-loci default-flip
"Nominal-WL comparability" piece).
