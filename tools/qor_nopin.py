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
"""Pin-FREE QoR corpus sweep — the companion to `qor_corpus.py` for changes that
add or drop TOPOLOGY CANDIDATES.

`select_topology <bundle> <index>` pins by the candidate's 1-based POSITION in
the bundle's pool.  A topology/generation change that unlocks a previously
filtered candidate (or drops one) RENUMBERS that pool, so a flow's hard index
pin then selects a *different* topology on the two builds — a phantom
BETTER/WORSE that has nothing to do with the change under test.  This exact trap
bit the big2 b25 sweep: `big2_b4_b24` and `b24_bus_056` showed large regressions
that were pure pin artifacts (caught by @codex on PR #448); with the pins
neutralized on both builds they were byte-identical and only the genuinely-moved
circuit remained.

This script sweeps the same corpus as `qor_corpus.py` but NEUTRALIZES every
`select_topology` / `select_topologies` command (drops it), so each build plans
from its own full candidate pool and the diff reflects the PLANNER'S CHOICE, not
stale index bookkeeping.  Use it whenever a branch changes candidate generation;
use plain `qor_corpus.py` when the pins are meaningful (the flow is pinned on
purpose to hold a topology fixed).

Everything else — the corpus list, the `(overlaps, unplaced, viol_bundles)`
metric, `check_design`'s bundle-violation count, the informational abstract/
detailed **wirelength** capture (`qc._wirelengths`), and the `--compare`
diff/guard — is REUSED verbatim from `qor_corpus.py`, so a `--compare` here reads
and reports identically (and the two tools' JSONs are the same shape; just don't
cross pinned-vs-neutralized files).

Usage (mirrors qor_corpus.py — capture on each build, then compare):
  git checkout main   && bin/bb && tools/qor_nopin.py --out base.json
  git checkout branch && bin/bb && tools/qor_nopin.py --out mine.json
  tools/qor_nopin.py --compare base.json mine.json
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
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import qor_corpus as qc  # noqa: E402 — after sys.path setup (reuse its corpus/audit/compare)

_PIN_CMDS = ("select_topology", "select_topologies")


def _is_pin_cmd(cmd_line):
    """True iff CMD_LINE is a topology-pin command, using the SAME normalization
    the CLI applies before dispatch (`buda_cli.do_command`: strip, split on
    arbitrary whitespace, lowercase the verb).  Matching that exactly is what
    lets the neutralizer catch an uppercase `SELECT_TOPOLOGY`, a tab- or
    multi-space-delimited pin, or a trailing-comment pin — every form the CLI
    itself would execute — instead of only the bare `select_topology <sp>` case."""
    parts = cmd_line.strip().split()
    return bool(parts) and parts[0].lower() in _PIN_CMDS


def run_flow_nopin(flow):
    """Source one flow end-to-end with every `select_topology[ies]` neutralized,
    and capture its final QoR.  Same return shape as `qor_corpus.run_flow` (an
    `overlaps/unplaced/viol_bundles/sec` dict, or an `err` row if it raised)."""
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True

    # Drop pin commands so the planner chooses from the full pool on THIS build.
    _orig = s.do_command

    def _patched(cmd):
        if _is_pin_cmd(cmd):
            return None
        return _orig(cmd)

    s.do_command = _patched

    d = os.path.dirname(flow)
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cwd = os.getcwd()
            try:
                if d:                               # flows source paths relative to their dir
                    os.chdir(d)
                s.do_command(f"source {os.path.basename(flow)}")
            finally:
                os.chdir(cwd)
    except SystemExit as e:
        if e.code not in (0, None):
            return {"flow": flow, "err": f"SystemExit({e.code})"}
    except Exception as e:                          # noqa: BLE001 — record, don't crash the sweep
        return {"flow": flow, "err": f"{type(e).__name__}: {str(e)[:80]}"}
    dt = time.time() - t0
    ov = getattr(getattr(s, "nuts_result", None), "num_overlaps", None)
    un = getattr(getattr(s, "detailed_result", None), "num_unplaced", None)
    vb = qc._check_design_bundles(s)
    awl, dwl = qc._wirelengths(s)                    # same WL capture as qor_corpus
    return {"flow": flow, "overlaps": ov, "unplaced": un,
            "viol_bundles": vb, "abstract_wl": awl, "detailed_wl": dwl,
            "sec": round(dt, 1)}


def cmd_run(flows, out, jobs=1):
    os.chdir(_ROOT)                                 # flow paths are repo-root-relative
    t0 = time.time()
    results = qc.sweep(run_flow_nopin, flows, jobs,
                       progress=lambda r: print(json.dumps(r), flush=True))
    print(f"\nswept {len(results)} flows in {time.time() - t0:.1f}s "
          f"(jobs={max(1, jobs)})")
    if out:
        with open(out, "w") as fh:
            json.dump(results, fh, indent=1)
        print(f"wrote {len(results)} results (pins neutralized) -> {out}")
    return results


def main():
    ap = argparse.ArgumentParser(
        description="Pin-free QoR corpus sweep (neutralizes select_topology) "
                    "and/or compare two runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See the module docstring for when to use this vs qor_corpus.py.")
    ap.add_argument("--flows", nargs="+", metavar="FLOW",
                    help="run these flows instead of the default corpus")
    ap.add_argument("--out", metavar="PATH",
                    help="write the run's results as JSON to PATH")
    ap.add_argument("--compare", nargs=2, metavar=("BASE", "BRANCH"),
                    help="diff two result JSONs (from earlier --out runs) on "
                         "QoR + runtime; exits non-zero if any flow regressed")
    ap.add_argument("-j", "--jobs", type=int, default=qc.default_jobs(),
                    metavar="N",
                    help="worker processes for the sweep (default: CPU count "
                         "= %(default)s; 1 = serial, timing-faithful sec)")
    args = ap.parse_args()

    if args.compare:
        sys.exit(1 if qc.cmd_compare(*args.compare) else 0)   # identical diff/guard

    cmd_run(args.flows or qc.CORPUS, args.out, jobs=args.jobs)


if __name__ == "__main__":
    main()
