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
`(num_overlaps, num_unplaced, viol_bundles)` — the overlap/unplaced counts plus
the number of bundles with `check_design` violations (a route can be overlap-
free yet electrically broken).  The value for a branch is the DIFF against a
baseline: build `main`, capture a baseline; build the branch, capture again;
`--compare` the two.

Usage:
  # capture this build's QoR over the default corpus
  PYTHONPATH=build:src:tools tools/qor_corpus.py --out mine.json

  # baseline-vs-branch recipe (the point of this tool)
  git checkout main   && bin/bb && tools/qor_corpus.py --out base.json
  git checkout branch && bin/bb && tools/qor_corpus.py --out mine.json
  tools/qor_corpus.py --compare base.json mine.json

`--compare` tags each moved flow BETTER/WORSE on the QoR metric
(overlaps/unplaced/viol_bundles) and exits non-zero if any regressed, then
prints two informational diffs that are reported but never gate: a
**wirelength** diff (total abstract WL after NUTS + detailed WL after DNUTS,
base->branch, plus the largest per-flow movers — a topology/planner change
legitimately MOVES wirelength, so it is shown but not a pass/fail signal) and a
**runtime** diff (total corpus wall-clock + the largest per-flow movers —
single-run and noisy).

  # just a subset (e.g. while iterating on one flow)
  tools/qor_corpus.py --flows flow/rnr/mix.buda flow/rnr/slowdown_rnr.buda

Notes:
  * A flow that raises is recorded with an "err" field, not skipped silently.
  * `overlaps`/`unplaced`/`viol_bundles` are None when the flow never reached
    that stage (e.g. no run_detailed_nuts) — reported as such, never coerced
    to 0.
  * `viol_bundles` re-runs `check_design` at the deepest completed stage and
    parses its own summary ('... across N bundle(s)'), so it matches the CLI.
  * The corpus below is the subset of flow/ that runs the full pipeline
    through run_detailed_nuts.  Edit CORPUS to add/remove vehicles.
"""
import argparse
import contextlib
import io
import json
import os
import re
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
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/bigHalf_bus038_bitrunk.buda",
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
    "flow/rnr/mix2_fast_bottomup.buda",
    "flow/rnr/mix2_fast_on_aligned_sql.buda",
    "flow/rnr/mix2_fast_topdown.buda",
    "flow/rnr/mix2_topdown_refine.buda",
    "flow/chip/chip_topdown.buda",
    "flow/chip/chip_bottomup.buda",
    "flow/chip/chip3_topdown.buda",
    "flow/chip/chip3a_bottomup.buda",
]


def _seg_wl(seg):
    """Routing-direction length of a placed segment (its span extent)."""
    return abs(seg.span_hi - seg.span_lo)


def _wirelengths(s):
    """(abstract WL after NUTS, detailed WL after DNUTS) for a solved session —
    each the sum of PLACED-segment span lengths (the metric `report_wirelength`
    prints, computed straight off the placed structs).  A `placed=False`
    TrackSegment carries no wire and is EXCLUDED, matching the canonical
    `_wirelength_by_bundle` (nutsflow.py) — otherwise an incomplete route would
    report an inflated, non-comparable abstract WL.  `None` when that stage did
    not run, mirroring the None-means-stage-absent convention the other metrics
    use, so a build that stops before NUTS/DNUTS is distinguishable from a
    zero-length route."""
    nr = getattr(s, "nuts_result", None)
    dr = getattr(s, "detailed_result", None)
    awl = (round(sum(_seg_wl(x) for x in nr.segments if getattr(x, "placed", True)))
           if nr is not None else None)
    dwl = (round(sum(_seg_wl(x) for x in dr.net_segments if getattr(x, "placed", True)))
           if dr is not None else None)
    return awl, dwl


def run_flow(flow):
    """Source one flow end-to-end and capture its final QoR.  Returns a dict
    with overlaps/unplaced/viol_bundles/abstract_wl/detailed_wl/sec, or an err
    field if the flow raised."""
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
    except SystemExit as e:
        # An intentional `exit` / `exit 0` ends a flow normally — fall through
        # and capture its metrics.  A NONZERO code is a CLI fail-fast (unknown
        # command, missing nested `source`, ...): a real abort that must surface
        # as an err row, not masquerade as a completed None/None measurement.
        if e.code not in (0, None):
            return {"flow": flow, "err": f"SystemExit({e.code})"}
    except Exception as e:                      # noqa: BLE001 — record, don't crash the sweep
        return {"flow": flow, "err": f"{type(e).__name__}: {str(e)[:80]}"}
    dt = time.time() - t0
    ov = getattr(getattr(s, "nuts_result", None), "num_overlaps", None)
    un = getattr(getattr(s, "detailed_result", None), "num_unplaced", None)
    vb = _check_design_bundles(s)
    awl, dwl = _wirelengths(s)
    return {"flow": flow, "overlaps": ov, "unplaced": un,
            "viol_bundles": vb, "abstract_wl": awl, "detailed_wl": dwl,
            "sec": round(dt, 1)}


def _check_design_bundles(s):
    """Run `check_design` at the deepest completed stage and return the number
    of BUNDLES with design violations (0 = clean).  Parses check_design's own
    summary line ('... across N bundle(s)') so the count matches exactly what
    the CLI reports.  None if the audit could not run (e.g. no routed stage)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            s.do_command("check_design")           # bare = deepest stage, read-only
    except SystemExit:
        pass
    except Exception:                              # noqa: BLE001
        return None
    out = buf.getvalue()
    if "no violations found" in out:
        return 0
    m = re.search(r"across (\d+) bundle\(s\)", out)
    return int(m.group(1)) if m else None


