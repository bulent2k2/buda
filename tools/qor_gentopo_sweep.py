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

"""Sweep the gen-topo knobs no_hanan_loci / multi_trunk over the QoR corpus.

The reproducible harness behind
docs/internal/gentopo_loci_multitrunk_2026-07.md.  Runs every corpus flow (the
34 full-pipeline flows under flow/big_data_test[/big2], flow/hbundles, flow/rnr
— see docs/internal/qor_corpus.md) four ways, isolating exactly the two
candidate-generation knobs and holding everything else in each flow fixed:

  baseline       : neither flag  (default-on Hanan loci, no multi-trunk trees)
  no_hanan_loci  : midpoint-only trunk loci
  multi_trunk    : add BITRUNK_HVH/VHV two-level datapath trees
  both           : multi_trunk + no_hanan_loci

Each flow runs AS WRITTEN with script_path set (so the shipped kSegsRel /
run_nuts dead-span gates engage on healer flows) EXCEPT that every
generate_topologies / generate_hier_topologies command is rewritten to force
exactly the two controlled knobs for the setting — any pre-existing
multi_trunk / no_hanan_loci / hanan_loci token is stripped first, so the four
settings differ ONLY in these two knobs.  visualize* / report_wl* / exit are
skipped so the whole flow runs.

Metrics per run: abstract NUTS wirelength (Σ|span| over placed segments), NUTS
overlaps, DetailedNUTS opens (unplaced bits), wall runtime.

Usage (from anywhere; paths resolve off the repo root this file lives in):

    python3 tools/qor_gentopo_sweep.py [--json OUT.json] [--only SUBSTR ...]

Counts are host-sensitive (-march=native FP) and single-run wall times; trends,
not absolute values, are the result.
"""
import argparse
import contextlib
import glob
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("build", "src", "tools"):
    sys.path.insert(0, os.path.join(ROOT, p))
import buda        # noqa: E402  (ostream redirect)
import buda_cli    # noqa: E402

# The corpus = every full-pipeline (run_detailed_nuts) flow in these four dirs,
# the same set docs/internal/qor_corpus.md documents.
_CORPUS_DIRS = ("flow/big_data_test", "flow/big_data_test/big2",
                "flow/hbundles", "flow/rnr")

SETTINGS = {
    "baseline":       [],
    "no_hanan_loci":  ["no_hanan_loci"],
    "multi_trunk":    ["multi_trunk"],
    "both":           ["multi_trunk", "no_hanan_loci"],
}
_ORDER = list(SETTINGS)

_GEN = ("generate_topologies", "generate_hier_topologies")
_CTRL = {"multi_trunk", "no_hanan_loci", "hanan_loci"}
_SKIP = {"visualize", "visualize_topologies", "report_wl",
         "report_wirelength", "exit"}


def corpus():
    """The 34 full-pipeline corpus flows (repo-relative), sorted+deduped."""
    out = set()
    for d in _CORPUS_DIRS:
        for f in glob.glob(os.path.join(ROOT, d, "*.buda")):
            with open(f) as fh:
                if any(line.split("#", 1)[0].split()[:1] == ["run_detailed_nuts"]
                       for line in fh):
                    out.add(os.path.relpath(f, ROOT))
    return sorted(out)


def _rewrite_gen(cmd_line, extra):
    parts = cmd_line.strip().split()
    kept = [p for p in parts[1:] if p.lower() not in _CTRL]
    return " ".join([parts[0]] + kept + extra)


def _metrics(s):
    wl, unpl_seg, overlaps = 0.0, 0, None
    nr = getattr(s, "nuts_result", None)
    if nr is not None:
        for t in nr.segments:
            if getattr(t, "placed", True):
                wl += abs(t.span_hi - t.span_lo)
            else:
                unpl_seg += 1
        overlaps = nr.num_overlaps
    dr = getattr(s, "detailed_result", None)
    opens = dr.num_unplaced if dr is not None else None
    return dict(wl=round(wl, 1), overlaps=overlaps, opens=opens,
                unplaced_seg=unpl_seg,
                bundles=len(s.bundles) if getattr(s, "bundles", None) else 0)


