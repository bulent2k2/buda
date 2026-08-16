# Getting a power-routed DEF out of OpenROAD

**Status: recipe written, not yet run.** The inputs are verified present and
the reachability was probed on 2026-08-16; the `openroad` run itself needs a
machine this container is not (see §3). Written so that whoever picks it up
does not have to re-derive which files, which commands, or which of the two
errands they are actually on.

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

1. **Docker.** The ORFS project publishes prebuilt images; check the current
   tag in the OpenROAD-flow-scripts README rather than trusting a tag
   written down here, since they move. Mount the repo and run the script in
   §5. *(Image name not verified in this session.)*

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

`flow/ariane133/pdn.tcl` — write it there when running this; it is not
checked in yet because an unrunnable script in the tree invites someone to
believe it has been run.

The stripe geometry is ORFS's `grid_strategy-M1-M4-M7.tcl` verbatim (the
strategy ariane133 is built with); the surrounding read/write is OpenROAD's
own `src/pdn/test/macros.tcl` pattern, which is the closest test to this
design — a core grid plus per-macro grids over SRAMs.

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

# --- standard-cell grid: M1 followpins, M4 + M7 stripes ---
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {metal7}
add_pdn_stripe  -grid {grid} -layer {metal1} -width {0.17} -pitch {2.4}  -offset {0} -followpins
add_pdn_stripe  -grid {grid} -layer {metal4} -width {0.48} -pitch {56.0} -offset {2}
add_pdn_stripe  -grid {grid} -layer {metal7} -width {1.40} -pitch {30.0} -offset {2}
add_pdn_connect -grid {grid} -layers {metal1 metal4}
add_pdn_connect -grid {grid} -layers {metal4 metal7}

# --- macro grids: M5/M6 over the SRAMs, by orientation class ---
define_pdn_grid -name {macro_r0} -voltage_domains {CORE} -macro \
  -orient {R0 R180 MX MY} -halo {2.0 2.0 2.0 2.0} -default
add_pdn_stripe  -grid {macro_r0} -layer {metal5} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_stripe  -grid {macro_r0} -layer {metal6} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_connect -grid {macro_r0} -layers {metal4 metal5}
add_pdn_connect -grid {macro_r0} -layers {metal5 metal6}
add_pdn_connect -grid {macro_r0} -layers {metal6 metal7}

define_pdn_grid -name {macro_r90} -voltage_domains {CORE} -macro \
  -orient {R90 R270 MXR90 MYR90} -halo {2.0 2.0 2.0 2.0} -default
add_pdn_stripe  -grid {macro_r90} -layer {metal6} -width {0.93} -pitch {40.0} -offset {2}
add_pdn_connect -grid {macro_r90} -layers {metal4 metal6}
add_pdn_connect -grid {macro_r90} -layers {metal6 metal7}

pdngen
write_def flow/ariane133/ariane_pdn.def
```

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
