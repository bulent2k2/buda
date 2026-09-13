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

"""E2 -- abstract precision: how much does the top lose by seeing a block's
committed metal as a blob instead of as tracks?  (docs/internal/convergence_ladder.md)

One bottom-up run of the SoC vehicle decides what every block's INTERNAL
routing is -- the `hier.locked` bundles, solved once per cell and copied to
every instance.  That routing is then handed to the top level in four
abstractions, and the top-level nets alone are routed against each:

  none    no obstruction at all: the optimistic bound, what the top would get
          if the blocks were empty
  blob    per instance, per layer the block's routing USES: the whole instance
          footprint, inset by a pin-access ring -- what a LEF `OBS` says
  bbox    per instance, per layer: the bounding box of the routing actually
          on that layer
  exact   every locked segment's own rectangle on its own layer -- the
          per-track keepouts BUDA's own bottom-up path installs

The four arms are ONE flow with different `add_keepout` lines: the recording
of the bottom-up run with its block-internal buses removed (they are the
metal the keepouts stand for), its bottom-up marks cleared AFTER the alignment
nudge (so instances sit exactly where the source run put them), and a fixed
healerless tail.  Everything else -- cells, instances, layers, patterns,
top-level buses -- is byte-identical across arms, so the only variable is how
precisely the top sees the blocks.

Each arm is routed and rendered by `tools/render_design.py`, whose JSON
carries the metrics (the report's wirelength, overlaps, unplaced, the audit
verdicts) and whose panels show the keepouts hatched under the wires.

Usage:
  tools/experiment/e2_abstract_precision.py --nq 2 [--nq 4 ...] --out DIR
                                            [--pin-ring 8] [--healers]
                                            [--loci all|outside]

Writes DIR/soc<NQ>_source.buda (the recording), DIR/soc<NQ>_<arm>.buda,
DIR/soc<NQ>_<arm>_{fp,nuts,dnuts}.png + _meta.json, and DIR/e2_summary.json
with the table printed at the end.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build"), os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render_design  # noqa: E402  (run_flow + render)

ARMS = ("none", "blob", "bbox", "exact")
_DROP_PREFIX = ("set_bottom_up", "check_template_tracks")
_TAIL_START = "run_hier_bundler"


def record(nq, out_path):
    """`soc.tcl <nq> -bottomup`, recorded at the do_command choke point."""
    env = dict(os.environ, BUDA_RECORD=out_path)
    r = subprocess.run([os.path.join(_ROOT, "bin", "btcl"), "flow/tcl/soc.tcl",
                        str(nq), "-bottomup"], cwd=_ROOT, env=env,
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not os.path.exists(out_path):
        sys.exit(f"e2: recording soc.tcl {nq} -bottomup failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return r.stdout


def bus_nets(line):
    """Net names an `add_bus`/`add_net` line declares (the add_bus expansion
    rule: `p[N]` -> p_0..p_{N-1}, `p[lo:hi]` -> p_lo..p_hi)."""
    tok = line.split()
    if tok[0] == "add_net":
        return [tok[1]]
    m = re.match(r"^(.*)\[(\d+)(?::(\d+))?\]$", tok[1])
    if not m:
        return [tok[1]]
    base, a, b = m.group(1), int(m.group(2)), m.group(3)
    lo, hi = (0, a - 1) if b is None else (a, int(b))
    return [f"{base}_{i}" for i in range(lo, hi + 1)]


def locked_routing(s):
    """From the source session: the block-internal nets, and every placed
    segment of a locked bundle as (instance, layer, x1, y1, x2, y2)."""
    locked = {w.input.original_bundle.id: w for w in s.bundles if w.hier.locked}
    nets = set()
    inst_of = {}
    for bid, w in locked.items():
        hb = w.input.original_bundle
        nets.update(hb.get_net_names())
        inst_of[bid] = tuple(hb.instances)
    comps = {c.name: c for c in s.bdb.all_components()}

    def containing_instance(x1, y1, x2, y2):
        best = None
        for c in comps.values():
            if c.is_leaf or c.x2 <= c.x1:
                continue
            if c.x1 <= x1 and c.y1 <= y1 and c.x2 >= x2 and c.y2 >= y2:
                if best is None or (c.x2 - c.x1) * (c.y2 - c.y1) < (best.x2 - best.x1) * (best.y2 - best.y1):
                    best = c
        return best.name if best else None

    rects = []
    for g in s.nuts_result.segments:
        if g.bundle_id not in locked or getattr(g, "placed", True) is False:
            continue
        half = g.width / 2.0
        if g.horiz:
            x1, x2 = g.span_lo, g.span_hi
            y1, y2 = g.track_position - half, g.track_position + half
        else:
            y1, y2 = g.span_lo, g.span_hi
            x1, x2 = g.track_position - half, g.track_position + half
        x1, y1, x2, y2 = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        r = (math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2))
        insts = inst_of.get(g.bundle_id) or ()
        inst = insts[0] if len(insts) == 1 else containing_instance(*r)
        if inst is None:
            inst = "?"
        rects.append((inst, g.layer) + r)
    return nets, rects, comps


def arm_keepouts(arm, rects, comps, pin_ring):
    """The keepout rectangles (layer, x1, y1, x2, y2) one abstraction hands
    the top level."""
    if arm == "none":
        return []
    if arm == "exact":
        return [(l, x1, y1, x2, y2) for _, l, x1, y1, x2, y2 in rects]
    by = defaultdict(list)
    for inst, l, x1, y1, x2, y2 in rects:
        by[(inst, l)].append((x1, y1, x2, y2))
    out = []
    for (inst, l), rs in sorted(by.items()):
        if arm == "bbox":
            out.append((l, min(r[0] for r in rs), min(r[1] for r in rs),
                        max(r[2] for r in rs), max(r[3] for r in rs)))
        else:  # blob: the instance footprint, inset by the pin-access ring
            c = comps.get(inst)
            if c is None:
                # no instance to name: fall back to the routing's own box
                out.append((l, min(r[0] for r in rs), min(r[1] for r in rs),
                            max(r[2] for r in rs), max(r[3] for r in rs)))
                continue
            x1, y1, x2, y2 = int(c.x1), int(c.y1), int(c.x2), int(c.y2)
            ring = pin_ring if (x2 - x1 > 2 * pin_ring + 1 and y2 - y1 > 2 * pin_ring + 1) else 0
            out.append((l, x1 + ring, y1 + ring, x2 - ring, y2 - ring))
    return out


def area(keeps):
    return sum((x2 - x1) * (y2 - y1) for _, x1, y1, x2, y2 in keeps)


def write_arm(src_lines, arm, keeps, nets_to_drop, out_path, healers, loci):
    """The recording with the block-internal buses removed, the bottom-up
    marks cleared after alignment, the arm's keepouts, and a fixed tail."""
    out = [f"# E2 arm '{arm}': {len(keeps)} keepout(s), area {area(keeps)}",
           "# generated by tools/experiment/e2_abstract_precision.py -- do not hand-edit"]
    tail_from = None
    dropped_buses = 0
    for i, ln in enumerate(src_lines):
        st = ln.strip()
        if not st or st.startswith("#"):
            continue
        head = st.split()[0]
        if head == _TAIL_START:
            tail_from = i
            break
        if head in _DROP_PREFIX and not st.startswith("set_bottom_up *"):
            continue
        if head == "check_template_tracks":
            continue
        if head in ("add_bus", "add_net"):
            nets = bus_nets(st)
            inside = [n in nets_to_drop for n in nets]
            if all(inside):
                dropped_buses += 1
                continue
            if any(inside):
                print(f"e2: WARNING bus straddles locked/unlocked: {st}")
        out.append(st)
        if head == "align_bottom_up":
            # instances now sit where the source run put them; from here on
            # the blocks are keepouts, not templates
            out.append("set_bottom_up * off")
    if tail_from is None:
        sys.exit("e2: recording has no run_hier_bundler line")
    if loci:
        out.append(f"set_keepout_loci {loci}")
    for l, x1, y1, x2, y2 in keeps:
        out.append(f"add_keepout {x1} {y1} {x2} {y2} {l}")
    rest = [ln.strip() for ln in src_lines[tail_from:] if ln.strip() and not ln.strip().startswith("#")]
    bundler = next(l for l in rest if l.startswith("run_hier_bundler"))
    planner = next((l for l in rest if l.startswith("run_planner hier")), "run_planner hier 5")
    out += [bundler, "generate_hier_topologies", planner, "run_nuts", "check_design nuts",
            "run_detailed_nuts", "check_design dnuts"]
    if healers:
        out += ["negotiate_congestion 10", "ripup_reroute 20", "check_design dnuts"]
    out.append("report_wirelength")
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")
    return dropped_buses


