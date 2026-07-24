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
"""Run the QoR corpus and compare two runs — the "did my change help or hurt?"
tool for any branch that touches topology / planner / NUTS.

Each flow is sourced end-to-end and its final routing quality captured as
`(num_overlaps, num_unplaced)` — the two numbers a routing change most often
moves.  The value for a branch is the DIFF against a baseline: build `main`,
capture a baseline; build the branch, capture again; `--compare` the two.

Usage:
  # capture this build's QoR over the default corpus
  PYTHONPATH=build:src:tools tools/qor_corpus.py --out mine.json

  # baseline-vs-branch recipe (the point of this tool)
  git checkout main   && bin/bb && tools/qor_corpus.py --out base.json
  git checkout branch && bin/bb && tools/qor_corpus.py --out mine.json
  tools/qor_corpus.py --compare base.json mine.json

  # just a subset (e.g. while iterating on one flow)
  tools/qor_corpus.py --flows flow/rnr/mix.buda flow/rnr/slowdown_rnr.buda

Notes:
  * A flow that raises is recorded with an "err" field, not skipped silently.
  * `overlaps`/`unplaced` are None when the flow never reached that stage
    (e.g. no run_detailed_nuts) — reported as such, never coerced to 0.
  * The corpus below is the subset of flow/ that runs the full pipeline
    through run_detailed_nuts.  Edit CORPUS to add/remove vehicles.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build"), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

# The curated corpus: flow/ vehicles that route the full pipeline through
# run_detailed_nuts (so both overlaps and unplaced are meaningful).  Paths are
# relative to the repo root.
CORPUS = [
    "flow/big_data_test/b44.buda",
    "flow/big_data_test/b61.buda",
    "flow/big_data_test/big.buda",
    "flow/big_data_test/big2/b1_bus_007.buda",
    "flow/big_data_test/big2/b24_bus_056.buda",
    "flow/big_data_test/big2/b34_bus_028.buda",
    "flow/big_data_test/big2/b3_bus_023.buda",
    "flow/big_data_test/big2/b4_bus_077.buda",
    "flow/big_data_test/big2/big2.buda",
    "flow/big_data_test/big2/big2_b4_b24.buda",
    "flow/big_data_test/big2/big2_noviz.buda",
    "flow/big_data_test/big2/tc3b_flat.buda",
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda",
    "flow/big_data_test/big_3bundles_sel_trunk+mst_topo.buda",
    "flow/big_data_test/tc3a.buda",
    "flow/hbundles/01_pipeline_hier.buda",
    "flow/hbundles/02_two_procs.buda",
    "flow/hbundles/03_priority_ordering.buda",
    "flow/hbundles/04_deep_hierarchy.buda",
    "flow/hbundles/05_stress_grid.buda",
    "flow/hbundles/06_multipin_stress.buda",
    "flow/hbundles/07_wide_fan_stress.buda",
    "flow/hbundles/08_cross_level.buda",
    "flow/hbundles/09_local_global_compete.buda",
    "flow/hbundles/10_chip_units_blocks_leaf.buda",
    "flow/rnr/mix.buda",
    "flow/rnr/mix2.buda",
    "flow/rnr/mix2_fast.buda",
    "flow/rnr/mix2_fast_bottomup.buda",
    "flow/rnr/mix2_fast_on_aligned_sql.buda",
    "flow/rnr/mix2_fast_topdown.buda",
    "flow/rnr/mix2_repro.buda",
    "flow/rnr/slowdown_rnr.buda",
]


def run_flow(flow):
    """Source one flow end-to-end and capture its final QoR.  Returns a dict
    with overlaps/unplaced/sec, or an err field if the flow raised."""
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    d = os.path.dirname(flow)
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cwd = os.getcwd()
            try:
                # Flows use paths relative to their own directory (source ...).
                if d:
                    os.chdir(d)
                s.do_command(f"source {os.path.basename(flow)}")
            finally:
                os.chdir(cwd)
    except SystemExit:
        pass                                    # `exit` in a flow is normal
    except Exception as e:                      # noqa: BLE001 — record, don't crash the sweep
        return {"flow": flow, "err": f"{type(e).__name__}: {str(e)[:80]}"}
    dt = time.time() - t0
    ov = getattr(getattr(s, "nuts_result", None), "num_overlaps", None)
    un = getattr(getattr(s, "detailed_result", None), "num_unplaced", None)
    return {"flow": flow, "overlaps": ov, "unplaced": un, "sec": round(dt, 1)}


def _fmt(r):
    if "err" in r:
        return r["err"]
    return f"{r.get('overlaps')}/{r.get('unplaced')}"


def cmd_run(flows, out):
    os.chdir(_ROOT)                             # flow paths are repo-root-relative
    results = []
    for f in flows:
        r = run_flow(f)
        results.append(r)
        print(json.dumps(r), flush=True)
    if out:
        with open(out, "w") as fh:
            json.dump(results, fh, indent=1)
        print(f"\nwrote {len(results)} results -> {out}")
    return results


def _score(r):
    """A single comparable number: overlaps + unplaced (None -> 0 for ordering
    only).  err rows sort worst."""
    if "err" in r:
        return float("inf")
    return (r.get("overlaps") or 0) + (r.get("unplaced") or 0)


def cmd_compare(base_path, mine_path):
    base = {r["flow"]: r for r in json.load(open(base_path))}
    mine = {r["flow"]: r for r in json.load(open(mine_path))}
    flows = list(base) + [f for f in mine if f not in base]
    hdr = f"{'flow':<48} {'base':>14} {'branch':>14}  delta"
    print(hdr)
    print("-" * len(hdr))
    n_better = n_worse = n_same = 0
    for f in flows:
        b, m = base.get(f), mine.get(f)
        if b is None:
            print(f"{f.replace('flow/', ''):<48} {'(new)':>14} {_fmt(m):>14}")
            continue
        if m is None:
            print(f"{f.replace('flow/', ''):<48} {_fmt(b):>14} {'(gone)':>14}")
            continue
        bf, mf = _fmt(b), _fmt(m)
        if bf == mf:
            n_same += 1
            continue                            # unchanged rows are noise; skip
        tag = "BETTER" if _score(m) < _score(b) else \
              "WORSE" if _score(m) > _score(b) else "changed"
        if tag == "BETTER":
            n_better += 1
        elif tag == "WORSE":
            n_worse += 1
        print(f"{f.replace('flow/', ''):<48} {bf:>14} {mf:>14}  {tag}")
    print(f"\n{n_better} better, {n_worse} worse, {n_same} unchanged "
          f"(of {len(flows)} flows).  Metric = overlaps/unplaced.")
    return n_worse


def main():
    ap = argparse.ArgumentParser(
        description="Run the QoR corpus and/or compare two runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See the module docstring for the baseline-vs-branch recipe.")
    ap.add_argument("--flows", nargs="+", metavar="FLOW",
                    help="run these flows instead of the default corpus")
    ap.add_argument("--out", metavar="PATH",
                    help="write the run's results as JSON to PATH")
    ap.add_argument("--compare", nargs=2, metavar=("BASE", "BRANCH"),
                    help="diff two result JSONs (from earlier --out runs); "
                         "exits non-zero if any flow regressed")
    args = ap.parse_args()

    if args.compare:
        sys.exit(1 if cmd_compare(*args.compare) else 0)

    cmd_run(args.flows or CORPUS, args.out)


if __name__ == "__main__":
    main()
