# Getting a power-routed DEF out of OpenROAD

**Status: RUN 2026-08-16.** Generated `flow/ariane133/ariane_pdn.def` on a
macOS dev machine through the `openroad/orfs:latest` Docker image
(`openroad -version` → `26Q3-1278-g4421880472`); the four verification
checks in §6 pass — 6673 metal paths read, 113969 via placements censused,
zero `unread_wire`. The script as first written did **not** run (§5 records
the `PDN-0008` fix it needed). Written so that whoever picks it up does not
have to re-derive which files, which commands, or which of the two errands
they are actually on.

This is the prerequisite named in three places — `opens_interchange.md`
item 15, `specialnets_scope.md` §4 and §5(0), and `flow/ariane133/ReadMe.md`
— each of which says "a power-routed DEF, which upstream generates rather
than ships" and stops there. This says how.

---

## 1. Why anything is needed at all

BUDA imports `SPECIALNETS` geometry as keepouts tagged `SPECIALNET <net>`
(`bdb.cpp`): power metal is real metal, and a signal cannot use it. But
`read_specialnet` collects points only while the next token is `(`, and DEF's
grammar puts optional clauses between the width and the first point:

```
+ ROUTED <layer> <width> [+ SHAPE <type>] [+ STYLE <n>] ( x y ) ( x y ) …
```

So a wire carrying `+ SHAPE` yields zero points and is dropped entirely.

**Measured against pdngen's own regression goldens** (`read_def`), before
the fix landed:

| golden | design | die | metal paths | read |
|---|---|---|---:|---:|
| `core_grid.defok` | gcd | 100 × 101 µm | 57 | **0** |
| `macros.defok` | RocketTile | 200 × 200 µm | 254 | **0** |
| `existing.defok` | RocketTile | 200 × 200 µm | 278 | **0** |
| `core_grid_snap.defok` | gcd | 100 × 101 µm | 96 | **0** |

Zero of 685, every one defeated by `+ SHAPE`. (The files hold 7466 wiring
clauses in total; the other 6781 are single-point via placements, which draw
no run — see `test/tests/data/pdn_goldens/ReadMe.md`.) This is not a claim
about what a generator *might* emit; it is what the generator's own goldens
contain.

That the count is visible at all is the 2026-08-15 census work: before it,
these files imported as `no_geometry` (a positive claim that the DEF drew
nothing) or as silence.

---

## 2. Two errands, and only one of them needs OpenROAD

They are routinely conflated. They are not the same work and they do not
have the same prerequisite.

**(0a) Does the reader read what a generator writes?** A parser question,
answerable against the goldens above. They are fetchable through the same
channel `flow/ariane133/fetch.py` already uses (`raw.githubusercontent.com`
is reachable; the GitHub *API* is not, so directory listing has to be
guessed at or read from a clone). **No OpenROAD install required.**
**LANDED 2026-08-16** — 685 of 685 metal paths now read; the fetcher and
fixtures live in `test/tests/data/pdn_goldens/`.

**(0b) What happens to our routing when a real PDN becomes keepouts?** A
QoR question, and the goldens cannot answer it: 200 × 200 µm with 269 nets
is a parser vehicle, not a design we route. This wants a PDN on
`flow/ariane133` — 1357 × 1357 µm, 133 macros, 5576 nets — and that means
running pdngen ourselves. **This document is for (0b).**

Do not let (0b)'s cost hold up (0a). The reader fix is byte-identical on
everything in the tree today (`demo/ariane/ariane.def` genuinely has no
`+ ROUTED` at all, and our two hand-authored DEFs already parse), which is
precisely why it was unmeasurable before the goldens and why it is safe to
land without the PDN run.

---

## 3. Installing OpenROAD

We need the `openroad` binary and its `pdn` module. We do **not** need
OpenROAD-flow-scripts, Yosys, or a full RTL-to-GDS flow: the design is
already floorplanned and placed, so the errand starts and ends inside one
`openroad` invocation.

**Not possible in the Claude Code Remote container**, and it is worth
recording why so nobody retries it: the agent proxy answers `403` to
`deb.debian.org` (no apt), `400` to `github.com` (no clone, no release
assets), `403 CONNECT` to `api.anaconda.org` (no conda) — only the git raw
channel and PyPI are open — and there were 6.9 GB of writable disk against a
source build that wants tens.

On a normal development machine, in order of preference:

