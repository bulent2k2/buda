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

"""The LOW-layers experiment: what does the bigsoc vehicle lose, and gain,
when the mock stack's lowest LOW layers are not declared?

`flow/tcl/bigsoc.tcl -low N` keeps only the N highest LOW layers (M2..M4 in
`flow/mockpdk/stack.buda`; the TOP layers M5..M7 always stay).  The netlist,
the PDK area model and therefore the auto_floorplan result are the same in
every arm -- this script CHECKS that (the die line must match) -- so any
difference in the routed result is the routing resources alone.

Each arm is one `bin/btcl -j 1 flow/tcl/bigsoc.tcl ...` run with
BUDA_REPLAN_PROF=1, one thread (the timings are comparable and not
parallelism artefacts), arms run one after the other.  Everything is read
from the run's own output:

  effectiveness  overlaps / unplaced / audit violations at the first audit
                 and at the end, doomed seats (abstract), abstract and
                 detailed wirelength, the wire per layer
  efficiency     wall, planner seconds, cuts and bands (the planner's
                 problem size), run_nuts seconds

Usage:
    tools/low_layers_experiment.py [--arms nq2,nq8full,nq8abs,nq16abs]
                                   [--keep -1,2,1,0] [--out DIR] [--reparse]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_DIAL8 = "-NC 4 -N 8 -NL3 4 -NMC 4 -NIO 16"
ARMS = {
    # name: (bigsoc.tcl arguments, what it measures)
    "nq2": ("2", "the default smoke dial, full flow, healers on"),
    "nq8full": (f"8 {_DIAL8} -noheal", "NQ=8 through DNUTS, healerless"),
    "nq8abs": (f"8 {_DIAL8} -abstract -noheal", "NQ=8, abstract NUTS only"),
    "nq16abs": (f"16 {_DIAL8} -abstract -noheal", "NQ=16, abstract NUTS only"),
}


def parse(text):
    """Read one run's output into a flat dict (missing facts stay absent)."""
    r = {}
    m = re.search(r"die (\d+x\d+)", text)
    if m:
        r["die"] = m.group(1)
    m = re.search(r"\[PlanProf\] bundles=(\d+).*?cuts=(\d+) bands=(\d+).*?"
                  r"total=([\d.]+)ms", text)
    if m:
        r["bundles"], r["cuts"], r["bands"] = (int(m.group(1)), int(m.group(2)),
                                               int(m.group(3)))
        r["planner_s"] = float(m.group(4)) / 1000.0
    m = re.search(r"\[PlanProf\].*?strict=([\d.]+)ms rank=[\d.]+ms\(fails=(\d+) "
                  r"scored=\d+\) ladder=([\d.]+)ms\(plans=(\d+) ripups=(\d+)\)",
                  text)
    if m:
        # the first planning pass and what it could not place: a failed
        # STRICT plan sends the bundle to the rip-up ladder, which re-plans
        r["strict_s"] = float(m.group(1)) / 1000.0
        r["strict_fails"] = int(m.group(2))
        r["ladder_s"] = float(m.group(3)) / 1000.0
        r["ladder_plans"] = int(m.group(4))
        r["ripups"] = int(m.group(5))
    m = re.search(r"\[NutsProf\] total=([\d.]+)s segments=(\d+) overlaps=(\d+)",
                  text)
    if m:
        r["run_nuts_s"] = float(m.group(1))
        r["segments"] = int(m.group(2))
    m = re.search(r"abstract audit -- (\d+) overlaps, (\d+) audit violations, "
                  r"(\d+) seat faults", text)
    if m:
        r["abs_ovl"], r["abs_viol"], r["abs_seats"] = map(int, m.groups())
    m = re.search(r"first audit -- (\d+) overlaps, (\d+) unplaced, "
                  r"(\d+) audit violations", text)
    if m:
        r["first_ovl"], r["first_unpl"], r["first_viol"] = map(int, m.groups())
    m = re.search(r"bigsoc\.tcl: (?:FAILED|clean)(?: \(abstract\))? -- "
                  r"(\d+) overlaps,(?: (\d+) unplaced,)? (\d+) audit violations",
                  text)
    if m:
        r["end_ovl"] = int(m.group(1))
        r["end_unpl"] = int(m.group(2) or 0)
        r["end_viol"] = int(m.group(3))
    lines = text.splitlines()
    for i, l in enumerate(lines):
        m = re.search(r"\[report_wirelength\] total (abstract|detailed) WL = "
                      r"(\d+)", l)
        if not m:
            continue
        kind = "abs" if m.group(1) == "abstract" else "det"
        r[f"{kind}_wl"] = int(m.group(2))
        for j in range(i - 1, max(i - 4, -1), -1):
            mm = re.search(r"by layer: (.*)", lines[j])
            if mm:
                r[f"{kind}_layers"] = dict(
                    (k, int(v)) for k, v in re.findall(r"(M\d)=(\d+)", mm.group(1)))
                break
        mb = re.search(r"/ (\d+) bit-wire", l)
        if mb:
            r["bit_wires"] = int(mb.group(1))
    return r


