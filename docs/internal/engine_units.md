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
| `import_def_lef` — DEF `COMPONENTS`, `DIEAREA` | in | DEF integers are DBU; divided by `UNITS DISTANCE MICRONS` (`src/bdb.cpp`), giving µm |
| `import_def_lef` — LEF `SIZE` / `RECT` | in | LEF is already µm; taken as-is |
| `import_gds` / `export_gds` | in / out | GDS DBU ↔ µm via the file's `UNITS` record; export writes 1 nm DBU (`kDbuUm`, `src/gds_io.cpp`) |
| BDB tables | store | `REAL` columns, in layout units |
| BDB → `Floorplan` | in | `int(round(...))` — the quantization point (`src/buda_session/hier.py`), ~59 sites across the Python layer |
| `.buda` script | in | **every declared distance is already in layout units — no conversion at all** |

So a run has exactly **one** scale decision, and it is made at import.  After
that the layout unit is fixed and everything else must agree with it.

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
`[4, 1e7]`:

| bound | why there |
|---|---|
| **min 4** | Under four tracks across the whole design there is nothing to route — no bus plus neighbours fits.  The corpus minimum is **24.4** (`flow/hbundles/05_stress_grid`, M7), 6× above. |
| **max 1e7** | The widest reticle die (~33 mm) at the finest production metal pitch (~28 nm) is ~1.2e6 tracks across — the physical ceiling.  The bound is ~8× past it, and ~1.3e4× past the corpus maximum of **797.2** (`flow/chip/chip_topdown`, M2–M5). |

Both bounds are far outside both the measured and the physical range on
purpose: a guard that stops a legitimate run is worse than one that misses a
subtle case.  A ~2000× DBU/µm mismatch moves the signal by 2000×, which
clears them by orders of magnitude.

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

Calibrated 2026-08 over 12 flows spanning `demo/quickstart` (extent 1000) to
`flow/chip/chip_topdown` (extent 14350); measurement path
`BUDA_UNIT_SIGNAL=1`, predicate and bounds in `src/buda_session/util.py`,
tests in `test/tests/test_unit_check.py`.

## 4. Why this is the cheap fix

The alternative — a typed unit boundary (`struct Dbu` / `struct LayoutUnit`)
— is the rigorous version, and it is large: every geometry signature changes.
It is worth costing separately if this class of bug recurs.  Until then, the
combination of *one* scale decision (§2), grid-derived geometry (§3a), and a
ratio-based guard (§3b) buys most of the safety for a fraction of the churn.

The remaining Phase 1 work — an explicit import scale factor so a real-PDK
DEF can be read at 1 layout unit = 1 DBU with no quantization at all — is in
[the plan](lefdef_interface_plan.md) §2.
