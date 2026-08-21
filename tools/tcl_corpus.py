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
"""Sweep the QoR corpus through a REAL tclsh and the command server.

The same 41 flows `tools/qor_corpus.py` runs through the CLI, run instead as
the translated Tcl in `flow/tcl/corpus/` (see `tools/buda2tcl.py`) — one
`tclsh` per flow, each driving the engine over the pipe protocol, ~13.5k
commands across the sweep.

The output is written in `qor_corpus.py --out`'s own schema, so the diff is
that tool's `--compare` rather than a second comparison to keep in step:

    tools/qor_corpus.py --out cli.json          # the CLI side
    tools/tcl_corpus.py --out tcl.json          # the Tcl side
    tools/qor_corpus.py --compare cli.json tcl.json

An identical result is the claim being tested: the front end is a DRIVER, not
a second implementation, so the routed design must not depend on which one
issued the commands.

`--nightly qor/qor_table_rows.json` converts the checked-in nightly snapshot
into the same schema, so a sweep can also be diffed against the last recorded
`main` result without hand-mapping column names.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CORPUS_TCL = os.path.join(_ROOT, "flow", "tcl", "corpus")

_QOR_RE = re.compile(r"^QOR (\{.*\})\s*$", re.M)


def tcl_path(flow):
    """flow/rnr/mix.buda -> flow/tcl/corpus/rnr/mix.tcl"""
    rel = os.path.relpath(flow, "flow")
    return os.path.join(_CORPUS_TCL, os.path.splitext(rel)[0] + ".tcl")


def run_one(flow, timeout=1800):
    """Run one translated flow under tclsh; return its QoR row."""
    script = tcl_path(flow)
    if not os.path.exists(script):
        return {"flow": flow, "err": f"not translated: {script}"}
    env = dict(os.environ)
    # Same reasoning as qor_corpus's worker init: this sweep already runs one
    # flow per core, so the engine's own stall-sweep/planner pools would
    # multiply that into jobs x cores threads.  Decision-identical either way.
    env.setdefault("BUDA_SWEEP_THREADS", "1")
    env.setdefault("BUDA_PLAN_THREADS", "1")
    env.setdefault("MPLBACKEND", "Agg")
    t0 = time.time()
    try:
        p = subprocess.run(["tclsh", script], capture_output=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=_ROOT, env=env)
    except subprocess.TimeoutExpired:
        return {"flow": flow, "err": f"timeout after {timeout}s"}
    m = _QOR_RE.search(p.stdout or "")
    if not m:
        # No QOR line: the flow died before the epilogue.  Report the tail of
        # what it said — a sweep that silently recorded "no data" would make a
        # broken bridge look like a missing measurement.
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
        return {"flow": flow, "err": f"rc={p.returncode}: " + " | ".join(tail)[:200]}
    row = json.loads(m.group(1))
    # The bridge's `buda::query` spells "never computed" as -1 (deliberately
    # not 0); the qor schema spells a stage that did not run as None.
    # Translate at the boundary — without this a DNUTS-less flow's -1 ranks
    # BETTER than the Python sweep's None for the identical run (measured on
    # flow/ariane133/ariane133.buda while it was a corpus candidate).
    for k in ("overlaps", "unplaced", "viol_bundles"):
        if row.get(k) == -1:
            row[k] = None
    row["sec"] = round(time.time() - t0, 1)
    return row


def sweep(flows, jobs):
    """Run the flows, heaviest first, and return rows in INPUT order."""
    order = {f: i for i, f in enumerate(flows)}
    rows = {}
    heavy = sorted(flows, key=lambda f: -os.path.getsize(
        os.path.join(_ROOT, f)) if os.path.exists(os.path.join(_ROOT, f)) else 0)
    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(run_one, f): f for f in heavy}
        for fut in cf.as_completed(futs):
            r = fut.result()
            rows[futs[fut]] = r
            print(json.dumps(r), flush=True)
    return [rows[f] for f in sorted(rows, key=lambda f: order[f])]


def from_nightly(path):
    """The checked-in nightly snapshot, in the sweep schema.

    `qor/qor_table_rows.json` is the same measurement under the table's own
    column names; mapping it here means a sweep can be diffed against the last
    recorded `main` result with the ordinary --compare, instead of by eye.
    """
    out = []
    for r in json.load(open(path)):
        out.append({"flow": r["flow"], "overlaps": r.get("ovl"),
                    "unplaced": r.get("unpl"), "viol_bundles": r.get("viol"),
                    "abstract_wl": r.get("busWL"), "detailed_wl": r.get("netWL"),
                    "sec": r.get("sec")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the sweep as JSON")
    ap.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--flows", nargs="*", help="subset (default: whole corpus)")
    ap.add_argument("--nightly", metavar="ROWS_JSON",
                    help="convert a nightly snapshot to the sweep schema "
                         "(with --out) instead of running anything")
    a = ap.parse_args(argv)

    if a.nightly:
        rows = from_nightly(a.nightly)
        if a.out:
            json.dump(rows, open(a.out, "w"), indent=1)
            print(f"converted {len(rows)} nightly rows -> {a.out}")
        return 0

    if not shutil.which("tclsh"):
        print("no tclsh on this host", file=sys.stderr)
        return 2
    flows = a.flows
    if not flows:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "qc", os.path.join(_HERE, "qor_corpus.py"))
        qc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qc)
        flows = list(qc.CORPUS)

    t0 = time.time()
    rows = sweep(flows, a.jobs)
    print(f"\nswept {len(rows)} flows through tclsh in {time.time() - t0:.1f}s "
          f"(jobs={a.jobs})")
    errs = [r for r in rows if "err" in r]
    if errs:
        print(f"{len(errs)} flow(s) did not report:")
        for r in errs:
            print(f"   {r['flow']}: {r['err']}")
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)
        print(f"wrote {len(rows)} results -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
