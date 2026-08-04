# Track-density doubling: the `_2x` clone vehicles (2026-08-04)

Status: **LANDED**, as *added* vehicles. No existing fixture or flow changed.

The chip stack was doubled in place (PR #585) because its vehicles are QoR
targets. The rest of the corpus is not: `rnr/*`, `big_data_test/*` and
`hbundles/*` are **regression vehicles for the healers and the planner's
congestion machinery**, and their congestion is the coverage. So here the
doubling is offered as a *twin*, not a replacement.

## The rule

For each corpus flow:

| the flow is… | what happens |
|---|---|
| already totally healed (0/0/0) | **nothing** — no clone, original untouched |
| improved by doubling | a `*_2x` **clone** is added; the original stays as is |
| not improved (neutral or worse) | **nothing** |

Measured across all 38 corpus flows, that yields exactly **two** clones. 30
flows are already 0/0/0 and 6 are the chip vehicles (already on the doubled
`chip_tracks.buda` from #585, so this transform never touched them).

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

The first keeps every placement grid valid. The second means the `def_layer`
overheads carry over untouched — the clone declares the same numbers as its
original. The third is not cosmetic: `tracks_in_range` walks a period at a time
with `pos += width + space_after`, so accumulated rounding decides whether a
track sitting exactly *on* a window edge falls inside it. A fixture built from
non-representable values breaks template alignment with an off-by-one at window
edges — measured, and documented in `flow/chip/ReadMe.md`.

Rails are untouched, including their `space_after`, so a fixture's own structure
survives: unequal rail widths, an odd signal count per group, an already
non-standard count. This is deliberately **not** the symmetric rewrite the chip
stack uses — symmetry only buys anything for a *mirrored* placement.

**The tool is not idempotent, and the fixtures contain a symlink**
(`flow/big_data_test/big2/tracks4top.buda -> ../../tracks/tracks4top.buda`).
Listing both paths doubles the target twice; the tool dedupes by real path and
says so. Caught in practice, hence the guard.

Non-idempotence also means **"has this been doubled?" is not a question the file
can answer** — 16 signals per period could be natively 16 or a doubled 8, and
nothing records which. An early `--check` mode claimed to verify exactly that
and, because re-doubling always produces a different file, returned 1 for every
non-empty fixture including ones it had just transformed (Codex, PR #586). It is
now `--audit`, which checks the property that *is* well-defined and worth
guarding: every width, spacing and origin **binary-exact**, every slot list
well-formed, with a per-layer period / signal-count / density report.

## What landed

```
flow/rnr/mix_tracks_2x.buda                    16 signals/period (was 8)
flow/rnr/mix2_fast_bottomup_caps_2x.buda       clone, one line changed
flow/rnr/mix2_fast_on_aligned_sql_2x.buda      clone, one line changed
```

Each clone is byte-identical to its original apart from `source
mix_tracks.buda` → `source mix_tracks_2x.buda`, and a header explaining the
pair. Both are in the QoR corpus **alongside** their originals — the pair *is*
the measurement.

| flow | original | `_2x` clone |
|---|---|---|
| `mix2_fast_bottomup_caps` | 2/0/0, WL 68371, 42.3s | **0/0/0**, WL 62778 (−8.2%), 18.8s |
| `mix2_fast_on_aligned_sql` | 2/16/1, WL 76368, 46.0s | **0/0/0**, WL 65160 (−14.7%), 4.7s |

## Why the originals are kept — this is the whole point

Both originals carry their residual **on purpose**:

* **`mix2_fast_on_aligned_sql`** is the corpus's *last live supply-doomed seat*
  outside chip3a. Its 2/16/1 is the exemplar that
  `tools/doomed_seat_forensics.py` and the `check_design` doomed-seat census are
  read against, and its own header already refuses an available
  `set_max_bundle_bits` fix for precisely that reason. Doubling its tracks in
  place would have erased the exemplar just as surely as applying that fix.
* **`mix2_fast_bottomup_caps`** keeps 2 residual overlaps that are live exercise
  for the bottom-up healer path under per-cell layer caps. A clean flow
  exercises nothing.

More generally: an in-place doubling of the shared fixtures **disarms
congestion-dependent tests**. Measured on the first attempt at this change —
fifteen tests failed, uniformly because their mechanism stopped engaging:

* ripup had no overlaps to reduce (`assert base > 0` → `assert 0 > 0`);
* negotiate had no opens to clear;
* the width gate found nothing statically infeasible, so it never fired;
* the refinement pass had no phantom detour to straighten (WL 418 → 330 became
  330 → 330);
* `nuts_corner_overlap`, `planner3`'s double-booked trunk, `ripup2`'s actual
  blocker — none of those situations arose.

That is the *router* improving and the *coverage* degrading. Precedent:
`docs/internal/mid_tier_failures_2026-07-29.md` records `06_multipin_stress`
self-healing until it could no longer enter stage b with opens, which is why a
healer-free `_raw` twin exists. **When a vehicle stops exhibiting the problem,
you need a vehicle that still does — the clone scheme keeps both.**

The clone scheme also means **no goldens move and no test changes are needed**:
`nuts_golden`, `topo_golden` and `flow_qor_golden.json` are all untouched,
because every existing flow routes exactly as before.

## Adding another clone later

1. Measure the flow both ways (`tools/qor_corpus.py --flows`). If the original
   is already 0/0/0, stop — there is nothing to demonstrate.
2. `cp` the tracks fixture to `*_2x.buda`, run
   `tools/double_track_density.py` on the copy, add the explaining header.
3. `cp` the flow, change only its `source` line, add a header stating what the
   original's residual is *for*.
4. Add **both** to `tools/qor_corpus.py`.
5. `test_double_track_density.py` will then require the twin to be the exact
   doubling of its original and the clone to differ by exactly one line.

## Not done

* `demo/tracks_ariane136.buda` declares overheads as *fractions* (`0.25`,
  `0.33`) rather than percentages, and they have never matched the actual
  density. Untouched and pre-existing, but `report_overhead` disagrees with that
  fixture either way.
* An in-place doubling of the non-chip corpus measured **2 better / 1 worse / 35
  unchanged**, WL −0.42%/−0.61%, runtime −18.7% — with the large runtime wins on
  flows that were *already clean* (`bigHalf` 63.1s → 2.0s, `mix2` 29.0s → 1.9s),
  i.e. exactly the flows this scheme leaves alone. That speed is available if
  the coverage cost is ever judged acceptable; it is recorded here rather than
  taken.
