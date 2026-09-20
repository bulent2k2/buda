# The judge — an audit that is not the router

Convergence-ladder build item 2 ([convergence_ladder.md](convergence_ladder.md),
"The judge must not be BUDA"), built 2026-09-20.

> If DetailedNUTS routes both arms and `check_design` scores them, a skeptic
> says the referee wears our jersey.

`tools/independent_audit.py` is the answer: it reads the persisted BDB tables
with `sqlite3` from the standard library and checks the route from geometry
alone.  It was specified to come BEFORE any A/B table was written, and did
not — E4, E2, E1 and E5 (6b, 6c and 6d with it) were all scored by
`check_design`.  This page is what it does, what it refuses to claim, and
what it found on its first application to those rounds.

## Running it

```bash
tools/independent_audit.py <design.bdb|design.bdb.sql>    # 0 clean, 1 dirty, 2 unjudgeable
tools/independent_audit.py ckpt.bdb --json res.json --quiet
```

A flow that opens `:memory:` leaves nothing to judge; give it a durable home
with the redirect `btcl -b` arms, which changes no flow text:

```bash
BUDA_BDB_MEMORY_TO=/tmp/run.bdb bin/buda flow/soc_small.buda --no-viz
tools/independent_audit.py /tmp/run.bdb
```

In the convergence driver, **`converge.tcl -judge`** does that per round and
puts the verdict in the table as its own column, beside the engine's:

```
| vehicle | size | heal | arm | round | ... | final ovl/unpl/viol | ... | judge |
| soc     | 2    | off  | td  | 0     | ... | 0/0/0               | ... | clean |
| soc     | 2    | off  | blind | 1   | ... | 1/8/8               | ... | 104   |
```

Two columns, never one.  A row where they disagree is the finding the judge
exists to make possible, so the driver reports what the judge said and never
substitutes its own reading: `clean`, a violation count, or `—` when the
judge declined to judge — which is not clean and must not read as it.

## What it checks

| kind | what it says |
|---|---|
| `OFF_GRID` | a bit-wire whose track position is not the centre of a SIGNAL slot of its layer's effective pattern |
| `SHORT` | two DIFFERENT nets whose metal overlaps on one layer |
| `KEEPOUT` | a bit-wire lying over a keepout that blocks its layer |
| `OPEN` | a net whose metal is not one connected piece, or that does not reach an endpoint block |
| | — two wires count as joined by a via only where the via LANDS ON BOTH: a `net_via` row *says* two segments are joined, and whether they are is geometry |
| `NO_METAL` | a net a bundle carries with no placed metal at all |
| `LAYER_DIR` | a bit-wire running across its layer's declared direction |

It imports **no engine** — not `buda`, not `buda_db`, not `src/`, not the
`tools/` modules that do.  Where a track lies, what a keepout blocks, what
"connected" means: all re-derived here from the stored numbers, so a defect
in the engine's arithmetic surfaces as a DISAGREEMENT instead of being
reproduced faithfully by both sides.  That claim is tested twice
(`test_independent_audit.py`), because each check alone has a hole: the
source is scanned for a forbidden import, and the tool is RUN in a
subprocess with `PYTHONPATH` emptied from a directory that is not the repo.
The second is the one that matters, and it has a trap this repository has
been caught by before — `conftest.py` pins the repo's own `build/` at
`sys.path[0]`, so an in-process check finds `buda` importable whatever the
tool does.

**A judge is worth what it catches**, so the tests are a mutation matrix:
each fault is planted in the tables in SQL, one at a time, and the judge must
name it.  Reverting any one of its **fourteen** rules fails at least one
named test: short, keepout, off-grid, layer-direction, metal in two pieces,
a net that does not reach its block, a net with no metal, a via that does not
land on the wires it claims to join, the abutment control (a T-junction
touches and does not overlap, and a judge that called that a short would
fail every correct route), the unjudgeable exit status, both halves of the
hierarchy rule below, and both halves of the membership fallback (falling
back at all, and saying so).

## What it deliberately does not claim

A clean verdict here is about GEOMETRY and nothing else: not timing, not
DRC at a real detailed router's rule deck, nothing the ladder page's
"What is deliberately not claimed" disclaims.  Four limitations are stated
rather than left to be discovered:

* **Region overrides** make the effective pattern a function of position,
  and a wire is judged at its MIDPOINT.  One whose span crosses an override
  boundary is counted and reported as a note, not a violation: whether the
  engine is entitled to place such a wire is a modelling question this file
  has not settled, and a judge must not manufacture violations out of one.
* **Reach is judged against the endpoint COMPONENT's bbox**, not the pin
  rectangle — that is the contract BUDA routes to, a bundle landing on a
  block face with the block's own routing taking it from there.
* **An unplaced component** (the `-1,-1,-1,-1` convention) is skipped for
  reach, counted, and named.
* **A design with no `bundle_net` rows** cannot say which nets were supposed
  to carry metal, so the scope falls back to the nets that HAVE metal —
  shorts and broken metal are still judged, `NO_METAL` is not — and the
  fallback is NAMED in the output.  A silent narrowing is how an audit comes
  to mean less than its reader thinks.

### The hierarchy rule, and how it was got wrong first

The first rule written here required every pin-bearing component to be
reached, and `flow/soc_small.buda` came back with **2048 OPENs** against a
route `check_design` calls clean.  The judge was wrong, not BUDA: a
hierarchy propagates a net's pin to every ancestor up to the common one, so
one net names a leaf AND the container holding it, while the bundle routes
to the container FACE.  Measured on `da_0_0_0`: the wire runs y 464..608 at
x 260, landing exactly on `quad_0/cl_0/core`'s top edge and
`quad_0/cl_0/l1d`'s bottom edge, while `core/regf` (296..448) and
`l1d/tag` (624..904) sit inside them, reached by those cells' own routing.

