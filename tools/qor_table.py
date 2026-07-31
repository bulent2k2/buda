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
"""Corpus SNAPSHOT table — run every corpus flow through the full pipeline and
dump the two-table QoR + wirelength snapshot that lives in `qor_table.md`.

The companion to `qor_corpus.py`: that tool A/B-COMPARES two builds and gates on
regressions; THIS tool produces a single-build point-in-time SNAPSHOT.  Per flow
it reports the sizes (bundle / bus-segment / net-segment counts), the wirelength
(abstract after NUTS + detailed after DNUTS, placed-only — matching
`report_wirelength`), the `(overlaps, unplaced, viol_bundles)` metric, and the
wall-clock, split into:

  * a DIRTY table — any flow with a residual overlap / unplaced bit / violation,
    or one that stops before DNUTS (the interesting-to-watch vehicles), and
  * a CLEAN table — the 0/0/0 flows, listed for their sizes/WL only.

Reuses `qor_corpus`'s CORPUS list, `_wirelengths`, `_check_design_bundles`, and
its parallel `sweep` so the snapshot always tracks the same corpus the
comparison tool sweeps.  The sweep is PARALLEL by default (`--jobs`, default =
CPU count): flows are independent, so every column is byte-identical to a
serial run EXCEPT `sec`, which inflates with CPU contention under parallel
load — pass `-j 1` when the per-flow timing itself is what you are measuring.

  tools/qor_table.py                          # print both tables to stdout
  tools/qor_table.py --out qor_table.md  # (re)write the checked-in snapshot
  tools/qor_table.py --flows flow/rnr/mix.buda flow/big_data_test/b44.buda
  tools/qor_table.py -j 1                     # serial (timing-faithful sec)
"""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import qor_corpus as qc  # noqa: E402 — after sys.path setup (reuse corpus/audit/WL)

# Curated one-line annotations for the DIRTY-table vehicles (why they carry a
# residual).  Purely cosmetic — a flow with no entry just gets a blank note.
_NOTES = {
    "flow/rnr/mix2_fast": "healer-skipping",
    "flow/rnr/mix2_fast_on_aligned_sql": "healer-skipping",
    "flow/rnr/mix2_fast_bottomup": "healer-skipping",
    "flow/rnr/mix2_fast_topdown": "healer-skipping",
    "flow/rnr/mix2_repro": "repro (stops pre-DNUTS)",
    "flow/rnr/mix2": "full healed, known residual",
    "flow/rnr/slowdown_rnr": "known hard/slow",
    "flow/hbundles/06_multipin_stress": "stress vehicle",
    "flow/big_data_test/big2/big2_noviz": "over-congested big2",
    "flow/big_data_test/big2/tc3b_flat": "over-congested big2",
    "flow/big_data_test/big2/big2": "1 electrically-broken bundle",
}


def run_flow(flow):
    """Source one flow end-to-end and capture a snapshot row: sizes (bundle /
    bus-seg / net-seg counts), wirelength (abstract/detailed, placed-only), the
    QoR metric, and wall-clock.  `None` for a field whose stage did not run
    (e.g. a flow that stops before DNUTS), mirroring `qor_corpus.run_flow`.
    Returns an `err` row instead if the flow raised."""
    import buda
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    tmp_logs = qc.private_log_dir(s)                # parallel worker: isolate logs
    d = os.path.dirname(flow)
    cwd = os.getcwd()
    t0 = time.time()
    try:
        # redirect_stdout catches Python prints; buda.ostream_redirect() routes
        # the C++ engine's std::cout/cerr into the same sink so the flow's own
        # logging can't pollute the table on stdout.
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), \
                buda.ostream_redirect():
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
        return {"flow": flow, "err": f"{type(e).__name__}: {str(e)[:60]}"}
    finally:
        if tmp_logs:
            shutil.rmtree(tmp_logs, ignore_errors=True)
    dt = time.time() - t0
    nr = getattr(s, "nuts_result", None)
    dr = getattr(s, "detailed_result", None)
    awl, dwl = qc._wirelengths(s)                   # placed-only, matches report_wirelength
    return {
        "flow": flow,
        "bund": len(s.bundles),
        "busS": (len(nr.segments) if nr is not None else None),
        "netS": (len(dr.net_segments) if dr is not None else None),
        "busWL": awl,
        "netWL": dwl,
        "ovl": (nr.num_overlaps if nr is not None else None),
        "unpl": (dr.num_unplaced if dr is not None else None),
        "viol": qc._check_design_bundles(s),
        "sec": round(dt, 1),
    }


def _is_clean(r):
    """A flow is CLEAN iff it fully completed with zero overlaps, zero unplaced
    bits, and zero violated bundles.  Any residual — or a `None` from a stage
    that never ran — puts it in the DIRTY table."""
    return r.get("ovl") == 0 and r.get("unpl") == 0 and r.get("viol") == 0


