#!/usr/bin/env python3
"""Prepare the inputs `pins.buda` imports, from the phase-0 runs.

    python3 prep_pins.py            # after docs/internal/librelane_hier_flow.md §8 steps 2 and 4

Writes, beside this script:

  two_reg32_fp.def   the top's FINAL DEF (runs/phase0/final/def/two_reg32.def)
                     reduced to what a BUDA import of the FLOORPLAN needs:
                     header, DIEAREA, TRACKS, the reg32 macro instances, and
                     the `mid` bus nets with their wiring stripped.
  reg32_macro.lef    the hardened block's LEF (../reg32/runs/phase0/final/lef/
                     reg32.lef), copied; any signal port the LEF marks `USE
                     CLOCK` is rewritten `USE SIGNAL` (said below).
  sky130_tech.lef    the PDK's tech LEF the run resolved (`TECH_LEFS` in
                     runs/phase0/resolved.json, matched by corner the way
                     ../measure/read_resolved.py does), copied.

WHY A REDUCED DEF.  The final DEF is the one file of the top run with a
fixed path, and it carries three things the routing question does not want:
100 standard cells (tap cells, clock buffers) with no LEF here — BUDA's
importer refuses a component with no footprint rather than guess one —
the routed wiring, and the PDN.  The step DEFs that lack them live under
numbered directories (`NN-odb-manualmacroplacement/`) whose NN is
LibreLane's to choose.  So this takes the final DEF and keeps the
floorplan: the macros as placed, the bus between them, the track grid.

WHY ONLY THE BUS NETS.  `emit_pin_def reg32` builds ONE template for the
cell from what every instance routes.  `mid` enters u1 west and leaves u0
east, so d comes from u1 and q from u0 and the two agree by construction.
Keep the port nets (clk, rst, d, q from the die pins) and BUDA routes them
too — u0's d from wherever `IOPlacement` put the die pins, u1's d from the
bus — and the two instances then DISAGREE on d, which the writer refuses
(correctly: a cell hardened once cannot have two pin maps).  The die pins
are dropped with those nets.  clk and rst still reach the template: the
writer reads the cell's port list from the macro LEF and spreads the ports
no bus reaches on the south edge, which is where the hand template put them.

WHY USE CLOCK -> USE SIGNAL.  BUDA's LEF reader treats a `USE CLOCK` pin as
a pre-route, like POWER, and drops it from the cell's pin list; a template
without `clk` is refused by `FP_TEMPLATE_MATCH_MODE strict`.  The rewrite
touches the COPY only and changes nothing about the block; the count of
rewritten pins is printed so it cannot pass unnoticed.

A pass prints one line per output with the counts, ending `prep_pins: ok`.
It fails LOUDLY — non-zero, naming the file and the shape — when a run
output is missing, the DEF has no TRACKS or no reg32 instance, the bus has
no nets, or the tech LEF cannot be found (`PDK_ROOT` may be passed to
relocate it when the run's path is from another machine).
"""
import argparse
import fnmatch
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_SECTION = re.compile(r"^(\w+)\s+(\d+)\s*;\s*\n(.*?)^END \1\s*$", re.S | re.M)
_ENTRY = re.compile(r"^\s*-\s+(\S+)(.*?);", re.S | re.M)
_CONN = re.compile(r"\(\s*(\S+)\s+(\S+)\s*\)")
_HEADER_KEEP = ("VERSION", "DIVIDERCHAR", "BUSBITCHARS", "DESIGN", "UNITS",
                "DIEAREA", "TRACKS")


