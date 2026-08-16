# pdngen goldens — real power-grid DEF output

What a PDN generator actually writes, as opposed to what we assumed it
writes. `opens_interchange.md` item 15 records the gap these closed:
`read_specialnet` collected points only while the next token was `(`, and
DEF puts `+ SHAPE` between the width and the first point — so every stripe
and every rail a generator draws was read as nothing, and the only
power-routed DEFs in the tree were the two we had typed by hand in the one
form that parsed.

## The two fixtures, and why there are two

**`pdngen_excerpt.def` — checked in, always runs.** Four wiring clauses
lifted verbatim from the goldens below, one per form the reader has to get
right:

| clause | from | what it pins |
|---|---|---|
| `+ SHAPE FOLLOWPIN` 2-point | `macros.defok` | a standard-cell rail |
| `+ SHAPE STRIPE` 2-point | `macros.defok` | a die-crossing stripe |
| `+ SHAPE RING` 2-point | `existing.defok` | a ring segment |
| `+ SHAPE STRIPE` 1-point + via name | `macros.defok` | a via PLACEMENT, which is not a wire |

The coordinates, widths, layers and via name are upstream's bytes. The only
edit is the clause keyword: pdngen writes one `+ ROUTED` per net followed by
`NEW` continuations, so clauses lifted out of the middle of a net are spelled
`NEW` to sit correctly in one statement. The surrounding DEF header is ours
(a minimal legal wrapper).

It is checked in because a fixture that needs the network is a fixture CI
does not run, and "the reader reads generator output" is exactly the claim
that must not go unchecked. Four clauses is small enough to quote under
BSD-3 with attribution; the full files are not.

**`*.defok` — fetched, opt-in.** The whole goldens, for the counts:

```bash
python3 test/tests/data/pdn_goldens/fetch.py          # ~1 MB, 4 files
python3 test/tests/data/pdn_goldens/fetch.py --check  # verify, fetch nothing
```

`test_def_specialnets.py` skips its golden-backed tests when they are absent,
naming this command. They are digest-pinned: if upstream regenerates a
golden, the fetch fails loudly rather than silently handing the suite a
different fixture — which is the failure mode `flow/ariane133/fetch.py`
exists to prevent, and the same reasoning applies here.

## What they measure

Totals across the four, as read by `read_def` after the item-15 fix:

| | paths | note |
|---|---:|---|
| metal polylines | **685** | read as **0** before the fix — all four files, every one defeated by `+ SHAPE` |
| via placements | **6781** | `NEW <layer> 0 ( x y ) <viaName>` — no run, so no polyline to lose; censused `SPECIALNETS.via_placement` |
| mid-path vias | 0 | legal DEF, not emitted here |
| `RECT` / `POLYGON` | 0 | legal DEF, not emitted here |

The last two rows are why the reader still censuses those forms rather than
handling them: nothing available emits them, so an implementation would be
built against no evidence. That is the mistake this directory exists to stop
repeating, not one to make in a new direction.

## Provenance

The OpenROAD Project, `src/pdn/test/*.defok`, BSD 3-Clause. Fetched from
`raw.githubusercontent.com`, never vendored. Upstream:
<https://github.com/The-OpenROAD-Project/OpenROAD>

To generate a power-routed DEF of our own — which is what item 15's *keepout
impact* still needs, and what these small designs cannot supply — see
[openroad_pdn_recipe.md](../../../../docs/internal/openroad_pdn_recipe.md).
