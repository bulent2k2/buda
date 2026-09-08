#!/usr/bin/env python3
# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""LOOK at a LibreLane run, stage by stage -- renders and BDBs from the
snapshots a finished run already left behind.

    snapshots.py <run_dir> [--all] [--stages a,b,c] [--bdb] [--out DIR] [--list]

The study measures these runs thoroughly and never looks at them.  LibreLane
renders exactly ONE image, of the FINAL layout, at `*-klayout-render/`; the
eighteen intermediate DEFs a top run writes -- floorplan, macro placement,
PDN, global and detailed placement, CTS, global and detailed routing -- are
on disk and nothing ever draws them.  So a floorplan that is wrong is
invisible until a number twelve steps later is wrong, and by then the
picture that would have shown it is buried.

NOTHING IS RE-RUN.  Every artefact here comes from files the run already
wrote, so this works on runs finished weeks ago and costs seconds per stage.

RENDERING is KLayout's own `render.py` -- the script the flow's
`KLayout.Render` step uses -- which takes a DEF as readily as a GDS
(`gds = input.endswith(".gds")`, else DEF plus `--input-lef`).  Nothing is
reimplemented: the tech file, layer properties, layer map and cell LEFs all
come from the run's own `resolved.json`, so a stage renders with exactly the
colours and layer set the final render uses.

BDBs are written only for the stages where a BDB is the right container, and
that boundary is measured rather than assumed.  `import_def_lef` reads
COMPONENTS, NETS and pin connectivity -- so a placement stage lands complete
(on the tier-1a N=8 top at macro placement: 296 components, 4 cells, 3,586
nets, 6,928 pins) -- but it does NOT read routed geometry, so `net_segment`,
`bus_segment` and `net_via` come back EMPTY (measured: 0/0/0).  Those tables
are BUDA's OWN routing output.  A BDB of a routed LibreLane stage would
therefore look like an unrouted design and quietly mislead, so this refuses
to write one and says why; for somebody else's routing the DEF is the
artefact, and `bin/viz <stage>.def` opens it.

What each is good for:
  * PNG    -- every stage.  The quick "does this look right", and diffable
              by eye across stages or between arms.
  * BDB    -- placement stages.  Opens in `bin/fp` (move a macro, see HPWL
              and flylines move) and in `bin/viz`; persists as diffable
              `.bdb.sql` with `save_bdb <file>.sql`.
  * DEF    -- routing stages.  `bin/viz <run>/NN-step/<design>.def`.
"""
import argparse
import fnmatch
import glob
import json
import os
import re
import shlex
import subprocess
import sys

IMAGE = os.environ.get("LIBRELANE_IMAGE", "ghcr.io/librelane/librelane:3.0.11")
# The image is a Nix closure, so librelane's scripts live under a store path
# with a content hash in it -- not a fixed location, and it changes with the
# image.  Ask the interpreter where the package is rather than hard-coding a
# path that works until the next release.
_RENDER_SHIM = (
    "import os,sys,runpy,librelane;"
    "sys.argv[0]=os.path.join(os.path.dirname(librelane.__file__),"
    "'scripts','klayout','render.py');"
    "runpy.run_path(sys.argv[0],run_name='__main__')"
)
# The stages worth a look by default, in flow order: each is a point where a
# human eye catches something a metric does not until much later.
CURATED = [
    ("floorplan", "the die and the rows"),
    ("manualmacroplacement", "where the macros landed"),
    ("generatepdn", "the power grid, and whether it reaches the macros"),
    ("globalplacement", "std-cell spread"),
    ("detailedplacement", "legalised placement"),
    ("cts", "the clock tree"),
    ("globalrouting", "congestion"),
    ("detailedrouting", "the actual metal"),
]
# A BDB is written only up to here: past it the DEF carries ROUTING, which
# import_def_lef does not read (see the module docstring).
PLACEMENT_STAGES = {"floorplan", "manualmacroplacement", "generatepdn",
                    "globalplacementskipio", "ioplacement", "globalplacement",
                    "detailedplacement", "cutrows", "tapendcapinsertion",
                    "setpowerconnections", "repairdesignpostgpl", "cts"}


def stages(run_dir):
    """[(ordinal, step-dir name, bare stage name, def path)] in flow order."""
    out = []
    for d in sorted(glob.glob(os.path.join(run_dir, "*", "*.def"))):
        step = os.path.basename(os.path.dirname(d))
        m = re.match(r"(\d+)-\w+?-(\w+)$", step)
        if not m:
            continue
        out.append((int(m.group(1)), step, m.group(2), d))
    return sorted(out)


def resolved(run_dir):
    p = os.path.join(run_dir, "resolved.json")
    if not os.path.isfile(p):
        sys.exit(f"snapshots: no {p} -- is that a LibreLane run directory?")
    return json.load(open(p))


def by_corner(value, corner):
    """A LEF view is either a plain list or a dict KEYED BY CORNER PATTERN,
    and the two must not be confused: `list(dict)` yields the KEYS, so the
    renderer would be handed `nom_*` as a filename.

    LibreLane resolves `TECH_LEFS`/`CELL_LEFS` per corner --
    `{"nom_*": ..., "min_*": ..., "max_*": ...}` against a concrete
    `DEFAULT_CORNER` like `nom_tt_025C_1v80` -- so the pattern is matched
    with fnmatch and the exact key wins if present.  This is the rule
    `phase0/measure/read_resolved.py` already implements; it is repeated
    here rather than imported because that file is a standalone stdout
    helper for `run_or.sh`, not a module.  Codex #906."""
    if not isinstance(value, dict):
        return list(value or [])
    if corner in value:
        got = value[corner]
    else:
        got = next((v for k, v in value.items() if fnmatch.fnmatch(corner, k)), None)
        if got is None:
            got = next(iter(value.values()), None)
    return list(got) if isinstance(got, list) else ([got] if got else [])