def tag_of(arm, keep, add=0):
    return f"{arm}_low{'all' if keep < 0 else keep}" + (f"_top{add}" if add else "")


def run_arm(arm, keep, outdir, extra_env, add=0):
    args, _what = ARMS[arm]
    cmd = [str(REPO / "bin" / "btcl"), "-j", "1", str(REPO / "flow/tcl/bigsoc.tcl")]
    cmd += args.split()
    if keep >= 0:
        cmd += ["-low", str(keep)]
    if add:
        cmd += ["-top", str(add)]
    env = dict(os.environ, BUDA_REPLAN_PROF="1", **extra_env)
    if os.environ.get("LOW_RECORD"):
        # write the session down as a flat .buda beside the log, for
        # tools/render_design.py (the recorder flattens the stack source)
        env["BUDA_RECORD"] = str(outdir / (tag_of(arm, keep, add) + ".buda"))
    t0 = time.time()
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True)
    wall = time.time() - t0
    text = p.stdout + "\n" + p.stderr
    tag = tag_of(arm, keep, add)
    (outdir / f"{tag}.log").write_text(text)
    r = parse(text)
    r.update(arm=arm, keep=keep, add_top=add, wall_s=round(wall, 1), rc=p.returncode)
    return r


def table(rows):
    cols = [("arm", "arm"), ("keep", "LOW kept"), ("add_top", "TOP added"), ("wall_s", "wall s"),
            ("planner_s", "planner s"), ("bands", "bands"),
            ("run_nuts_s", "run_nuts s"), ("abs_ovl", "abs ovl"),
            ("abs_seats", "seats"), ("first_unpl", "1st unpl"),
            ("end_ovl", "end ovl"), ("end_unpl", "end unpl"),
            ("end_viol", "end viol"), ("abs_wl", "abs WL"), ("det_wl", "det WL")]
    out = ["| " + " | ".join(h for _, h in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        cells = []
        for k, _ in cols:
            v = r.get(k, "")
            if k == "keep":
                v = "3 (all)" if v < 0 else v
            cells.append(f"{v:,}" if isinstance(v, int) and k not in ("keep",)
                         else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", default="nq2,nq8full,nq8abs,nq16abs")
    ap.add_argument("--keep", default="-1,2,1,0",
                    help="LOW layers kept per arm; -1 = the stack as it is")
    ap.add_argument("--match", action="store_true",
                    help="replace every dropped LOW layer by an added TOP layer: "
                         "each --keep K (K>=0) runs with -top (3-K)")
    ap.add_argument("--out", default=str(REPO / "flow/tcl/log/low_layers"))
    ap.add_argument("--reparse", action="store_true",
                    help="re-read the saved logs in --out into results.json "
                         "and results.md (wall time and exit code are kept "
                         "from the old results.json); runs nothing")
    a = ap.parse_args(argv)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    if a.reparse:
        old = json.loads((outdir / "results.json").read_text())
        rows = []
        for o in old:
            tag = tag_of(o["arm"], o["keep"], o.get("add_top", 0))
            r = parse((outdir / f"{tag}.log").read_text())
            r.update(arm=o["arm"], keep=o["keep"], add_top=o.get("add_top", 0), wall_s=o["wall_s"], rc=o["rc"])
            rows.append(r)
        (outdir / "results.json").write_text(json.dumps(rows, indent=1))
        md = table(rows)
        (outdir / "results.md").write_text(md + "\n")
        print(md)
        return
    rows = []
    for arm in a.arms.split(","):
        if arm not in ARMS:
            sys.exit(f"unknown arm {arm!r}; one of {', '.join(ARMS)}")
        dies = set()
        for keep in [int(k) for k in a.keep.split(",")]:
            r = run_arm(arm, keep, outdir, {}, add=(3 - keep) if a.match and keep >= 0 else 0)
            rows.append(r)
            dies.add(r.get("die"))
            print(f"{arm} keep={keep}: wall {r['wall_s']}s rc={r['rc']}",
                  flush=True)
            (outdir / "results.json").write_text(json.dumps(rows, indent=1))
        if len(dies) != 1:
            print(f"WARNING: {arm}: the die differs between arms {dies} -- "
                  "the floorplan is not held fixed", flush=True)
    md = table(rows)
    (outdir / "results.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main(sys.argv[1:])