def run_flow(path, extra):
    """Run one corpus flow with the two knobs forced to `extra`; return metrics."""
    abspath = os.path.join(ROOT, path) if not os.path.isabs(path) else path
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = abspath
    orig = s.do_command

    def patched(cmd_line):
        stripped = cmd_line.strip()
        if not stripped or stripped.startswith("#"):
            return orig(cmd_line)
        cmd = stripped.split()[0].lower()
        if cmd in _GEN:
            cmd_line = _rewrite_gen(stripped, extra)
        elif cmd in _SKIP:
            return None
        return orig(cmd_line)

    s.do_command = patched
    t0 = time.perf_counter()
    err = None
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()), \
            buda.ostream_redirect():
        try:
            s.run_command(f"source {abspath}")
            s._flush_bdb_writeback()
        except SystemExit:
            pass                       # a stray exit slipped through (shouldn't)
        except BaseException as e:     # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
    m = _metrics(s)
    m["runtime"] = round(time.perf_counter() - t0, 2)
    m["error"] = err
    return m


def _short(flow):
    return flow.replace("flow/", "").replace("big_data_test/", "").replace(
        ".buda", "")


def report(data):
    """Print the three 4-column tables (opens / WL / runtime) from a result dict."""
    def opv(m):
        return f"{m['opens']}" + (f" ({m['overlaps']}ov)" if m['overlaps'] else "")

    print("\n### opens  [overlaps in parens]\n")
    print("| flow | " + " | ".join(_ORDER) + " |")
    print("|---" + "|--:" * len(_ORDER) + "|")
    tot = {s: [0, 0] for s in _ORDER}
    for flow, r in data.items():
        cells = []
        for s in _ORDER:
            cells.append(opv(r[s]))
            tot[s][0] += r[s]["opens"] or 0
            tot[s][1] += r[s]["overlaps"] or 0
        print(f"| `{_short(flow)}` | " + " | ".join(cells) + " |")
    print("| **TOTAL opens/ov** | " +
          " | ".join(f"**{tot[s][0]}/{tot[s][1]}**" for s in _ORDER) + " |")

    print("\n### abstract wirelength (Σ|span|), Δ% vs baseline\n")
    print("| flow | " + " | ".join(_ORDER) + " |")
    print("|---" + "|--:" * len(_ORDER) + "|")
    sw = {s: 0.0 for s in _ORDER}
    for flow, r in data.items():
        b = r["baseline"]["wl"] or 1e-9
        cells = [f"{r['baseline']['wl']:.0f}"]
        for s in _ORDER[1:]:
            cells.append(f"{r[s]['wl']:.0f} ({(r[s]['wl']-b)/b*100:+.1f}%)")
        for s in _ORDER:
            sw[s] += r[s]["wl"]
        print(f"| `{_short(flow)}` | " + " | ".join(cells) + " |")
    bt = sw["baseline"] or 1e-9
    print("| **TOTAL** | " + f"**{sw['baseline']:.0f}**" + " | " +
          " | ".join(f"**{(sw[s]-bt)/bt*100:+.1f}%**" for s in _ORDER[1:]) + " |")

    print("\n### runtime (s)\n")
    print("| flow | " + " | ".join(_ORDER) + " |")
    print("|---" + "|--:" * len(_ORDER) + "|")
    sr = {s: 0.0 for s in _ORDER}
    for flow, r in data.items():
        print(f"| `{_short(flow)}` | " +
              " | ".join(f"{r[s]['runtime']:.2f}" for s in _ORDER) + " |")
        for s in _ORDER:
            sr[s] += r[s]["runtime"]
    b = sr["baseline"] or 1e-9
    print("| **TOTAL** | " +
          " | ".join(f"**{sr[s]:.1f}** ({(sr[s]-b)/b*100:+.0f}%)"
                     if s != "baseline" else f"**{sr[s]:.1f}**"
                     for s in _ORDER) + " |")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", metavar="OUT",
                    help="write the raw result dict to this JSON file")
    ap.add_argument("--only", nargs="*", metavar="SUBSTR",
                    help="restrict to corpus flows whose path contains any SUBSTR "
                         "(handy for a quick subset run)")
    args = ap.parse_args()

    flows = corpus()
    if args.only:
        flows = [f for f in flows if any(s in f for s in args.only)]
    data = {}
    for i, flow in enumerate(flows, 1):
        data[flow] = {}
        for name, extra in SETTINGS.items():
            m = run_flow(flow, extra)
            data[flow][name] = m
            tag = (f"ov={m['overlaps']} op={m['opens']} wl={m['wl']} "
                   f"{m['runtime']}s" + (f" ERR={m['error']}" if m['error'] else ""))
            print(f"[{i:2}/{len(flows)}] {flow:52} {name:14} {tag}", flush=True)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(data, f, indent=1)
    report(data)
    print("\nDONE")


if __name__ == "__main__":
    main()