1. **Docker.** The ORFS project publishes prebuilt images. This is what the
   2026-08-16 run used — `openroad/orfs:latest` (~1.6 GB, amd64), the
   `openroad` binary living at
   `/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad` (not on
   `PATH`, so name it as the entrypoint). Check the current tag in the
   OpenROAD-flow-scripts README rather than trusting `latest` long-term,
   since they move. The exact command that ran, from the repo root:

   ```bash
   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
     -v "$PWD":/work -w /work \
     --entrypoint /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad \
     openroad/orfs:latest -exit flow/ariane133/pdn.tcl
   ```

   `--user` makes the output DEF yours, not root's; `-w /work` is why the
   script's repo-relative paths resolve.

2. **Source build.** OpenROAD ships its own dependency installer:

   ```bash
   git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD.git
   cd OpenROAD
   sudo ./etc/DependencyInstaller.sh      # apt/brew packages + CMake deps
   ./etc/Build.sh                          # -> build/src/openroad
   ```

   Budget an hour and tens of GB. *(Command names from the repository's
   documented layout; not run in this session.)*

Verify whichever path with `openroad -version` before going further. The
`pdn` commands used below have been stable for several releases but the
option spellings are checked against upstream's own tests (§5), so if a
command errors, the fastest fix is to diff against the current
`src/pdn/test/*.tcl`.

---

## 4. The inputs — all of them already in the tree

Nothing new needs sourcing. This is the payoff of the ariane133 fetch work.

| file | where | notes |
|---|---|---|
| `NangateOpenCellLibrary.tech.lef` | `flow/ariane133/` via `fetch.py` | digest-pinned, 19,485 B. **Defines `SITE FreePDK45_38x28_10R_NP_162NW_34O`**, which is what the DEF's rows reference — verified this session |
| `fakeram45_256x16.lef` | `flow/ariane133/` via `fetch.py` | digest-pinned, the 57.57 × 133.0 µm SRAM |
| `ariane.def` | `demo/ariane/` | in the tree. `UNITS DISTANCE MICRONS 2000`, `DIEAREA ( 0 0 ) ( 2714720 2713760 )` = 1357 × 1357 µm, **962 `ROW`s**, 133 `COMPONENTS`, each `+ FIXED` with `+ HALO 10000 …` (5 µm) |
| PDN strategy | `OpenROAD-flow-scripts/flow/platforms/nangate45/grid_strategy-M1-M4-M7.tcl` | the platform default ORFS itself uses for ariane133 (`designs/nangate45/ariane133/config.mk` sets `PLATFORM = nangate45` and the same `ADDITIONAL_LEFS = …/fakeram45_256x16.lef`). Fetchable; reproduced in §5 |

Run `python3 flow/ariane133/fetch.py --check` first; if it reports anything
missing, `python3 flow/ariane133/fetch.py`.

**Not needed:** `NangateOpenCellLibrary.macro.lef`, the standard-cell LEF
with the proprietary header this repo deliberately does not vendor. The DEF
places only the 133 SRAMs — the 76,731 standard cells are in the netlist,
not the floorplan — so no `COMPONENTS` entry references a std cell footprint.
Followpin rails come off the `ROW` statements and the `SITE` in the tech LEF.
*(This is the one input assumption not verified by running the tool; if
pdngen complains, add the std cell LEF locally and do not check it in.)*

---

## 5. The script

`flow/ariane133/pdn.tcl` — checked in, and it runs (§header). It was NOT
checked in until it had been run, for the reason still worth stating: an
unrunnable script in the tree invites someone to believe it has been run.

The stripe geometry is ORFS's `grid_strategy-M1-M4-M7.tcl` verbatim (the
strategy ariane133 is built with); the surrounding read/write is OpenROAD's
own `src/pdn/test/macros.tcl` pattern, which is the closest test to this
design — a core grid plus per-macro grids over SRAMs.

**Two edits the ORFS strategy needed on THIS floorplan** (`ariane.def` is a
MacroPlacement output, not an ORFS one). Both are in the script below:

1. **`cut_rows` before the grids.** `ariane.def` lays 962 `ROW`s across the
   whole die, *uncut* under the 133 SRAMs — and the macros abut those rows
   flush. pdngen's macro-grid legality check then refuses the grid with
   `[ERROR PDN-0008] … halo overlaps row … reduce the halo to at most …`.
   `cut_rows -halo_width_x 2 -halo_width_y 2` removes the rows around each
   macro so the M5/M6 macro grids can be built. (An ORFS floorplan arrives
   with rows already cut, which is why the upstream strategy omits this.)

2. **An explicit small macro-grid `-halo`.** With no `-halo`, or with
   `-halo {0 0 0 0}` (0 is read as "unset"), the grid inherits the DEF's 5 µm
   placement halo and PDN-0008 fires again. `{0.1 0.1 0.1 0.1}` sits inside
   the 2 µm `cut_rows` band. The recipe's original `{2.0 2.0 2.0 2.0}` was
   too large — the macros abut rows with a sub-micron gap.