def verdict_counts(line):
    """(violations, bundles) of one audit line: `Total: N violation(s) in G
    group(s) across B bundle(s)` or `Success: no violations found.`"""
    if line is None:
        return None, None
    m = re.search(r"(\d+) violation\(s\) in \d+ group\(s\) across (\d+) bundle", line)
    if m:
        return int(m.group(1)), int(m.group(2))
    return (0, 0) if "Success" in line else (None, None)


def dnuts_verdicts(verdicts):
    """The FIRST and LAST detailed audits of a flow whose audit lines run
    `nuts, dnuts[, healed dnuts]`: first = what the route was before any healer,
    last = the endpoint.  One dnuts audit means both are the same line."""
    if len(verdicts) < 2:
        return None, None
    return verdicts[1], verdicts[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--nq", type=int, action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pin-ring", type=int, default=8,
                    help="blob inset per side, layout units (pin access a LEF OBS leaves)")
    ap.add_argument("--healers", action="store_true", help="append negotiate+ripup to every arm")
    ap.add_argument("--loci", default=None, choices=[None, "all", "outside"],
                    help="emit set_keepout_loci <mode> before the keepouts")
    ap.add_argument("--dpi", type=int, default=110)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    summary = {"pin_ring": a.pin_ring, "healers": a.healers, "rows": []}

    for nq in a.nq:
        src = os.path.join(a.out, f"soc{nq}_source.buda")
        record(nq, src)
        s, log, secs = render_design.run_flow(src)
        nets, rects, comps = locked_routing(s)
        v = [ln.strip() for ln in log.splitlines()
             if "Success: no violations" in ln or "violation(s)" in ln]
        first, last = dnuts_verdicts(v)
        src_row = dict(nq=nq, arm="source (bottom-up, every net, as the flow ran)",
                       bundles=len(s.bundles), locked=sum(1 for w in s.bundles if w.hier.locked),
                       overlaps=s.nuts_result.num_overlaps,
                       unplaced=s.detailed_result.num_unplaced,
                       seconds=round(secs, 1), keepouts=None, keepout_area=None,
                       healed=len(v) > 2)
        src_row["first_violations"], src_row["first_viol_bundles"] = verdict_counts(first)
        src_row["violations"], src_row["viol_bundles"] = verdict_counts(last)
        summary["rows"].append(src_row)
        print(f"[e2] NQ={nq}: {len(nets)} block-internal nets in {src_row['locked']} locked bundles, "
              f"{len(rects)} locked segments")
        with open(src) as f:
            src_lines = f.read().splitlines()
        for arm in ARMS:
            keeps = arm_keepouts(arm, rects, comps, a.pin_ring)
            flow = os.path.join(a.out, f"soc{nq}_{arm}.buda")
            dropped = write_arm(src_lines, arm, keeps, nets, flow, a.healers, a.loci)
            prefix = os.path.join(a.out, f"soc{nq}_{arm}")
            meta = render_design.render(flow, prefix, title=f"E2 {arm} · soc.tcl {nq}", dpi=a.dpi)
            first, last = dnuts_verdicts(meta["verdicts"])
            fviol, fvb = verdict_counts(first)
            viol, vb = verdict_counts(last)
            row = dict(nq=nq, arm=arm, bundles=meta["bundles"], bit_wires=meta["bit_wires"],
                       overlaps=meta["overlaps"], unplaced=meta["unplaced"],
                       first_violations=fviol, first_viol_bundles=fvb,
                       violations=viol, viol_bundles=vb, detailed_wl=meta["detailed_wl"],
                       keepouts=len(keeps), keepout_area=area(keeps),
                       dropped_buses=dropped, seconds=meta["seconds"], healed=a.healers)
            summary["rows"].append(row)
            print(f"[e2] NQ={nq} {arm:5s}: keepouts={len(keeps):5d} area={area(keeps):>10d} "
                  f"bundles={row['bundles']} bits={row['bit_wires']} ovl={row['overlaps']} "
                  f"unpl={row['unplaced']} viol={viol}/{vb} detWL={row['detailed_wl']} {row['seconds']}s")

    with open(os.path.join(a.out, "e2_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(markdown_table(summary))


def markdown_table(summary):
    """A wirelength with unplaced bits is PARENTHESISED: it excludes them and
    is not comparable to a complete route (`report_wirelength` says so)."""
    def n(x):
        return "—" if x is None else str(x)
    out = ["| NQ | arm | keepouts | keepout layer-area | bundles | ovl | unpl | first audit | last audit | detailed WL | s |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in summary["rows"]:
        wl = r.get("detailed_wl")
        wl_s = "—" if wl is None else (f"({wl:,})" if (r["unplaced"] or 0) > 0 else f"{wl:,}")
        out.append(f"| {r['nq']} | {r['arm']} | {n(r.get('keepouts'))} | {n(r.get('keepout_area'))} | "
                   f"{r['bundles']} | {r['overlaps']} | {r['unplaced']} | "
                   f"{n(r.get('first_violations'))} ({n(r.get('first_viol_bundles'))}) | "
                   f"{n(r['violations'])} ({n(r['viol_bundles'])}) | {wl_s} | {r['seconds']} |")
    return "\n".join(out)


if __name__ == "__main__":
    main()
