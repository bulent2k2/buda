# QoR corpus — the 34 routing flows

The standing QoR benchmark set: every `.buda` flow that runs the **full pipeline
through `run_detailed_nuts`** under `flow/big_data_test/`,
`flow/big_data_test/big2/`, `flow/hbundles/`, and `flow/rnr/`. This is the
corpus the [`set_drop_dangling` modes sweep](drop_dangling_modes_2026-07.md) and
the [healer-effectiveness sweep](healer_effectiveness_2026-07.md) measure over,
and the reference set for any planner/NUTS change that claims a corpus-wide
result.

Regenerate the list with:

```bash
for d in flow/big_data_test flow/big_data_test/big2 flow/hbundles flow/rnr; do \
  grep -rl run_detailed_nuts $d/*.buda; done | sort -u
```

## How this table was measured

Each flow is run **as written with `script_path` set** — every command executed
except `visualize*` / `report_wl*` / `exit`. For all but one flow this is
exactly what the shipped CLI does. The exception is the stripped `exit`: the
internal CLI raises `SystemExit` on `exit`, and **`rnr/mix2_repro.buda` has a
mid-script `exit` (line 13, right after `run_nuts`)** ahead of its healers and
`run_detailed_nuts`, so under the real CLI it stops there. Its row below is the
harness running the *whole* file past that exit — a debug repro of `mix2` whose
endpoint therefore mirrors `mix2`'s, **not** a shipped CLI endpoint (marked
`†`). Every other corpus flow has no pre-`run_detailed_nuts` exit (verified), so
for them the run *is* the shipped one. Because `script_path` is set, the shipped
`kSegsRel` 0.02 default and the `run_nuts` dead-span escalation engage on flows
that contain a healer (the `_healers_in_flow` gate), so `overlaps`/`opens` here
are the **real shipped endpoints** (`mix2_repro` excepted).

> This differs from the `drop_dangling_modes` sweep's `base` column, which used a
> *scriptless* harness that suppresses `kSegsRel`/dead-span on both sides (a fair
> mode-isolation regime, but not the shipped one). The two agree on every
> **healer-less** flow (`big2_noviz` 2/28, `tc3b_flat` 2/28, `tc3a` 870/9179,
> `hbundles/06` 4/48, …) and diverge only on **healer** flows where `kSegsRel`
> engages here (e.g. `slowdown_rnr` 0/0 here vs 0/32 scriptless; `mix2` 2/42 vs
> 6/73). See [`dedup_default_2026-07.md`](dedup_default_2026-07.md) for the
> `kSegsRel`-confound analysis.

Counts are host-sensitive (`-march=native` FP) and single-run wall times;
trends, not absolute values, are the result. Harness:
`scratchpad/qor_corpus.py` (measurement host, this session).

## Columns

- **heal** — healer commands in the flow: `none`, `nc` (`negotiate_congestion`
  only), `rr` (`ripup_reroute` only), or `both`. *(This corpus only exercises
  `none` and `both`.)*
- **nets** — logical net count (`bdb.all_nets()` for a BDB-backed flow, else the
  bit count). In BUDA a net **is** a single bit-wire, so `nets == bits` except
  where not every net ends up bundled (`hbundles/08`: 60 nets, 56 bits).
- **bits** — total routed bit-wires: Σ per-bundle net-names, post-expansion (a
  hier template counts once per instance).
- **bundles** — number of routed bundles (`len(session.bundles)`; post-expansion
  per-instance count for hier flows).
- **overlaps** — NUTS overlaps at the endpoint.
- **opens** — DetailedNUTS unplaced bits at the endpoint.
- **runtime** — wall time for the whole flow.

## The corpus

