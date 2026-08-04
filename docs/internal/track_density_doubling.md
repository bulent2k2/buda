# Doubling the corpus track density (2026-08-04)

Status: **LANDED**. Every shared track fixture outside `flow/chip/` now carries
**twice** the signal tracks per period. The chip stack got the same treatment
separately (PR #585); this is the port to the rest of the corpus.

## The transform

`tools/double_track_density.py`. Each `SIGNAL w s` slot becomes **two**
`SIGNAL w/2 s/2` slots. The pair occupies `2*(w/2 + s/2) == w + s` — exactly the
footprint the single slot had — which makes three properties true *by
construction* rather than by measurement:

| | why |
|---|---|
| **period** unchanged on every layer | the pair's footprint equals the slot's |
| **signal metal** unchanged | `2 * w/2 == w` |
| **binary-exactness** preserved | halving a binary-exact value is binary-exact |

The first keeps every placement grid valid (`align_bottom_up`, the chip
vehicles' 504 = LCM(...), every checked-in floorplan). The second means the
`def_layer` overheads are untouched — no declaration edits anywhere. The third
is not cosmetic: `tracks_in_range` walks a period at a time with
`pos += width + space_after`, so accumulated rounding decides whether a track
sitting exactly *on* a window edge falls inside it. A fixture built from
non-representable values breaks template alignment with an off-by-one at window
edges — measured and documented in `flow/chip/ReadMe.md`.

Rails are untouched, including their `space_after`, so each fixture's own
structure survives: unequal rail widths (`tracks.buda` L7 is POWER 6 / GROUND
2), an odd signal count per group (that same L7 has 3 per group), an already
non-standard count (`tracks2topM4M5` L4 had 10). This is deliberately **not**
the symmetric rewrite the chip stack uses — symmetry only buys anything for a
*mirrored* placement, and none of these fixtures has one.

**The tool is not idempotent, and the fixtures contain a symlink**
(`flow/big_data_test/big2/tracks4top.buda -> ../../tracks/tracks4top.buda`).
Listing both paths doubles the target twice; the tool dedupes by real path and
says so. Caught in practice, hence the guard.

Non-idempotence also means **"has this been doubled?" is not a question the
file can answer** — 16 signals per period could be natively 16 or a doubled 8,
and nothing records which. An early `--check` mode claimed to verify exactly
that and, because re-doubling always produces a different file, returned 1 for
every non-empty fixture including ones it had just transformed (Codex, PR #586).
It is now `--audit`, which checks the property that *is* well-defined and is the
one worth guarding: every width, spacing and origin **binary-exact**, every slot
list well-formed, with a per-layer period / signal-count / density report.
`test_double_track_density.py` pins it, including that `--audit` passes on both
densities and refuses the 0.8 / 0.4 scaling that broke the chip stack.

## Measured

Full corpus (38 flows), `tools/qor_corpus.py`:

```
2 better, 1 worse, 35 unchanged.  Metric = overlaps/unplaced/viol_bundles.
  abstract WL   16,997,915 -> 16,927,227   (-0.42%)
  detailed WL  342,392,727 -> 340,318,617  (-0.61%)
  runtime            1132.5s ->     920.3s (-18.7%)
```

| flow | before | after |
|---|---|---|
| `rnr/mix2_fast_on_aligned_sql` | 2/16/1 in 35.6s | **0/0/0 in 3.2s** |
| `rnr/mix2_fast_bottomup_caps` | 2/0/0 | **0/0/0** |
| `rnr/mix2_fast_bottomup` | 0/0/0 in 5.1s | **1/0/0** in 3.3s |

Most flows are *unchanged on the metric* because they were already clean — the
win shows up as wirelength and, dramatically, as runtime: `bigHalf` holds 0/0/0
but goes **63.1s → 2.0s**, `mix2` 29.0s → 1.9s. Those flows were spending
almost all their time healing congestion the density removes.

**The one regression is a healer trade, not a break.** `mix2_fast_bottomup`
reaches DNUTS with 16 opens, and `negotiate` clears all 16 at the cost of one
overlap — correct on its lexicographic `(opens, overlaps)` metric — after which
`ripup` stops because opens are already 0. The endpoint is electrically clean:
0 unplaced, 0 violating bundles, one abstract track overlap.

## Goldens

`nuts_golden` moved; `topo_golden` did **not** — topology *generation* is
geometric and independent of the track patterns, so this is a good sanity
signal that the change moves placement only. Regenerated with
`PYTHONPATH=build:tools python3 tools/nuts_snapshot.py`.

The counts move in the direction of quality: `rnr_mix` 248 → 220 abstract
segments and 1862 → **1472** vias (−21%), matching its −9.9% WL, with
`unplaced=0` before and after. The finer pitch lets the planner pick simpler
topologies. `test/tests/data/flow_qor_golden.json` was rebaselined the same way
(`BUDA_FLOW_QOR_REGEN=1`).

## The `*_8track.buda` fixtures — read this before "modernizing" them

The doubling removes so much congestion that **behaviours the test suite exists
to observe stop occurring at all**. 14 mid-tier tests failed on the first run,
uniformly because their mechanism no longer engages:

* ripup has no overlaps to reduce (`assert base > 0` → `0 > 0`);
* negotiate has no opens to clear;
* the width gate finds nothing statically infeasible, so it never fires;
* the refinement pass has no phantom detour to straighten (WL 418 → 330 becomes
  330 → 330);
* `nuts_corner_overlap`, `planner3`'s double-booked trunk, `ripup2`'s actual
  blocker — none of those situations arises.

This is the *router* getting better and the *coverage* getting worse. So four
fixtures preserve the historical density:

```
flow/tracks/tracks_8track.buda
flow/tracks/tracks2top_8track.buda
flow/tracks/tracks4top_8track.buda
flow/rnr/mix_tracks_8track.buda
```

Two consumers, split by what the flow is *for*:

1. **Congestion demo flows source them directly** — `planner3`, `planner5_span_drop`,
   `ripup1`, `ripup2`, `nuts_corner_overlap`, `nuts_group_pull`. These exist to
   exhibit a mechanism under congestion; they are executable documentation, not
   designs anyone wants routed well. None is in the QoR corpus.
2. **Tests build a sparse copy at runtime** when the vehicle *is* a corpus flow
   and should keep the improvement — `test_planner_refine`, `test_ripup_reroute`,
   `test_ripup_width_gate`, `test_nuts_pull_repack`, `test_planner_signal_tracks`.
   Each swaps the `source …tracks….buda` line into a `tmp_path` copy and asserts
   the line it expected was actually there, so a moved `source` fails loudly
   instead of silently testing the wrong thing.

There is precedent for this shape: `docs/internal/mid_tier_failures_2026-07-29.md`
records `06_multipin_stress` self-healing until it could no longer enter stage b
with opens, which is why a healer-free `_raw` twin exists. Same lesson, new
cause — **when a vehicle stops exhibiting the problem, the test needs a vehicle
that still does, not a weaker assertion.**

## Not done

* `demo/tracks_ariane136.buda` declares overheads as *fractions* (`0.25`, `0.33`)
  rather than percentages, and they have never matched the actual density. The
  transform preserves density exactly, so this is untouched and pre-existing —
  but it means `report_overhead` disagrees with that fixture either way.
* The `set_max_bundle_bits auto` lever that helped the chip vehicles is still
  chip-only; a corpus-wide evaluation is its own change.
