# Layout units — the coordinate contract

*Phase 1 of [the LEF/DEF interface plan](lefdef_interface_plan.md).  This is
the one place that says what BUDA's numbers mean.  If you are adding a
distance — a coordinate, a width, a pitch, a margin — read §2 and §3.*

---

## 1. The contract, in one sentence

**Every distance in BUDA is in *layout units*, and a layout unit is whatever
the design says it is.**

The routing engine is deliberately **unit-agnostic**.  `Point` and `Rect` are
plain `int` (`src/topology.h`), and nothing in `topology.cpp`, `nuts.cpp`,
`congestion_planner.cpp` or `routing_grid.cpp` claims those integers are
microns — grep returns zero hits.  Every decision the engine makes is a
comparison or a ratio of two lengths, so it is invariant under any
**consistent** choice of unit.

That is a feature, not an accident: it is what makes a DBU-exact import cheap
(§4).  It also has a sharp edge, which §3 is about.

## 2. Where units enter and leave

Only the boundaries carry a physical interpretation.  Everything between
them just compares numbers.

| Boundary | Direction | Conversion |
|---|---|---|
| `import_def_lef` — DEF `COMPONENTS`, `DIEAREA` | in | DEF integers are DBU; `dbu × scale / UNITS` (`src/bdb.cpp`) |
| `import_def_lef` — LEF `SIZE` / `RECT` | in | LEF is already µm; `µm × scale` |
| `import_gds` / `export_gds` | in / out | GDS DBU ↔ µm via the file's `UNITS` record, then × / ÷ scale; export writes 1 nm DBU (`kDbuUm`, `src/gds_io.cpp`) |
| BDB tables | store | `REAL` columns, in layout units |
| BDB → `Floorplan` | in | `int(round(...))` — the quantization point (`src/buda_session/hier.py`), ~59 sites across the Python layer |
| `.buda` script | in | **every declared distance is already in layout units — no conversion at all** |

So a run has exactly **one** scale decision, and it is made at import:

```
set_import_scale micron    # default — 1 layout unit = 1 µm (bit-identical)
set_import_scale dbu       # 1 layout unit = 1 DEF database unit — EXACT
set_import_scale 2000      # or an explicit factor
```

After that the layout unit is fixed and everything else must agree with it.

**Why `dbu` is worth having.** At the default scale the engine's integer grid
quantizes to 1 µm — ~2000 DBU on an advanced node, roughly 20–25 track
pitches thrown away per coordinate.  That is what made real-PDK data
unusable, and it was never an algorithm limitation: the algorithms do not know
what a micron is.  In DBU mode the stored value *is* the integer the DEF wrote,
and the ~59 `int(round(...))` conversions become exact.  A 10 mm die at
2000 DBU/µm is 2×10⁷ layout units, comfortably inside int32.

**Why the mode resolves to a number.**  `dbu` is resolved against the DEF
being imported (its own `UNITS DISTANCE MICRONS`), so the script never has to
know the technology's DBU count and cannot mis-state it.  What gets persisted
is the resulting *number*, not the mode — persisting the mode would re-resolve
it against whatever DEF is imported next, silently restating the stored
coordinates in a different unit.

## 3. The sharp edge, and the guard

Because nothing is typed, nothing stops a design from mixing scales — block
coordinates imported at one scale and a track pattern hand-declared at
another.  The result is **not** a crash and **not** an obviously wrong
picture.  It is a plan in which every bus reserves a wrong fraction of the
space it needs, reported as feasible.  At 2000 DBU/µm a bus would reserve
~1/2000 of its real width and the design would look gloriously routable.

Two things guard against that.

**(a) Prefer grid-derived geometry over literals.**  `LayerStack::eff_bus_width`
returns `bits * layer.bit_pitch` whenever the layer has a track pattern, and
falls back to `base_width * dilution` only when it does not
(`src/layering.cpp:67`).  A grid-derived quantity is in the grid's units by
construction, so it cannot disagree with the grid.  The literals that remain
are fallbacks for designs with no pattern:

- bus width `len(bits) * 1.5` — "1.5 layout units per bit"
  (`src/buda_cmds/bundling_cmds.py`);
- the NUTS inter-bus track pitch default `1.0` (`src/buda_session/nutsflow.py`);
- every script-declared distance: `corner_margin`, `set_min_stub_length*`,
  `detour_channel`, `def_layer` `span_min`/`span_max`, `set_track_pitch`,
  `def_track_pattern` widths and spacings, `add_block`/`add_keepout`
  coordinates.