def lefs(cfg, run_dir):
    """Every LEF the render needs: the PDK's, plus each macro's hardened one.

    The macro LEFs come from the run's own MACROS entry rather than from a
    guess about the tree layout, and a path that is not there is reported
    instead of silently dropped -- a macro rendered without its LEF is an
    empty box, which looks like a placement bug.

    The PDK views are corner-keyed (see `by_corner`), and the technology
    view's key is `TECH_LEFS` -- PLURAL.  Reading a singular `TECH_LEF`
    found nothing and passed the renderer NO technology LEF at all
    (Codex #906); the renders still came out because `-T`/`-M` supply the
    layer map, but the sites and layer definitions were missing."""
    corner = cfg.get("DEFAULT_CORNER") or "nom_tt_025C_1v80"
    out = by_corner(cfg.get("CELL_LEFS"), corner)
    out += by_corner(cfg.get("EXTRA_LEFS"), corner)
    tech = by_corner(cfg.get("TECH_LEFS") or cfg.get("TECH_LEF"), corner)
    out = tech + out
    missing = []
    for _cell, m in (cfg.get("MACROS") or {}).items():
        for p in (m.get("lef") or []):
            q = p if os.path.isabs(p) else os.path.normpath(os.path.join(run_dir, "..", p))
            (out if os.path.isfile(q) else missing).append(q)
    return out, missing


def render(def_path, png, cfg, run_dir, lef_paths, quiet=False):
    args = ["docker", "run", "--rm",
            "-v", f"{os.path.expanduser('~')}:{os.path.expanduser('~')}",
            "-w", os.getcwd(), IMAGE,
            "python3", "-c", _RENDER_SHIM]
    for l in lef_paths:
        args += ["-l", l]
    for flag, key in (("-T", "KLAYOUT_TECH"), ("-P", "KLAYOUT_PROPERTIES"),
                      ("-M", "KLAYOUT_DEF_LAYER_MAP")):
        if cfg.get(key):
            args += [flag, cfg[key]]
    args += ["-o", png,
             "--resolution", str(cfg.get("KLAYOUT_RENDER_RESOLUTION") or 1000),
             def_path]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        print(f"      render failed: {(r.stderr or r.stdout).strip().splitlines()[-1][:150]}")
    return r.returncode == 0