`macro_r90` finds no instances (all 133 SRAMs are R0-family — `N/S/FN/FS`)
and warns `PDN-1051`; harmless, kept so the strategy is complete.

```tcl
# Generate a power grid on the ariane133 floorplan.
#   openroad -exit flow/ariane133/pdn.tcl
# Inputs: fetch.py must have run (tech LEF + SRAM LEF).

read_lef flow/ariane133/NangateOpenCellLibrary.tech.lef
read_lef flow/ariane133/fakeram45_256x16.lef
read_def demo/ariane/ariane.def

# --- global connections (nangate45 platform defaults) ---
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$}   -power
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDPE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDCE$}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$}   -ground
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSSE$}
global_connect
set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

# --- cut core rows around the macros (see note 1 above; PDN-0008 without it) ---
cut_rows -halo_width_x 2 -halo_width_y 2

# --- standard-cell grid: M1 followpins, M4 + M7 stripes ---
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {metal7}
add_pdn_stripe  -grid {grid} -layer {metal1} -width {0.17} -pitch {2.4}  -offset {0} -followpins
add_pdn_stripe  -grid {grid} -layer {metal4} -width {0.48} -pitch {56.0} -offset {2}
add_pdn_stripe  -grid {grid} -layer {metal7} -width {1.40} -pitch {30.0} -offset {2}
add_pdn_connect -grid {grid} -layers {metal1 metal4}
add_pdn_connect -grid {grid} -layers {metal4 metal7}

# --- macro grids: M5/M6 over the SRAMs, by orientation class ---
# -halo {0.1 …} (not {2.0 …} / not unset) — see note 2 above.
define_pdn_grid -name {macro_r0} -voltage_domains {CORE} -macro \
  -orient {R0 R180 MX MY} -halo {0.1 0.1 0.1 0.1} -default
add_pdn_stripe  -grid {macro_r0} -layer {metal5} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_stripe  -grid {macro_r0} -layer {metal6} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_connect -grid {macro_r0} -layers {metal4 metal5}
add_pdn_connect -grid {macro_r0} -layers {metal5 metal6}
add_pdn_connect -grid {macro_r0} -layers {metal6 metal7}

define_pdn_grid -name {macro_r90} -voltage_domains {CORE} -macro \
  -orient {R90 R270 MXR90 MYR90} -halo {0.1 0.1 0.1 0.1} -default
add_pdn_stripe  -grid {macro_r90} -layer {metal6} -width {0.93} -pitch {40.0} -offset {2}
add_pdn_connect -grid {macro_r90} -layers {metal4 metal6}
add_pdn_connect -grid {macro_r90} -layers {metal6 metal7}

pdngen
write_def flow/ariane133/ariane_pdn.def
```

The maintained copy is `flow/ariane133/pdn.tcl` (checked in). If it and this
block ever diverge, the file is authoritative — it is the one that runs.

---

## 6. Checking the result before believing it

```bash
grep -o "+ SHAPE [A-Z]*" flow/ariane133/ariane_pdn.def | sort | uniq -c
```

Expect thousands of `STRIPE`, hundreds-to-thousands of `FOLLOWPIN` (962 rows
give roughly that many rails), and `RING` only if a ring is added — the ORFS
strategy above declares none.

Then run it through our reader, which is the actual point:

```python
import sys; sys.path.insert(0, "build")
import buda
from collections import Counter
d = buda.read_def("flow/ariane133/ariane_pdn.def")
print(len(d.special_wires),
      Counter(u.construct for u in d.unmodelled if "SPECIALNET" in u.construct))
```

Expect, since (0a) landed on 2026-08-16: every metal path read, the via
placements censused as `via_placement`, and **no `unread_wire` at all**. A
residual `unread_wire` or `partial_wire` means this design carries a form the
goldens do not — a via mid-path, a `RECT` or a `POLYGON` special wire — which
is worth knowing precisely because it would be the first vehicle for them
(`opens_interchange.md` item 15 names them as deliberately unbuilt).

A count of `0` wires would mean the reader has regressed under this document;
that is what the four goldens produced before the fix.

**Measured on the 2026-08-16 DEF:**

```
+ SHAPE census   : 1339 FOLLOWPIN, 119303 STRIPE  (no RING — none declared)
special_wires    : 6673   (1339 FOLLOWPIN + 5334 STRIPE)
unmodelled       : {'SPECIALNETS.via_placement': 113969}
```