**A script author owns the consistency of that list.**  If you scale the
import, scale these too.

**(b) The unit-plausibility guard** (`set_unit_check`, on by default) catches
it when you do not.  The signal is **tracks across the design**:

```
tracks_across = design_extent / layer_track_pitch
```

a ratio of two layout-unit lengths — invariant under any *consistent* unit,
and off by the scale ratio under an inconsistent one.  It fires outside
`[0.5, 1e7]`:

| bound | why there |
|---|---|
| **min 0.5** | Half a track pitch: the design is smaller than one wire of a layer it claims to route on.  The smallest design in the tree is **3.66** (`flow/four_blocks`, M7 — a 150-unit toy on a 41-unit pitch), 7× above. |
| **max 1e7** | The widest reticle die (~33 mm) at the finest production metal pitch (~28 nm) is ~1.2e6 tracks across — the physical ceiling.  The bound is ~8× past it, and ~1.3e4× past the tree's maximum of **797.2** (`flow/chip/chip_topdown`, M2–M5). |

The first calibration set the minimum at 4, from the QoR corpus alone
(minimum 24.4).  That broke six legitimate unit-test fixtures, which are
*much* smaller than any corpus vehicle — a reminder that a bound is only as
good as the population it was measured over, and that "the corpus" is not the
same population as "everything the repo runs".

Both bounds are far outside both the measured and the physical range on
purpose: a guard that stops a legitimate run is worse than one that misses a
subtle case.

**What the ratio signal cannot do.**  A ~2000× mismatch multiplies it by
2000× — but a mis-scaled *small* design and a legitimate *huge* one produce
the same number, and the check has no way to tell them apart.  On the Phase-1
regression fixture the mismatch reads 720 000 tracks across: nonsense, and
comfortably inside the bounds.  No pure ratio can do better; without an
absolute anchor there is nothing to compare against.

**The anchor, when there is one.**  A design that *declared* an import scale
has asserted that its layout units are physical, so `unit_pitch / lu_per_um`
is a track pitch in real microns — and real metal pitches are bounded, from
~20 nm at the finest production node to ~10–20 µm for top metal and
redistribution.  A declared-scale design therefore gets a second, far sharper
check: the pitch must land in **0.005 … 500 µm** (~10× outside the real range
at both ends).  The fixture above fails it at 0.0005 µm — half a nanometre.

At the default scale of 1.0 that check does not apply, and deliberately so:
"microns" there is nominal.  Every corpus design would fail a physical pitch
test, and rightly — nobody claimed those numbers were physical.  *Declaring*
a scale is what turns the claim on.  Same principle as "no track pattern, no
verdict": the guard judges only what the design actually asserted.

The check runs **once per session**, at the first stage that has both a
floorplan and a routing grid — `run_planner` for most flows, `run_nuts` for
the ones that declare their patterns after planning
(`demo/comprehensive_demo.buda` does).  A design with no track pattern is
never judged: there is no second scale to disagree with, and inventing a
verdict from the coordinates alone would need exactly the calibration this
signal exists to avoid.

```
set_unit_check on      # default — stop the run, with the numbers
set_unit_check warn    # report and continue
set_unit_check off     # disable
```

Calibrated 2026-08 over **124 flows / 580 layer-rows** — every `.buda` in
`flow/`, `demo/` and `test/` that reaches a planner with a grid — spanning
`flow/four_blocks` (extent 150) to `flow/chip/chip_topdown` (extent 14350).
Nothing in that population is within 7× of either bound.  Measurement and
predicate are the
same code the verdict uses — `unit_consistency_signals` /
`unit_plausibility_faults` in `src/buda_session/util.py` — so the number a
user is shown is the number that was judged; tests in
`test/tests/test_unit_check.py`.

## 4. Why this is the cheap fix

The alternative — a typed unit boundary (`struct Dbu` / `struct LayoutUnit`)
— is the rigorous version, and it is large: every geometry signature changes.
It is worth costing separately if this class of bug recurs.  Until then, the
combination of *one* scale decision (§2), grid-derived geometry (§3a), and a
ratio-based guard (§3b) buys most of the safety for a fraction of the churn.

What Phase 1 did **not** do: the `1.5`-layout-units-per-bit width fallback
still exists for designs with no track pattern, and a scaled import with a
hand-written track pattern still needs the pattern scaled by hand.  §3a is
what keeps the first from mattering on any design that *has* a pattern; §3b
is what stops the second from passing silently.  Both are honest limits, not
oversights — see [the plan](lefdef_interface_plan.md) §2.