_METRICS = ("overlaps", "unplaced", "viol_bundles")


def _fmt(r, keys=_METRICS):
    if "err" in r:
        return r["err"]
    return "/".join(str(r.get(k)) for k in keys)


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


def _rank(r, keys):
    """Sort key for regression detection (higher = worse), over the metric
    KEYS given: (n_missing_metrics, sum of the metrics).

    A None VALUE means a pipeline STAGE DID NOT RUN on this build.  It ranks
    worse than any real count, so a branch that stops reaching NUTS/DNUTS where
    the baseline produced a number is flagged WORSE — not a clean 'changed' that
    slips past the guard (Codex P2).  A flow that is None on BOTH builds (never
    runs DNUTS by design) is caught earlier by the string-equality skip, so its
    missing metric is never mistaken for a regression.  viol_bundles (bundles
    with check_design violations) counts toward worseness too — a route can be
    overlap-free yet electrically broken.

    `keys` is the set of metrics present in BOTH compared files.  A metric a file
    never MEASURED (an older two-column baseline has no viol_bundles KEY at all)
    is excluded entirely — otherwise its absence would read as a None VALUE and
    rank that baseline worse than any three-column branch, letting a real
    electrical regression (0/0 vs 0/0/1) masquerade as BETTER (Codex P2).  err
    rows rank worst."""
    if "err" in r:
        return (len(keys) + 1, 0)
    vals = [r.get(k) for k in keys]
    n_missing = sum(v is None for v in vals)
    return (n_missing, sum((v or 0) for v in vals))


def _runtime_report(paired):
    """Informational runtime diff (single-run and noisy, so NOT part of the
    pass/fail guard): total corpus wall-clock plus the largest per-flow movers."""
    timed = [(f, b, m) for f, b, m in paired
             if isinstance(b.get("sec"), (int, float))
             and isinstance(m.get("sec"), (int, float))]
    if not timed:
        return
    tb = sum(b["sec"] for _, b, _ in timed)
    tm = sum(m["sec"] for _, _, m in timed)
    d = tm - tb
    pct = (100 * d / tb) if tb else 0.0
    print("\nruntime (informational — single-run, noisy; not a guard):")
    print(f"  total {tb:.1f}s -> {tm:.1f}s  ({d:+.1f}s, {pct:+.1f}%)")
    movers = sorted(timed, key=lambda x: abs(x[2]["sec"] - x[1]["sec"]),
                    reverse=True)
    for f, b, m in movers:
        ds = m["sec"] - b["sec"]
        if abs(ds) < 1.0:                       # sub-second deltas are noise
            break
        print(f"  {f.replace('flow/', ''):<46} "
              f"{b['sec']:>6.1f}s -> {m['sec']:>6.1f}s  {ds:+.1f}s")