def _display(flow):
    """Corpus-relative flow name for the table: drop the `flow/` root and the
    `.buda` suffix, and shorten an over-long path by dropping `big_data_test/`."""
    n = flow.replace("flow/", "").removesuffix(".buda")
    if len(n) > 44:
        n = n.replace("big_data_test/", "")
    return n


def _c(v):
    return "null" if v is None else str(v)


def _dirty_table(rows):
    out = ["```"]
    out.append(f"{'flow':<46}{'bund':>4} {'busS':>5} {'netS':>6} {'busWL':>8} "
               f"{'netWL':>10} | {'ovl':>4} {'unpl':>4} {'viol':>4} {'sec':>6}   note")
    out.append("-" * 132)
    for r in rows:
        if "err" in r:
            out.append(f"{_display(r['flow']):<46}ERR: {r['err']}")
            continue
        out.append(
            f"{_display(r['flow']):<46}{r['bund']:>4} {_c(r['busS']):>5} "
            f"{_c(r['netS']):>6} {_c(r['busWL']):>8} {_c(r['netWL']):>10} | "
            f"{_c(r['ovl']):>4} {_c(r['unpl']):>4} {_c(r['viol']):>4} "
            f"{r['sec']:>6.1f}   {_NOTES.get(r['flow'], '')}".rstrip())
    out.append("```")
    return "\n".join(out)


def _clean_table(rows):
    out = ["```"]
    out.append(f"{'flow':<46}{'bund':>4} {'busS':>5} {'netS':>6} {'busWL':>8} "
               f"{'netWL':>10} {'sec':>7}")
    out.append("-" * 90)
    for r in rows:
        out.append(
            f"{_display(r['flow']):<46}{r['bund']:>4} {_c(r['busS']):>5} "
            f"{_c(r['netS']):>6} {_c(r['busWL']):>8} {_c(r['netWL']):>10} "
            f"{r['sec']:>7.1f}")
    out.append("```")
    return "\n".join(out)


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "-C", _ROOT, "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:                               # noqa: BLE001
        return "unknown"


def render(rows, stamp):
    """The full markdown snapshot: a header (date + commit), the DIRTY table,
    then the CLEAN table.  `netWL`/`netS` show `null` for a flow that stopped
    before DNUTS.  Sorted within each table by net-segment count, descending."""
    def key(r):
        n = r.get("netS")
        return -(n if isinstance(n, int) else -1)
    dirty = sorted([r for r in rows if "err" in r or not _is_clean(r)], key=key)
    clean = sorted([r for r in rows if "err" not in r and _is_clean(r)], key=key)
    n_ok = len(clean)
    parts = [
        f"# QoR corpus snapshot — {stamp}",
        "",
        "Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: "
        "`bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; "
        "`busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) "
        "wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits "
        "/ bundles with `check_design` violations; `sec` = wall-clock.  `null` = "
        "that stage did not run.",
        "",
        f"{n_ok} clean · {len(dirty)} with residuals · {len(rows)} flows.",
        "",
        "## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)",
        "",
        _dirty_table(dirty),
        "",
        "## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles",
        "",
        _clean_table(clean),
        "",
    ]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(
        description="Run the corpus and dump the two-table QoR + wirelength "
                    "snapshot (qor_table.md).")
    ap.add_argument("--out", help="write the markdown snapshot to this path "
                                  "(default: print to stdout)")
    ap.add_argument("--flows", nargs="+",
                    help="run these flows instead of the default corpus")
    ap.add_argument("--stamp", help="header stamp (default: today's date + "
                                    "the short git commit)")
    ap.add_argument("-j", "--jobs", type=int, default=qc.default_jobs(),
                    metavar="N",
                    help="worker processes for the sweep (default: CPU count "
                         "= %(default)s; 1 = serial, timing-faithful sec)")
    args = ap.parse_args()

    os.chdir(_ROOT)                                 # flow paths are repo-root-relative
    flows = args.flows or qc.CORPUS
    t0 = time.time()
    rows = qc.sweep(
        run_flow, flows, args.jobs,
        progress=lambda r: print(f"  done {r['flow']}", file=sys.stderr,
                                 flush=True))
    print(f"  swept {len(rows)} flows in {time.time() - t0:.1f}s "
          f"(jobs={max(1, args.jobs)})", file=sys.stderr, flush=True)
    stamp = args.stamp or f"{time.strftime('%Y-%m-%d')} (main @ {_git_commit()})"
    md = render(rows, stamp)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(md if md.endswith("\n") else md + "\n")
        print(f"\nwrote snapshot -> {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
