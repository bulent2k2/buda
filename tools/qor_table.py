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
The `sec1` column keeps the last known SERIAL timings comparable across
parallel refreshes: a full-corpus `-j 1 --out` run re-stamps the checked-in
sidecar `qor_serial_times.json` (date + commit), and every later render shows
those per-flow times beside the current run's `sec` ('-' = flow added since).

  tools/qor_table.py                          # print both tables to stdout
  tools/qor_table.py --out qor_table.md  # (re)write the checked-in snapshot
  tools/qor_table.py --flows flow/rnr/mix.buda flow/big_data_test/b44.buda
  tools/qor_table.py -j 1                     # serial (timing-faithful sec)

The nightly workflow refreshes the checked-in snapshot and opens a PR only when
the QoR actually moved.  Two flags serve that, and neither runs a flow beyond
the sweep itself:

  tools/qor_table.py --out qor_table.md --json qor_table_rows.json
  tools/qor_table.py --diff old_rows.json qor_table_rows.json   # runs NO flows

`--json` is the markdown's machine-readable twin (sorted by flow, so reordering
the CORPUS list is not a diff); `--diff` reports whether anything but the `sec`
timing changed.  Without that distinction the table differs on EVERY run — `sec`
is the run's own wall-clock — and the nightly would open a PR every night.
"""
import argparse
import contextlib
import io
import json
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


# The serial-timing sidecar: the last known FULL `-j 1` run's per-flow
# wall-clocks, checked in next to qor_table.md.  A parallel snapshot's `sec`
# column is contention-inflated, so the table keeps this stamped serial
# reference as its own `sec1` column.  Rewritten ONLY by a full-corpus
# `-j 1 --out` run (a --flows subset or a stdout run never clobbers it).
_SERIAL_PATH = os.path.join(_ROOT, "qor_serial_times.json")


def load_serial_times(path=None):
    """The sidecar as {'stamp': <date + commit>, 'times': {flow: sec}}, or
    None when absent/unreadable/incomplete.  Both fields are validated: a
    hand-edited or conflict-resolved sidecar missing its stamp must fall back
    to the documented 'none recorded yet' render, not KeyError AFTER the
    (lengthy) corpus sweep (Codex #545)."""
    try:
        with open(path or _SERIAL_PATH) as fh:
            data = json.load(fh)
        ok = (isinstance(data.get("times"), dict)
              and isinstance(data.get("stamp"), str) and data["stamp"].strip())
        return data if ok else None
    except Exception:                               # noqa: BLE001
        return None


def save_serial_times(rows, stamp, path=None):
    """Record a full serial run's per-flow `sec` (err rows excluded) with the
    run's date + commit stamp."""
    data = {"stamp": stamp,
            "times": {r["flow"]: r["sec"] for r in rows if "err" not in r}}
    with open(path or _SERIAL_PATH, "w") as fh:
        json.dump(data, fh, indent=1)
        fh.write("\n")


#: Fields that change on EVERY run without the QoR having moved.  `sec` is this
#: run's wall-clock; comparing it would make the snapshot differ every night.
VOLATILE_FIELDS = frozenset({"sec"})


def semantic_diff(before, after):
    """Compare two --json row lists ignoring VOLATILE_FIELDS.

    Returns (added, removed, moved) as sorted flow-name lists, where `moved` is
    a list of (flow, [changed_field, ...]).  This is the nightly workflow's
    open-a-PR predicate: it lives here, not in the YAML, so it can be tested.
    """
    def index(rows):
        return {r["flow"]: {k: v for k, v in r.items() if k not in VOLATILE_FIELDS}
                for r in rows}
    b, a = index(before), index(after)
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    moved = []
    for flow in sorted(set(b) & set(a)):
        fields = sorted(k for k in set(b[flow]) | set(a[flow])
                        if b[flow].get(k) != a[flow].get(k))
        if fields:
            moved.append((flow, fields))
    return added, removed, moved


def _c1(times, flow):
    """The `sec1` cell: the flow's last known serial time, '-' when unknown
    (e.g. a flow added to the corpus after the last -j 1 run)."""
    v = (times or {}).get(flow)
    return f"{v:.1f}" if isinstance(v, (int, float)) else "-"


def _dirty_table(rows, serial_times=None):
    out = ["```"]
    out.append(f"{'flow':<46}{'bund':>4} {'busS':>5} {'netS':>6} {'busWL':>8} "
               f"{'netWL':>10} | {'ovl':>4} {'unpl':>4} {'viol':>4} {'sec':>6} "
               f"{'sec1':>7}   note")
    out.append("-" * 140)
    for r in rows:
        if "err" in r:
            out.append(f"{_display(r['flow']):<46}ERR: {r['err']}")
            continue
        out.append(
            f"{_display(r['flow']):<46}{r['bund']:>4} {_c(r['busS']):>5} "
            f"{_c(r['netS']):>6} {_c(r['busWL']):>8} {_c(r['netWL']):>10} | "
            f"{_c(r['ovl']):>4} {_c(r['unpl']):>4} {_c(r['viol']):>4} "
            f"{r['sec']:>6.1f} {_c1(serial_times, r['flow']):>7}   "
            f"{_NOTES.get(r['flow'], '')}".rstrip())
    out.append("```")
    return "\n".join(out)


def _clean_table(rows, serial_times=None):
    out = ["```"]
    out.append(f"{'flow':<46}{'bund':>4} {'busS':>5} {'netS':>6} {'busWL':>8} "
               f"{'netWL':>10} {'sec':>7} {'sec1':>7}")
    out.append("-" * 98)
    for r in rows:
        out.append(
            f"{_display(r['flow']):<46}{r['bund']:>4} {_c(r['busS']):>5} "
            f"{_c(r['netS']):>6} {_c(r['busWL']):>8} {_c(r['netWL']):>10} "
            f"{r['sec']:>7.1f} {_c1(serial_times, r['flow']):>7}")
    out.append("```")
    return "\n".join(out)


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "-C", _ROOT, "rev-parse", "--short", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:                               # noqa: BLE001
        return "unknown"


def render(rows, stamp, serial=None):
    """The full markdown snapshot: a header (date + commit), the DIRTY table,
    then the CLEAN table.  `netWL`/`netS` show `null` for a flow that stopped
    before DNUTS.  Sorted within each table by net-segment count, descending.
    `serial` is the serial-timing sidecar dict (load_serial_times()); when
    present, each table carries a `sec1` column — the last known full `-j 1`
    run's per-flow wall-clock, stamped in the legend — since a parallel run's
    own `sec` is contention-inflated."""
    def key(r):
        n = r.get("netS")
        return -(n if isinstance(n, int) else -1)
    dirty = sorted([r for r in rows if "err" in r or not _is_clean(r)], key=key)
    clean = sorted([r for r in rows if "err" not in r and _is_clean(r)], key=key)
    n_ok = len(clean)
    times = (serial or {}).get("times")
    sec1_legend = (
        f"  `sec1` = last known SERIAL (-j 1) wall-clock, captured "
        f"{serial['stamp']} — refresh with a full `-j 1 --out` run "
        f"('-' = no serial timing yet)." if times else
        "  `sec1` = last known serial (-j 1) wall-clock (none recorded yet — "
        "run a full `-j 1 --out` sweep to capture it).")
    parts = [
        f"# QoR corpus snapshot — {stamp}",
        "",
        "Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: "
        "`bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; "
        "`busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) "
        "wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits "
        "/ bundles with `check_design` violations; `sec` = wall-clock of THIS "
        "run (contention-inflated when the sweep ran parallel).  `null` = "
        "that stage did not run." + sec1_legend,
        "",
        f"{n_ok} clean · {len(dirty)} with residuals · {len(rows)} flows.",
        "",
        "## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)",
        "",
        _dirty_table(dirty, times),
        "",
        "## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles",
        "",
        _clean_table(clean, times),
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
    ap.add_argument("--json", metavar="PATH",
                    help="also write the raw sweep rows as JSON.  The "
                         "machine-readable twin of the markdown: the nightly "
                         "workflow diffs it to tell a REAL QoR change from the "
                         "`sec` column's per-run timing churn, which would "
                         "otherwise make the table differ every single night.")
    ap.add_argument("-j", "--jobs", type=int, default=qc.default_jobs(),
                    metavar="N",
                    help="worker processes for the sweep (default: CPU count "
                         "= %(default)s; 1 = serial, timing-faithful sec)")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"),
                    help="compare two --json files ignoring per-run timing and "
                         "report whether the QoR actually moved; prints "
                         "'changed=true|false' and appends it to $GITHUB_OUTPUT "
                         "when set.  Runs NO flows.")
    args = ap.parse_args()

    if args.diff:
        old_p, new_p = args.diff
        try:
            with open(new_p) as fh:
                after = json.load(fh)
        except Exception as e:                      # noqa: BLE001
            sys.exit(f"--diff: cannot read {new_p}: {e}")
        try:
            with open(old_p) as fh:
                before = json.load(fh)
        except Exception:                           # noqa: BLE001
            # No committed sidecar yet (first run, or it was just added): treat
            # as changed so the snapshot gets published rather than silently
            # skipped forever.
            print(f"no readable {old_p} — treating as changed")
            before = None
        if before is None:
            changed = True
        else:
            added, removed, moved = semantic_diff(before, after)
            for f in added:
                print(f"  + {f} (new corpus flow)")
            for f in removed:
                print(f"  - {f} (left the corpus)")
            for f, fields in moved:
                b = {r["flow"]: r for r in before}[f]
                a = {r["flow"]: r for r in after}[f]
                print(f"  ~ {f}: " + ", ".join(
                    f"{k} {b.get(k)}->{a.get(k)}" for k in fields))
            changed = bool(added or removed or moved)
        print(f"changed={'true' if changed else 'false'}")
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as fh:
                fh.write(f"changed={'true' if changed else 'false'}\n")
        return

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
    if args.jobs <= 1 and not args.flows and args.out:
        # A FULL serial snapshot refresh is the (only) source of truth for the
        # serial-timing sidecar: re-stamp it from this run.  Subset (--flows)
        # and stdout-only runs never clobber it.
        save_serial_times(rows, stamp)
        print(f"  serial timings -> {_SERIAL_PATH}", file=sys.stderr, flush=True)
    if args.json:
        # Sorted by flow, not left in `sweep`'s input (= CORPUS list) order:
        # that order is already stable run-to-run, but it makes the file hostage
        # to the LIST's ordering, so merely moving a corpus entry would render
        # as a whole-file diff and a spurious "QoR changed" PR.
        with open(args.json, "w") as fh:
            json.dump(sorted(rows, key=lambda r: r["flow"]), fh, indent=1)
            fh.write("\n")
        print(f"  rows -> {args.json}", file=sys.stderr, flush=True)
    md = render(rows, stamp, load_serial_times())
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(md if md.endswith("\n") else md + "\n")
        print(f"\nwrote snapshot -> {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
