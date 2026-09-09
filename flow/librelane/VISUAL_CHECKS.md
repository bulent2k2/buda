# Looking at the LibreLane study

Every number this study reports is measured, and for a long time none of it
was ever *seen*.  LibreLane renders exactly one image, of the final layout;
the eighteen intermediate DEFs a top run writes sit on disk and nothing
draws them; and BUDA's own contribution — the bundles, the corridors, the
pin templates — reaches LibreLane as files, with no picture at any point.
A wrong floorplan is then invisible until a metric twelve steps later is
wrong, and by then the picture that would have shown it is buried.

This is the recipe sheet for the three things worth looking at.  Nothing
here re-runs a flow or a tool: every command reads artefacts that are
already on disk.

| I want to see | Section |
|---|---|
| where the die, the macros and the cells ended up, per LibreLane stage | [1](#1-librelanes-own-stages) |
| BUDA's bundles, buses and bit-wires — the plan itself | [2](#2-budas-own-view-bundles-buses-bit-wires) |
| the pins BUDA emits, and the pins the block actually got | [3](#3-the-pins) |

---

## 1. LibreLane's own stages

[`snapshots.py`](snapshots.py) renders any stage of a finished run from the
DEF that stage wrote, through KLayout's own `render.py` — the script the
flow's `KLayout.Render` step uses — with the tech file, layer properties,
layer map and cell LEFs all taken from the run's `resolved.json`.  So a
stage renders in exactly the colours the final render uses.

**One run:**

```bash
cd ~/src/buda/flow/librelane
python3 snapshots.py tier1a/n8/h/top/runs/h --stages floorplan,detailedplacement
```

The argument is the directory holding `resolved.json` — `.../runs/<tag>`,
not the design directory above it.  Output goes to `<run>/../snapshots/`,
one `NN-stage.png` per stage plus an `index.md`; `--out DIR` redirects it.

**The stages, and which runs have them.**  These names are stable across
all 48 runs on disk:

| stage | step | in |
|---|---|---|
| `floorplan` | 13 | every run — the die and the rows |
| `manualmacroplacement` | 17 | **top runs only** — where the macros landed |
| `generatepdn` | 21 | every run — the grid, and whether it reaches the macros |
| `globalplacement` | 28 | every run — std-cell spread |
| `detailedplacement` | 34 | every run — the legalised full placement |
| `cts` | 35 | every run — the clock tree |
| `globalrouting` / `detailedrouting` | 39 / 44 | every run |
| `applydeftemplate` | 54 | **H+B block runs** — BUDA's pins going in (§3) |
| `ioplacement` | 25 | **arm-H block runs** — LibreLane's own pin placer (§3) |

`--list` prints what a given run actually has, with the curated default set
marked.  A block run has no `manualmacroplacement`; passing a stage a run
lacks is harmless, it renders the ones it has.  With no `--stages` you get
the curated eight.

**Every run on disk, in one pass:**

```bash
cd ~/src/buda/flow/librelane
find . -name resolved.json | sed 's|^\./||; s|/resolved.json||' | sort | while read -r r; do
    python3 snapshots.py "$r" \
        --stages floorplan,manualmacroplacement,detailedplacement \
        --out "renders/$(echo "$r" | tr '/' '_')"
done
python3 contact_sheet.py                 # -> renders/index.html
```

Drop `--stages` for the curated eight, which is what the sweep above uses:
measured on the current tree, 48 runs and **356 images** in 33 MB, about
90 s a run (most of it Docker start-up).
[`contact_sheet.py`](contact_sheet.py) then writes one page over all of
them, grouped by run and laid out left to right in flow order, so the same
stage can be compared across N, across arms and across the variants of one
arm — which is the question a directory full of PNGs cannot answer.
`renders/` is git-ignored; the page's `img` paths are relative, so it opens
straight from the filesystem.  `--embed` inlines a downscaled copy of every
image instead, giving ONE shareable file (8.9 MB for the set).

**Every tile says how much of its stage is placed**, and that is the number
to read before concluding a render is broken.  `floorplan` is a grey
rectangle in every run and correctly so: measured on the N=8 top, the DEF
at that stage carries its die, its 896 standard-cell rows and **296
components of which 0 have a location** — LibreLane places the macros at
step 17 and the cells at 28/34.  Re-rendered at six times the resolution it
is the same picture with the row hatch resolved.  The first stage with
anything in it is `manualmacroplacement` for a top (104 of 296) and
`globalplacement` for a block.

**A BDB as well as a picture**, for the placement stages:

```bash
cd ~/src/buda/flow/librelane
python3 snapshots.py tier1a/n8/h/top/runs/hb --stages manualmacroplacement --bdb
```

That one opens in `bin/fp`, where a macro can be dragged and the HPWL and
flylines move with it, and in `bin/viz`.  It is written for placement
stages only: `import_def_lef` does not read routed geometry, so a BDB of a
routed stage would look like an unrouted design and quietly mislead.  For a
routing stage the DEF is the artefact — `bin/viz <run>/44-*/tpu_top.def`.

---

## 2. BUDA's own view: bundles, buses, bit-wires

The renders above are LibreLane's result.  BUDA's *plan* — which nets
bundled together, which topology each bundle took, where every bit-wire
sits — is in the `.buda` flows the arm generates, and it has its own
viewer.

**The flows, and what each one is the plan for:**

| flow | written by | the plan for |
|---|---|---|
| `tier1a/size.buda` | checked in | each leaf cell's SIZE (arm H+B's first contribution) |
| `tier1a/n<N>/pins.buda` | `pins.sh N` | each leaf cell's PINS — one `FP_DEF_TEMPLATE` per cell type |
| `tier1a/n<N>/h/top/guides.buda` | `guides.sh N` | the top's bus CORRIDORS, against the placement LibreLane used |
| `phase0/two_reg32/buda_route.buda` | checked in | the phase-0 toy's route |

**Run one with a viewer and a prompt:**

```bash
cd ~/src/buda
bin/btcl -v -b flow/librelane/tier1a/n2/pins.buda
```

`-b` runs the flow verbatim, keeps its design in an auto-named checkpoint
(`n2/pins.ckpt.bdb`) and drops you at the pin/edit prompt; `-v` opens the
main viewer on the finished design when the session ends.  At the prompt:

| verb | shows |
|---|---|
| `visualize` | the main window — blocks, buses, NUTS tracks, bit-wires, pre-routes |
| `topos` / `topos <bus>` | every candidate topology per bundle, with WL and segment counts |
| `explore <bus>` | the topology explorer for one bundle: step candidates with `a`/`d` |
| `pins` | the pin inventory — which bundle is pinned to which candidate |
| `dump_hbundles` | the bundle list: nets, level, cell context |
| `report_wirelength` | abstract and detailed WL, per layer |
| `check_design dnuts` | the audit at the bit level |
| `pin <bus> <N>` then `replan` | choose a different topology and re-route |
| `done` | save and exit |

Anything the prompt does not recognise goes to the engine verbatim, so
every command in [BUDA_SCRIPT_REFERENCE.md](../../docs/BUDA_SCRIPT_REFERENCE.md)
is available there.

**Two things to know before running one on a design you have already
hardened.**  Re-running a flow re-emits its outputs — `pins.buda` rewrites
`n<N>/pins/<cell>.def`, `guides.buda` rewrites `top/out/buda_bus.guide`.
That is safe because it is deterministic (measured: both re-emit
byte-identically), but it does touch the files a completed run was built
from.  And `guides.buda` reads `top/out/{placed.def,tech.tlef,blocks.lef}`,
which `guides.sh N` stages out of the run — if those are gone, run
`guides.sh N` again rather than the flow directly.

**Skip the rebuild on a second look.**  Every `-b` session leaves a resume
trace beside its checkpoint, so a later session restores the plan instead
of recomputing it, and prints the exact command to do so when it exits:

```bash
cd ~/src/buda
bin/btcl -i flow/librelane/tier1a/n2/pins.buda \
    flow/librelane/tier1a/n2/pins.ckpt.bdb plan
```

---

## 3. The pins

Two different objects, and the interesting thing is the difference between
them.

**(a) What BUDA planned** — `emit_pin_def` writes one `FP_DEF_TEMPLATE` per
leaf cell type into `n<N>/pins/<cell>.def`.  That is a PINS-only DEF: no
components, no nets, just each port's rectangle at the spot BUDA's
bit-wires reach the cell's face.

```bash
cd ~/src/buda
bin/viz flow/librelane/tier1a/n2/pins/pe_cell.def
```

No LEF argument: the file declares no cell, so there is no footprint to
look up.  Every pin is drawn as a boundary component at its own rectangle.

**(b) What the block actually got.**  The template is a request; the
hardened block is the answer, and the two are not required to agree.
Render the stage where the pins are set:

```bash
cd ~/src/buda/flow/librelane
python3 snapshots.py tier1a/n2/h/pe_cell/runs/h --stages applydeftemplate   # arm H+B
python3 snapshots.py tier1a/n4/h/pe_cell/runs/h --stages ioplacement        # arm H
```

`applydeftemplate` is the step that puts BUDA's template in, and it exists
only on an H+B block run; an arm-H block has `ioplacement` instead, which
is LibreLane's own placer deciding.  Rendering the same cell from both arms
side by side is the picture of what the template changed.

**(c) The difference, as text.**  `tools/pin_def_verify.py` is the check
the block README's step 1 runs — every TEMPLATE pin must appear in the
hardened DEF at the same ABSOLUTE rectangle (never the same `PLACED`
origin: OpenROAD re-centres every pin it writes):

```bash
cd ~/src/buda/flow/librelane/tier1a/n2/h
python3 ../../../../../tools/pin_def_verify.py \
    ../pins/pe_cell.def pe_cell/runs/h/final/def/pe_cell.def
```

This is what replaces `FP_TEMPLATE_MATCH_MODE strict`, which the arm cannot
use: BUDA plans against the emitter's structural view, whose cells declare
only the bus ports, while a block run synthesizes the twin with its
`clk`/`rst` — and strict mode exits 1 on exactly that gap.

---

## What this does not show

* **Routed metal in a BDB.**  `import_def_lef` reads placement and
  connectivity, not routed geometry, so a routing stage's BDB comes back
  with empty `net_segment`/`bus_segment`/`net_via`.  `snapshots.py` refuses
  to write one and says so; use `bin/viz <stage>.def` for somebody else's
  routing.
* **BUDA's plan and LibreLane's result in ONE picture.**  They are separate
  windows today.  The corridors BUDA writes (`out/buda_guides.json`) and
  the metal the router laid down (`44-*/tpu_top.def`) can each be drawn,
  but nothing overlays them.
* **DRC markers on the layout.**  `tier1a/drc_locate.py` maps every marker
  to the instance and the cell-local spot holding it, as text; nothing
  draws them.