def fail(msg):
    print(f"prep_pins: FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def macro_names(lef_text):
    return re.findall(r"^\s*MACRO\s+(\S+)", lef_text, re.M)


def reduce_def(text, macros, bus_prefix):
    """The floorplan half of a routed DEF: header statements, the macro
    instances, the bus nets (connections on kept instances only, wiring
    dropped).  Returns (text, n_instances, n_nets, n_tracks)."""
    header = []
    for ln in text.splitlines():
        s = ln.strip()
        if any(s.startswith(k + " ") or s == k for k in _HEADER_KEEP):
            header.append(s)
    sections = {m.group(1): m.group(3) for m in _SECTION.finditer(text)}
    if "COMPONENTS" not in sections:
        fail("the DEF has no COMPONENTS section")
    if "NETS" not in sections:
        fail("the DEF has no NETS section")
    if not any(h.startswith("TRACKS ") for h in header):
        fail("the DEF has no TRACKS statements — the track positions come "
             "from here and nowhere else (is this a DEF OpenROAD wrote after "
             "floorplanning?)")
    if not any(h.startswith("DIEAREA ") for h in header):
        fail("the DEF has no DIEAREA")
    comps, kept = [], set()
    for e in _ENTRY.finditer(sections["COMPONENTS"]):
        name, rest = e.group(1), e.group(2)
        cell = rest.split()[0] if rest.split() else ""
        if cell in macros:
            kept.add(name)
            # `+ SOURCE DIST` is provenance BUDA's reader does not model and
            # reports as unmodelled; it says nothing about the floorplan.
            rest = re.sub(r"\+\s*SOURCE\s+\S+", "", rest)
            comps.append("  - " + name + " " + " ".join(rest.split()) + " ;")
    nets = []
    for e in _ENTRY.finditer(sections["NETS"]):
        name, rest = e.group(1), e.group(2)
        plain = name.replace("\\", "")
        if not plain.startswith(bus_prefix):
            continue
        body = rest.split("+", 1)[0]
        conns = [f"( {i} {p} )" for i, p in _CONN.findall(body) if i in kept]
        if not conns:
            continue
        nets.append(f"  - {name} {' '.join(conns)} + USE SIGNAL ;")
    out = header + [f"COMPONENTS {len(comps)} ;"] + comps + ["END COMPONENTS",
           f"NETS {len(nets)} ;"] + nets + ["END NETS", "END DESIGN", ""]
    n_tracks = sum(1 for h in header if h.startswith("TRACKS "))
    return "\n".join(out), len(comps), len(nets), n_tracks


def tech_lef_path(resolved_path, pdk_root=None):
    c = json.load(open(resolved_path))
    corner = c.get("DEFAULT_CORNER", "nom_tt_025C_1v80")
    v = c["TECH_LEFS"]
    if isinstance(v, dict):
        hits = [k for k in v if fnmatch.fnmatch(corner, k)]
        if len(hits) != 1:
            fail(f"TECH_LEFS: corner {corner!r} matches {len(hits)} of "
                 f"{sorted(v)} — expected exactly one")
        v = v[hits[0]]
    if isinstance(v, list):
        v = v[0]
    if pdk_root and c.get("PDK_ROOT") and v.startswith(c["PDK_ROOT"]):
        v = pdk_root + v[len(c["PDK_ROOT"]):]
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", default=os.path.join(HERE, "runs", "phase0"),
                    help="the top's LibreLane run directory (§8 step 4)")
    ap.add_argument("--def", dest="def_path", default=None,
                    help="the top's DEF (default: <run>/final/def/two_reg32.def)")
    ap.add_argument("--macro-lef", default=os.path.join(
        HERE, "..", "reg32", "runs", "phase0", "final", "lef", "reg32.lef"),
        help="the hardened block's LEF (§8 step 2)")
    ap.add_argument("--bus", default="mid", help="the bus prefix to keep")
    ap.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT"),
                    help="relocate the run's TECH_LEFS path onto this root")
    ap.add_argument("--out-dir", default=HERE)
    a = ap.parse_args()

    def_path = a.def_path or os.path.join(a.run, "final", "def", "two_reg32.def")
    for p, why in ((def_path, "run §8 step 4: librelane --dockerized "
                              "--run-tag phase0 config.json in two_reg32/"),
                   (a.macro_lef, "run §8 step 2: librelane --dockerized "
                                 "--run-tag phase0 config.json in reg32/"),
                   (os.path.join(a.run, "resolved.json"),
                    "the run directory holds no resolved.json — is --run a "
                    "LibreLane run?")):
        if not os.path.isfile(p):
            fail(f"missing {p}\n  remedy: {why}")

    lef_text = open(a.macro_lef).read()
    macros = set(macro_names(lef_text))
    if not macros:
        fail(f"{a.macro_lef} declares no MACRO")
    n_clock = len(re.findall(r"^\s*USE\s+CLOCK\s*;", lef_text, re.M))
    lef_out = re.sub(r"^(\s*)USE\s+CLOCK\s*;", r"\1USE SIGNAL ;", lef_text,
                     flags=re.M)
    n_pins = len(re.findall(r"^\s*PIN\s+\S+", lef_out, re.M))

    reduced, n_inst, n_nets, n_tracks = reduce_def(open(def_path).read(),
                                                   macros, a.bus)
    if n_inst == 0:
        fail(f"{def_path}: no instance of {sorted(macros)} in COMPONENTS")
    if n_nets == 0:
        fail(f"{def_path}: no net named {a.bus}* connects the kept instances")

    tl = tech_lef_path(os.path.join(a.run, "resolved.json"), a.pdk_root)
    if not os.path.isfile(tl):
        fail(f"tech LEF {tl} (from resolved.json TECH_LEFS) is not readable "
             f"here\n  remedy: pass --pdk-root <your PDK root> (or set "
             f"PDK_ROOT) to relocate it")

    os.makedirs(a.out_dir, exist_ok=True)
    p_def = os.path.join(a.out_dir, "two_reg32_fp.def")
    p_lef = os.path.join(a.out_dir, "reg32_macro.lef")
    p_tech = os.path.join(a.out_dir, "sky130_tech.lef")
    open(p_def, "w").write(reduced)
    open(p_lef, "w").write(lef_out)
    shutil.copyfile(tl, p_tech)
    print(f"{p_def}: {n_inst} instance(s) of {'/'.join(sorted(macros))}, "
          f"{n_nets} {a.bus}* net(s), {n_tracks} TRACKS statement(s); "
          f"other components, nets, PINS and wiring dropped")
    print(f"{p_lef}: {n_pins} pin(s); {n_clock} USE CLOCK pin(s) rewritten "
          f"USE SIGNAL so the template carries them")
    print(f"{p_tech}: copied from {tl}")
    print("prep_pins: ok")


if __name__ == "__main__":
    main()