Reach is now required of the **outermost pin-bearing components** only,
which is exactly what the hierarchy licenses and no more: an inner
component is satisfied by an ancestor that CARRIES A PIN OF THIS NET, never
by a container that merely encloses it.  Both halves are pinned in both
directions on `flow/hier_testcase.buda`.

## What it needed from the engine

Nothing in the tree could be judged at first, and the reason was a real gap
rather than a missing convenience.  The routing grid and the keepouts got a
table in v29 so a resume routes against the same physical model the build
did — persisted by WRITE-THROUGH at each declaration, which reaches only the
declarations made while a BDB is open.  `source flow/tracks/tracks.buda`
before `open_bdb` is the order nearly every flow here is written in, so
those checkpoints carried a route and **zero `track_pattern` rows**: the
failure v29 exists to remove, reached from the other side.

The session now journals each grid declaration and `open_bdb` replays the
journal after the restore.  Precedence is unchanged — what the checkpoint
holds fills in what this session has not declared, what this session
declared wins and is what gets stored — and both orders now store the same
patterns, asserted row for row.

## First verdicts

Every design judged so far, with `check_design`'s verdict beside it:

| design | `check_design` | judge |
|---|---|---|
| two-instance reserve vehicle (24 wires) | Success | clean |
| `flow/hier_bundle1`, `hier_four_blocks`, `hier_four_blocks_cell`, `hier_testcase` | Success | clean |
| `flow/soc_small.buda` (9328 wires, 6 layers) | Success | clean |
| `converge tpu 8 -arms blind` (mesh, bottom-up) | 0/0/0 | clean |
| `converge soc 2 -arms td` (no copies) | 0/0/0 | clean |
| `converge soc 2 -arms blind` (bottom-up) | 1/8/8 | **104** |
| `converge soc 4 -arms blind` (bottom-up) | 5/117/117 | **201** |

The agreements are the point as much as the disagreements: a judge that
never agrees is measuring itself.

## The first finding: a bottom-up copy lands off the grid

On the SoC's bottom-up arm the judge reports **96 bit-wires on no signal
track at all**, at NQ = 2 and NQ = 4 alike, and no in-house audit mentions
them.  The engine's own verdict for that NQ = 2 round is `1/8/8` — one
overlap and the eight `pc_0` bits whose supply-doomed seat is already
documented ([flow/tcl/soc.md](../../flow/tcl/soc.md)); the judge finds those
eight too, as `OPEN`.  The 96 are new.

What is MEASURED, on
`converge.tcl soc 2 -arms blind -maxreserve 0 -informed 0 -judge`:

* All 96 belong to bundles 127, 128 and 129 — `bu_locked` **copies** of
  `core_cell`'s `regf→alu` template at `quad_0/cl_1/core`,
  `quad_1/cl_0/core` and `quad_1/cl_1/core`.
* The **reference** instance's bundle (126, `quad_0/cl_0/core`) is 32/32 on
  grid.  Each copy is **0/32**.
* The copy is a rigid translation by the instance offset: the reference's
  first bit sits at x = 336.5 and `quad_0/cl_1/core`'s at 1360.5, exactly
  1024 apart — the instances' Δx.
* M3 is vertical with an 18-unit period whose signal centres fall at
  3.5, 5, 6.5, 8, 12.5, 14, 15.5, 17.  1024 mod 18 = 16, so every copied
  bit lands off every slot; 1360.5 sits INSIDE the GROUND slot spanning
  1359..1361.
* The instances' x phases mod 18 are 4, 2, 14 and 12 — four different
  phases, one template.
* `align_bottom_up` had already said so in the same run: *"quad_0/cl_1/core
  (inside a marked parent) sits off cell 'core_cell's chosen phase — the
  parent template places it at an incompatible offset; not fixable by
  translation"*, and reverted its one attempted move.
* `check_template_tracks` nevertheless reports **`cell 'core_cell':
  ALIGNED — 4 instance(s) see identical signal tracks (ref
  quad_0/cl_0/core, 12 window(s) compared)`**, so DNUTS copied.
* The other layers are fine: the same cell's M5 and M6 copies are 32/32 on
  grid (M6 is horizontal and every instance shares y = 112; the M5 deltas
  are multiples of its period).

So the checker's verdict and the placed metal disagree, on a cell the
aligner had already flagged.  **The mechanism is not established** and is
deliberately not guessed at here: `rel_tracks` compares each instance's
pool relative to its own origin, which should see a phase shift, so
something upstream of the comparison — which windows reach it, or which
bundles are in the fixed set — is the place to look.  That is a separate
piece of work; what this page records is the measurement.

Two controls bound it, and both point the same way: the mesh
(`tpu 8`), whose `-bottomup` snaps the row pitch onto the stack's track
period, is clean; and the SoC's own TOP-DOWN round, which makes no copies
at all, is clean at the same size.  The fault is in the copy, not in the
design or the judge.

**What it means for the published tables**: every "clean" in E1 and E5 —
6b, 6c and 6d included — is a `check_design` clean, and the bottom-up arms
of those tables carry this off-grid metal unless the fix changes it.  The
tables are not withdrawn on that account: the claim they support is about
the LOOP (does an informed round converge, and in how many rounds), both
arms are scored the same way, and the 96 wires are present in every arm's
bottom-up rounds alike.  But a row that reads clean is clean BY THE
ENGINE'S AUDIT, and until the copy is fixed and the rows re-judged, that is
what those tables say.  The ladder page's build order put the judge first
for exactly this reason.