def _wirelength_report(paired):
    """Informational wirelength diff (a topology/planner change legitimately
    MOVES wirelength, so it is reported but is NOT part of the pass/fail guard,
    like runtime): total abstract WL (after NUTS) + detailed WL (after DNUTS)
    base->branch, plus the largest per-flow abstract-WL movers.

    Only flows whose ROUTE COMPLETENESS is unchanged — same (overlaps, unplaced)
    on both builds — are summed into the totals.  A completeness change (e.g. the
    branch drops MORE bit-wires, so its detailed_wl covers fewer net_segments)
    would otherwise read as a phantom WL 'improvement' (Codex #464 P2); such
    flows are excluded from the totals but still listed among the movers, flagged
    with their (overlaps, unplaced) change so the heal/regress stays visible.
    Silently skips a pre-WL baseline / any flow where a side lacks the value."""
    pmap = {f: (b, m) for f, b, m in paired}

    def _num(r, k):
        v = r.get(k)
        return v if isinstance(v, (int, float)) else None

    def _same_completeness(b, m):
        return (_num(b, "overlaps") == _num(m, "overlaps")
                and _num(b, "unplaced") == _num(m, "unplaced"))

    def _rows(key, comparable_only):
        out = []
        for f, (b, m) in pmap.items():
            bv, mv = _num(b, key), _num(m, key)
            if bv is None or mv is None:
                continue
            if comparable_only and not _same_completeness(b, m):
                continue
            out.append((f, bv, mv))
        return out

    aw_all = _rows("abstract_wl", False)
    if not aw_all and not _rows("detailed_wl", False):
        return
    n_excl = sum(1 for _, (b, m) in pmap.items()
                 if _num(b, "abstract_wl") is not None
                 and _num(m, "abstract_wl") is not None
                 and not _same_completeness(b, m))

    print("\nwirelength (informational — topology changes move it; not a guard):")
    for label, key in (("abstract WL (after NUTS)", "abstract_wl"),
                       ("detailed WL (after DNUTS)", "detailed_wl")):
        rows = _rows(key, True)
        if not rows:
            continue
        tb = sum(bv for _, bv, _ in rows)
        tm = sum(mv for _, _, mv in rows)
        d = tm - tb
        pct = (100 * d / tb) if tb else 0.0
        print(f"  {label:<26} {tb:>15,.0f} -> {tm:>15,.0f}  ({d:+,.0f}, {pct:+.2f}%)"
              f"  [{len(rows)} comparable flows]")
    if n_excl:
        print(f"  ({n_excl} flow(s) excluded from the totals — route completeness "
              f"changed, WL not comparable; flagged below)")

    # Per-flow abstract-WL movers: filter by |Δ%| FIRST, then sort desc, then cap
    # (a large flow's sub-0.1% move must not truncate a smaller flow's ≥0.1% one).
    movers = [(f, bv, mv, mv - bv, (100 * (mv - bv) / bv) if bv else 0.0)
              for f, bv, mv in aw_all]
    movers = sorted((r for r in movers if abs(r[4]) >= 0.1),
                    key=lambda x: abs(x[4]), reverse=True)
    if movers:
        print("  abstract-WL movers (|Δ| ≥ 0.1%):")
    for f, bv, mv, d, pct in movers[:12]:
        b, m = pmap[f]
        flag = ("" if _same_completeness(b, m) else
                f"  [!] ov {_num(b,'overlaps')}->{_num(m,'overlaps')} "
                f"unpl {_num(b,'unplaced')}->{_num(m,'unplaced')}")
        print(f"    {f.replace('flow/', ''):<44} "
              f"{bv:>12,.0f} -> {mv:>12,.0f}  {d:+,.0f} ({pct:+.2f}%){flag}")


def _present_metrics(rows):
    """The metrics a result file actually MEASURED — a key present on any of its
    non-err rows.  An older two-column baseline has no 'viol_bundles' key."""
    return [k for k in _METRICS if any(k in r for r in rows if "err" not in r)]


def cmd_compare(base_path, mine_path):
    base_rows = json.load(open(base_path))
    mine_rows = json.load(open(mine_path))
    base = {r["flow"]: r for r in base_rows}
    mine = {r["flow"]: r for r in mine_rows}
    # Rank/format over only the metrics present in BOTH files.  A metric one
    # side never measured (e.g. viol_bundles in a pre-#432 baseline) is dropped
    # from the diff, with a loud note — otherwise its missing KEY would read as
    # a None VALUE and rank that baseline worse than any branch, so a real
    # electrical regression (0/0 vs 0/0/1) would masquerade as BETTER and pass
    # the guard (Codex P2).
    bkeys, mkeys = _present_metrics(base_rows), _present_metrics(mine_rows)
    keys = [k for k in _METRICS if k in bkeys and k in mkeys]
    dropped = [k for k in _METRICS if (k in bkeys) != (k in mkeys)]
    if dropped:
        print(f"NOTE: {', '.join(dropped)} present in only one input — excluded "
              f"from the diff.  Re-run this tool on BOTH builds to compare it.\n")

    flows = list(base) + [f for f in mine if f not in base]
    hdr = f"{'flow':<48} {'base':>14} {'branch':>14}  delta"
    print(hdr)
    print("-" * len(hdr))
    n_better = n_worse = n_same = 0
    for f in flows:
        b, m = base.get(f), mine.get(f)
        if b is None:
            print(f"{f.replace('flow/', ''):<48} {'(new)':>14} {_fmt(m, keys):>14}")
            continue
        if m is None:
            print(f"{f.replace('flow/', ''):<48} {_fmt(b, keys):>14} {'(gone)':>14}")
            continue
        bf, mf = _fmt(b, keys), _fmt(m, keys)
        if bf == mf:
            n_same += 1
            continue                            # unchanged rows are noise; skip
        tag = "BETTER" if _rank(m, keys) < _rank(b, keys) else \
              "WORSE" if _rank(m, keys) > _rank(b, keys) else "changed"
        if tag == "BETTER":
            n_better += 1
        elif tag == "WORSE":
            n_worse += 1
        print(f"{f.replace('flow/', ''):<48} {bf:>14} {mf:>14}  {tag}")
    print(f"\n{n_better} better, {n_worse} worse, {n_same} unchanged "
          f"(of {len(flows)} flows).  Metric = {'/'.join(keys) or '(none)'}.")
    paired = [(f, base[f], mine[f]) for f in flows if f in base and f in mine]
    _wirelength_report(paired)
    _runtime_report(paired)
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
                    help="diff two result JSONs (from earlier --out runs) on "
                         "QoR + runtime; exits non-zero if any flow regressed")
    args = ap.parse_args()

    if args.compare:
        sys.exit(1 if cmd_compare(*args.compare) else 0)

    cmd_run(args.flows or CORPUS, args.out)


if __name__ == "__main__":
    main()