| flow | heal | nets | bits | bundles | overlaps | opens | runtime |
|---|---|--:|--:|--:|--:|--:|--:|
| `big_data_test/b44.buda` | none | 52 | 52 | 1 | 0 | 0 | 0.0s |
| `big_data_test/b61.buda` | none | 16 | 16 | 1 | 0 | 0 | 0.0s |
| `big_data_test/big.buda` | none | 2840 | 2840 | 80 | 0 | 0 | 1.5s |
| `big_data_test/big2/b1_bus_007.buda` | none | 28 | 28 | 1 | 0 | 0 | 0.0s |
| `big_data_test/big2/b24_bus_056.buda` | none | 48 | 48 | 1 | 0 | 0 | 0.1s |
| `big_data_test/big2/b34_bus_028.buda` | none | 28 | 28 | 1 | 0 | 0 | 0.0s |
| `big_data_test/big2/b3_bus_023.buda` | none | 1 | 1 | 1 | 0 | 0 | 0.0s |
| `big_data_test/big2/b4_bus_077.buda` | none | 60 | 60 | 1 | 0 | 0 | 0.1s |
| `big_data_test/big2/big2.buda` | both | 2840 | 2840 | 80 | 0 | 0 | 0.8s |
| `big_data_test/big2/big2_b4_b24.buda` | none | 108 | 108 | 2 | 0 | 0 | 0.0s |
| `big_data_test/big2/big2_noviz.buda` | none | 2840 | 2840 | 80 | 2 | 28 | 0.5s |
| `big_data_test/big2/tc3b_flat.buda` | none | 2840 | 2840 | 80 | 2 | 28 | 0.6s |
| `big_data_test/bigHalf.buda` | both | 2840 | 2840 | 80 | 0 | 0 | 7.8s |
| `big_data_test/big_3bundles_sel_pure_mst_topo.buda` | none | 100 | 100 | 3 | 0 | 12 | 0.0s |
| `big_data_test/big_3bundles_sel_trunk+mst_topo.buda` | none | 100 | 100 | 3 | 0 | 0 | 0.0s |
| `big_data_test/tc3a.buda` | none | 2840 | 2840 | 80 | 870 | 9179 | 4.2s |
| `hbundles/01_pipeline_hier.buda` | none | 32 | 32 | 4 | 0 | 0 | 0.0s |
| `hbundles/02_two_procs.buda` | none | 64 | 64 | 8 | 0 | 0 | 0.0s |
| `hbundles/03_priority_ordering.buda` | none | 20 | 20 | 3 | 0 | 0 | 0.0s |
| `hbundles/04_deep_hierarchy.buda` | none | 28 | 28 | 7 | 0 | 0 | 0.0s |
| `hbundles/05_stress_grid.buda` | none | 224 | 224 | 61 | 0 | 0 | 0.3s |
| `hbundles/06_multipin_stress.buda` | none | 116 | 116 | 35 | 4 | 48 | 0.5s |
| `hbundles/07_wide_fan_stress.buda` | none | 46 | 46 | 24 | 0 | 11 | 0.8s |
| `hbundles/08_cross_level.buda` | none | 60 | 56 | 14 | 0 | 0 | 0.0s |
| `hbundles/09_local_global_compete.buda` | none | 56 | 56 | 2 | 0 | 0 | 0.0s |
| `hbundles/10_chip_units_blocks_leaf.buda` | none | 968 | 968 | 176 | 0 | 0 | 1.0s |
| `rnr/mix.buda` | both | 1270 | 1270 | 100 | 0 | 0 | 15.4s |
| `rnr/mix2.buda` | both | 1270 | 1270 | 100 | 2 | 42 | 26.9s |
| `rnr/mix2_fast.buda` | none | 1270 | 1270 | 100 | 33 | 256 | 3.8s |
| `rnr/mix2_fast_bottomup.buda` | none | 1270 | 1270 | 100 | 17 | 196 | 3.2s |
| `rnr/mix2_fast_on_aligned_sql.buda` | none | 1270 | 1270 | 100 | 33 | 256 | 3.3s |
| `rnr/mix2_fast_topdown.buda` | none | 1270 | 1270 | 100 | 16 | 175 | 3.5s |
| `rnr/mix2_repro.buda` | both † | 1270 | 1270 | 100 | 2 | 42 | 27.4s |
| `rnr/slowdown_rnr.buda` | both | 1270 | 1270 | 100 | 0 | 0 | 27.7s |

> **†** `mix2_repro` is a `mix2` **debug repro**: its script `exit`s on line 13
> (after `run_nuts`), so under the shipped CLI it runs neither the healers nor
> `run_detailed_nuts` — the CLI endpoint is the `run_nuts` state with no
> DetailedNUTS opens. The `both` / `2`/`42` shown here come from the harness
> skipping that `exit` and running the whole file (which mirrors `mix2`); it is
> included for completeness but is **not** a shipped-CLI baseline — use `mix2`
> as the canonical row.

## Reading the corpus

- **34 flows.** 5 run a healer under the shipped CLI (`both`: `big2`,
  `bigHalf`, `mix`, `mix2`, `slowdown_rnr`); a 6th, `mix2_repro`, has the
  healer commands but `exit`s before them (`†` above — its healers/detailed-NUTS
  run only because the harness skips the `exit`). The remaining 28 are
  healer-less. No flow uses `nc`-only or `rr`-only. The `mix2_fast*` variants
  are the **healer-less** ablations of `mix2` (their healer lines are commented
  out), which is why they carry residual opens.
- **Size spans three orders of magnitude:** from `b3_bus_023` (1 bit, 1 bundle)
  to `hbundles/10_chip_units_blocks_leaf` (968 bits, 176 bundles) and the
  2840-bit / 80-bundle `big`/`tc3a`/`bigHalf` family and 1270-bit / 100-bundle
  `mix` family.
- **Clean at the endpoint (0/0):** 25 of 34. The non-clean flows are deliberate
  stress / ablation vehicles:
  - `tc3a` (870/9179) — a hopeless over-capacity design (the ripup convergence
    guard's target; healer-less here).
  - `mix2` (2/42) and its `mix2_fast*` ablations (256/196/256/175 opens) —
    congested `mix2` with healers on vs commented off. (`mix2_repro` shows the
    same 2/42 but only because the harness runs past its line-13 `exit`; `†`.)
  - `big2_noviz` / `tc3b_flat` (2/28) — healer-less congested flats.
  - `hbundles/06` (4/48), `/07` (0/11), `big_3bundles_sel_pure_mst` (0/12) —
    healer-less stress fixtures.
- **`nets == bits`** on every flow but `hbundles/08_cross_level` (60 nets, 56
  bits) — BUDA nets are bit-level, so the two coincide unless some nets are not
  bundled into a routed bundle.
- **Runtime** is dominated by the `both`-healer flows (`mix`-family 15–28s,
  `bigHalf` ~8s); every healer-less flow finishes in ≤4.2s.

See also: [`drop_dangling_modes_2026-07.md`](drop_dangling_modes_2026-07.md) and
[`healer_effectiveness_2026-07.md`](healer_effectiveness_2026-07.md) (same
corpus, different experiments); [`dedup_default_2026-07.md`](dedup_default_2026-07.md)
(the `kSegsRel`/`script_path` measurement caveat).