def write_bdb(def_path, bdb, lef_paths, buda, layers):
    """One BDB per placement stage, through BUDA's own importer."""
    macro_lefs = [l for l in lef_paths if "/final/lef/" in l]
    if not macro_lefs:
        return False, "no macro LEF in the run's MACROS entry"
    cat = bdb + ".lef"
    with open(cat, "w") as f:
        for l in macro_lefs:
            f.write(open(l).read() + "\n")
    script = bdb + ".buda"
    with open(script, "w") as f:
        f.write(f"open_bdb {bdb}\nset_import_scale dbu\nset_unit_check warn\n")
        for i, (n, d) in enumerate(layers, 1):
            f.write(f"def_layer {i} {n} {d} 30\n")
        f.write(f"import_def_lef {def_path} {cat} allow_missing_footprints\nsave_bdb\n")
    r = subprocess.run([buda, "--no-viz", script], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1][:120] if r.returncode else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--out", help="output directory (default <run_dir>/../snapshots)")
    ap.add_argument("--all", action="store_true", help="every stage, not the curated set")
    ap.add_argument("--stages", help="comma-separated stage names")
    ap.add_argument("--bdb", action="store_true", help="also write a BDB per PLACEMENT stage")
    ap.add_argument("--list", action="store_true", help="list the stages and exit")
    a = ap.parse_args(argv)

    run_dir = os.path.abspath(a.run_dir)
    st = stages(run_dir)
    if not st:
        sys.exit(f"snapshots: no stage DEFs under {run_dir}")
    if a.list:
        print(f"{len(st)} stage DEF(s) in {run_dir}:")
        for n, step, name, _ in st:
            mark = "*" if any(name == c for c, _ in CURATED) else " "
            print(f"  {mark} {n:3} {name:26} {step}")
        print("\n  * = in the curated default set")
        return 0

    want = ([s.strip() for s in a.stages.split(",")] if a.stages
            else None if a.all else [c for c, _ in CURATED])
    picked = [s for s in st if want is None or s[2] in want]
    if not picked:
        sys.exit(f"snapshots: no stage matched; --list shows what is there")

    cfg = resolved(run_dir)
    lef_paths, missing = lefs(cfg, run_dir)
    for m in missing:
        print(f"snapshots: WARNING: macro LEF not found, its cells will render empty: {m}")
    out = os.path.abspath(a.out or os.path.join(run_dir, "..", "snapshots"))
    os.makedirs(out, exist_ok=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    buda = os.path.join(os.path.dirname(root), "bin", "buda")
    buda = buda if os.path.isfile(buda) else os.path.join(root, "..", "bin", "buda")
    layers = [("met1", "H"), ("met2", "V"), ("met3", "H"), ("met4", "V"), ("met5", "H")]

    rows = []
    for n, step, name, dp in picked:
        why = dict(CURATED).get(name, "")
        png = os.path.join(out, f"{n:02d}-{name}.png")
        ok = render(dp, png, cfg, run_dir, lef_paths)
        size = os.path.getsize(png) if ok and os.path.isfile(png) else 0
        bdb_note = ""
        if a.bdb:
            if name in PLACEMENT_STAGES:
                bp = os.path.join(out, f"{n:02d}-{name}.bdb")
                bok, err = write_bdb(dp, bp, lef_paths, buda, layers)
                bdb_note = os.path.basename(bp) if bok else f"failed: {err}"
            else:
                bdb_note = "skipped — a routed DEF's geometry is not read into a BDB (see --help)"
        print(f"  {n:3} {name:24} {'png ' + str(size // 1024) + 'k' if ok else 'PNG FAILED':14} {bdb_note}")
        rows.append((n, name, why, os.path.basename(png) if ok else None, bdb_note))

    idx = os.path.join(out, "index.md")
    with open(idx, "w") as f:
        f.write(f"# Stage snapshots — `{os.path.relpath(run_dir, os.getcwd())}`\n\n")
        f.write("Rendered from the DEFs the run already wrote; nothing was re-run.\n\n")
        f.write("| # | stage | what to look for | render | BDB |\n|---|---|---|---|---|\n")
        for n, name, why, png, bdb in rows:
            f.write(f"| {n} | `{name}` | {why} | {'![](' + png + ')' if png else '—'} | {bdb or '—'} |\n")
        f.write("\nOpen a routing stage's own DEF with `bin/viz <run>/NN-step/<design>.def`;\n"
                "open a placement BDB with `bin/fp <file>.bdb`.\n")
    print(f"\nsnapshots: {len(rows)} stage(s) -> {out}\n  index: {idx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