120642 SPECIALNETS clauses in → 6673 wires + 113969 via placements out,
nothing dropped. (Most of the 119303 `+ SHAPE STRIPE` clauses are single-point
via placements carrying that keyword, which is why the STRIPE census dwarfs
the 5334 STRIPE *polylines*.) At ~175× the goldens' clause count, on a real
133-macro design, every metal path reads — the item-15 fix confirmed at scale.

---

## 7. Where the file goes, and where it does not

**Do not check the DEF in.** It is megabytes of generated artifact, and
digest-pinning our own output is theatre — a pin proves a file did not
change, which for a file we generate says nothing about whether it is right.

The reproducible unit is *this document plus the script*, the same way
`fetch.py` is the reproducible unit for the inputs. Record beside any
measurement made with it: the `openroad -version` string, the script's git
sha, and the input digests `fetch.py --check` reports. A QoR number taken
against an unrecorded PDN is not comparable to anything.

If a *committed* fixture is ever wanted for tests, use the fetched goldens
(§1) rather than our own generated DEF: they are upstream's bytes, small,
and covered by a licence we can point at.

---

## 8. What this unblocks

With `ariane_pdn.def` in hand, in the order the work is worth doing:

1. **Item 15's real measurement** — import the same design with and without
   the PDN and see what the keepouts do to the route. This is the number the
   reader fix is currently landing without.

   **MEASURED 2026-08-16 — and the answer is "it explodes the grid," not a
   QoR delta.** The measurement must be *controlled*: pdngen's `write_def`
   reconstructs a 495-net `NETS` section that `demo/ariane/ariane.def` does
   not have (its signal connectivity comes from `ariane.v`), so importing the
   whole `ariane_pdn.def` is a *different netlist* — 658 busterms / 244
   hbundles vs the baseline 161 / 111. To isolate the keepouts, splice only
   the PDN's `SPECIALNETS` block into the original DEF:

   ```bash
   head -n 3868 demo/ariane/ariane.def            > flow/ariane133/ariane_keepout.def
   awk '/^SPECIALNETS/{p=1} p{print} /^END SPECIALNETS/{exit}' \
       flow/ariane133/ariane_pdn.def             >> flow/ariane133/ariane_keepout.def
   tail -n +3878 demo/ariane/ariane.def          >> flow/ariane133/ariane_keepout.def
   ```

   Importing that (netlist now identical — 161 busterms / 111 hbundles
   confirmed) adds exactly the power metal as keepouts:
   `keepouts added: OBS:13034, SPECIALNET:6673` (vs baseline `OBS:13034`
   alone; the 113969 via placements are 0-width and correctly NOT keepouts).

   The result is that **`run_planner hier` becomes intractable.** The 6673
   PDN wires are die-crossing M4/M7 stripes and M1 followpins that lie
   *outside* every block, so `set_keepout_loci outside` — which tames the
   13034 OBS keepouts precisely because they sit *inside* macros — cannot
   suppress their Hanan loci. They add ~1069 x-lines and ~2836 y-lines, and
   the grid goes from the ~6,327 cells item 12 achieved to **~3.2 million**
   (~500×). Baseline routes in 11 s; the keepout variant was still grinding
   the planner at 96% CPU after 16 minutes (single-threaded, allocating) and
   was killed — the same regime item 12 hit on raw `demo/ariane` before its
   fix ("killed at 50 min").

   So this is **item 12 re-opened in a new direction.** `set_keepout_loci
   outside` is a block-*interior* mitigation; a real PDN's dominant keepouts
   are die-spanning stripes, which it structurally cannot reach. The route
   impact is not a congestion number to report — it is that the design cannot
   be planned at all until stripe loci get a mitigation of their own (a long
   thin power stripe blocks metal but should not contribute a Hanan line per
   edge across the whole die). That mitigation is the real follow-up this
   measurement surfaces; until it exists, a PDN-keepout QoR number is not
   obtainable on a design this size. Reproduce with the splice above +
   `flow/ariane133/ariane133.buda`'s pipeline against the spliced DEF.
2. **`specialnets_scope.md` (a)** — carry each strap's *net identity* into
   the session, not just its rectangle. Small and additive; nothing reads
   the field yet.
3. **`specialnets_scope.md` (b)** — teach the three NDR rail predicates
   (`ndr_rail_credits`, the R9 `NDR_SHIELD` audit, `emit_shield_bond_vias`)
   to see strap geometry beside pattern slots. This is the piece that makes
   NDR shielding mean something on an imported design, and the piece the
   whole scoping exercise was originally aimed at.

Note the ordering constraint from R4 (`opens_ndr.md`): credit and audit must
derive their answer from one shared predicate, so (b) is one function with
three callers, never three lookups.
