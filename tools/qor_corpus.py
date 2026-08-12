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

  # baseline-vs-branch, WITHOUT touching the working tree (preferred)
  tools/qor_corpus.py --vs main

  # the same thing by hand, when you want the two runs separated
  git checkout main   && bin/bb && tools/qor_corpus.py --out base.json
  git checkout branch && bin/bb && tools/qor_corpus.py --out mine.json
  tools/qor_corpus.py --compare base.json mine.json

`--vs REV` materializes REV in a git worktree (cached under the temp dir,
keyed by commit), builds BOTH sides, sweeps them SEQUENTIALLY and compares.
It never reads, writes or checks out your working tree, so uncommitted work
needs no stash and you may keep editing — or commit — while it runs.  The
hand recipe above cannot do that: `git checkout main` requires a clean tree,
and stashing around it makes the measurement a mutation of the tree, whose
failure modes all yield a WRONG NUMBER rather than an error (see cmd_vs).

Three things to know about `--vs`: the baseline is the COMMIT, so `--vs HEAD`
measures your last commit and uncommitted changes appear only on the branch
side; the baseline runs the BASELINE's own copy of this harness, matching the
hand recipe, so a CORPUS that differs between the two is reported as a
comparable subset rather than silently reconciled; and the RUNTIME column is
advisory here even by its usual standards — working while the sweep runs is
the point of the mode, and it makes whichever side you worked during read
slower (the QoR verdict is decision-based and immune).

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
  * The sweep is PARALLEL by default (`--jobs`, default = CPU count): each flow
    runs in its own worker process (flows are independent — fresh session, own
    cwd — so the QoR/WL metrics are byte-identical to a serial run).  Only the
    per-flow `sec` column is affected: under parallel load it inflates with CPU
    contention, so pass `-j 1` when the per-flow timing itself is the point.
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
    # Per-cell layer policy vehicles (docs/internal/hier_layer_caps.md §12):
    # bands, fractional shares, and the by-depth bulk declaration — permanent
    # corpus rows so the policy path is regression-guarded like everything
    # else (Phase 5 Q2).
    "flow/rnr/mix2_fast_bottomup_caps.buda",
    "flow/rnr/mix2_fast_bottomup_shared.buda",
    "flow/chip/chip_bottomup_caps.buda",
    # On-grid stacked variant: cheap (~30s) and the guard for instance-phase
    # alignment — a regression in placement phase shows up as a QoR change.
    "flow/chip/chip_stack_bottomup.buda",
    # Its top-down twin, and the ONLY corpus row that exercises the mirrored
    # track origins (chip_tracks_mirror.buda) on the top-down path.  The
    # bottom-up vehicle above cannot cover them: it is the copy path, where a
    # broken origin shows up as bits solved alone rather than copied, so a
    # regression there is loud in a way it is not here.  Worth its own row
    # because the origins are three hand-placed numbers with no other guard.
    # Not cheap, unlike its bottom-up twin: ~101s serial (21/241/18).
    "flow/chip/chip_stack_topdown.buda",
    "flow/rnr/mix2_fast_on_aligned_sql.buda",
    # The `_2x` twins: same flow on double the track density (16 signals per
    # period, same metal).  Their ORIGINALS stay in the corpus too — the
    # pair is the measurement.  docs/internal/track_density_doubling.md
    "flow/rnr/mix2_fast_bottomup_caps_2x.buda",
    "flow/rnr/mix2_fast_on_aligned_sql_2x.buda",
    "flow/rnr/mix2_fast_topdown.buda",
    "flow/rnr/mix2_topdown_refine.buda",
    "flow/chip/chip_topdown.buda",
    "flow/chip/chip_bottomup.buda",
    "flow/chip/chip3_topdown.buda",
    "flow/chip/chip3a_bottomup.buda",
    # ── Feature families the corpus had no QoR guard for at all ───────────
    # `--candidates` reported these as NEW COVERAGE: whole command families
    # exercised only by pass/fail flow tests, so a change that quietly moved
    # their overlaps / unplaced / wirelength had nothing to fail.  Chosen for
    # coverage per second — six of the seven are under half a second against
    # a ~20-minute sweep.
    #
    # NDR (def_ndr / set_ndr / dump_ndr): eleven vehicles existed, none in
    # the corpus.  Three cover the axes that differ in ARITHMETIC rather
    # than in syntax — shields, bonding, and the bottom-up copy path where a
    # governed template is solved once and copied.
    "flow/ndr_shield_flat.buda",
    "flow/ndr_bond.buda",
    "flow/ndr_bottom_up.buda",
    # Interchange: eleven new tokens in one flow (import_def_lef /
    # import_verilog / import_lef_tech / export_gds / emit_guides /
    # export_def_blockages / set_import_scale / set_unit_check /
    # derive_container_bboxes / save_bdb / def_gds_layer).  `flow/def/chip`
    # is the SMALLEST vehicle that exercises the path — built for that.
    "flow/def/chip.buda",
    # add_keepout and set_track_pitch: a user-declared keepout ZONE had no
    # corpus flow, which is the constraint KEEPOUT_CROSS, the DNUTS crossing
    # cull and the cull heal all exist for.
    "flow/comprehensive_regression.buda",
    # The expert edit surface (edit_topology / edit_add_trunk / edit_add_stub
    # / edit_set_span / edit_commit): a USER candidate committed by hand
    # routes through the same planner and NUTS as a generated one.
    "flow/c_dd_detour.buda",
    # Hier CONVERGENT bundling on a real SoC-shaped netlist — the fan-in tree
    # path, whose per-bit taper the QoR triple is sensitive to.  The only
    # entry here that costs real time (~23s); the rest are rounding error.
    "flow/rv/soc_conv_div.buda",
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
    # NDR shield wires are real metal but not SIGNAL wirelength (R11) —
    # excluded so a rule-governed flow's detailed WL stays comparable.
    dwl = (round(sum(_seg_wl(x) for x in dr.net_segments
                     if getattr(x, "placed", True)
                     and not getattr(x, "is_shield", False)))
           if dr is not None else None)
    return awl, dwl


def run_flow(flow):
    """Source one flow end-to-end and capture its final QoR.  Returns a dict
    with overlaps/unplaced/viol_bundles/abstract_wl/detailed_wl/sec, or an err
    field if the flow raised."""
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    # --decisions: collect the run's decision-trace records (risk plan R2)
    # and dump them per flow, so two corpus runs can be diffed
    # decision-wise without parsing log text.
    _dec_dir = os.environ.get("BUDA_QOR_DECISIONS_DIR")
    if _dec_dir:
        s._decision_trace = []
    tmp_logs = private_log_dir(s)               # parallel worker: isolate logs
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
    finally:
        if tmp_logs:
            shutil.rmtree(tmp_logs, ignore_errors=True)
    dt = time.time() - t0
    if _dec_dir and getattr(s, "_decision_trace", None) is not None:
        try:
            os.makedirs(_dec_dir, exist_ok=True)
            key = flow.replace("/", "__").replace("\\", "__")
            with open(os.path.join(_dec_dir, key + ".jsonl"), "w") as f:
                for tag, kv in s._decision_trace:
                    f.write(json.dumps({"tag": tag, **kv}, default=str,
                                       sort_keys=True) + "\n")
        except OSError:
            pass                                 # informational only
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


def _flow_weight(flow):
    """Scheduling weight for the parallel sweep: the flow file's size plus any
    file it references via a top-level `source` / `open_bdb` line (one level,
    no recursion).  Heavy-BDB flows (the chip/* vehicles) are submitted FIRST
    so the longest flow cannot start last and become the wall-clock tail.
    Scheduling only — results are always returned in input order."""
    total = 0
    try:
        path = flow if os.path.isabs(flow) else os.path.join(_ROOT, flow)
        total += os.path.getsize(path)
        d = os.path.dirname(path)
        with open(path) as fh:
            for ln in fh:
                toks = ln.split("#", 1)[0].split()
                if len(toks) >= 2 and toks[0] in ("source", "open_bdb"):
                    ref = os.path.join(d, toks[1])
                    if os.path.isfile(ref):
                        total += os.path.getsize(ref)
    except OSError:
        pass
    return total


_IN_WORKER = False       # True inside a sweep worker process (set by _worker_init)


def _worker_init():
    """Initializer for sweep worker processes.  A parallel sweep already runs
    `jobs` flows at once, and ripup_reroute's own C++ stall-sweep pool
    (BUDA_SWEEP_THREADS, 0 = hardware concurrency) would multiply that into
    jobs x cores threads.  Pin it to 1 per worker — the stall sweep is
    decision-identical by construction, so the flows' results do not change.
    An explicit user setting always wins (setdefault).  Also marks the process
    as a worker so the flow runners isolate their session log files."""
    global _IN_WORKER
    _IN_WORKER = True
    os.environ.setdefault("BUDA_SWEEP_THREADS", "1")
    # Same reasoning for the planner's candidate-scoring pool (chip-flow P1):
    # `jobs` flows already saturate the cores, so nested planner threads would
    # only add contention.  Results are identical either way (the scoring
    # reduction is decision-identical at any thread count); a `-j 1` sweep
    # keeps planner parallelism and gets the single-flow speedup.
    os.environ.setdefault("BUDA_PLAN_THREADS", "1")


def private_log_dir(session):
    """In a parallel worker, point the session's log sink (`_log_run_dir` —
    the same redirect the CLI's --log archive mode uses) at a throwaway temp
    dir, and return it for cleanup.  Without this, sweep sessions leave
    `script_path` unset, so `_get_log_path` resolves every flow in a shared
    directory (several rnr/* and big_data_test/* corpus entries) to the SAME
    `log/nuts.log` / `log/bdb_blocks.log`, and concurrent workers would
    truncate/interleave each other's diagnostics (Codex #541).  No-op (None)
    outside a worker, so a serial sweep keeps the historical log locations."""
    if not _IN_WORKER:
        return None
    d = tempfile.mkdtemp(prefix="qor_logs_")
    session._log_run_dir = d
    return d


def default_jobs():
    """The CLI default for --jobs: one worker per CPU."""
    return os.cpu_count() or 1


def _run_isolated(run_fn, flow):
    """One flow in a throwaway single-worker pool.  Used to re-run the flows a
    broken pool left unfinished: a hard crash here is attributable to exactly
    THIS flow (its err row), and every other unfinished flow still runs."""
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    try:
        with ProcessPoolExecutor(max_workers=1, initializer=_worker_init) as ex:
            return ex.submit(run_fn, flow).result()
    except BrokenProcessPool:
        return {"flow": flow, "err": "worker: hard crash (process died)"}
    except Exception as e:                      # noqa: BLE001
        return {"flow": flow, "err": f"worker: {type(e).__name__}: {str(e)[:60]}"}


def sweep(run_fn, flows, jobs, progress=None):
    """Map `run_fn` (a top-level, picklable flow runner returning a result
    dict) over `flows`, in `jobs` worker processes.  Results are returned in
    INPUT order regardless of completion order — keyed by submission INDEX,
    so a flow listed twice (a --flows timing/nondeterminism check) keeps both
    runs' results.  `progress(result)` is called as each flow finishes.
    jobs<=1 runs serially in-process (the historical behavior —
    timing-faithful `sec`, easier pdb).

    Failure containment: a run_fn that raises yields an err row for its flow.
    A worker that DIES (segfault etc.) breaks the whole ProcessPoolExecutor —
    every unfinished future raises BrokenProcessPool, not just the culprit's —
    so the unfinished flows are then re-run one at a time in throwaway
    single-worker pools (`_run_isolated`, sequential — crash recovery is the
    rare path): the crashing flow alone gets the err row, its innocent
    neighbors still produce real results, and the sweep always returns one row
    per input flow."""
    if jobs <= 1 or len(flows) <= 1:
        out = []
        for f in flows:
            r = run_fn(f)
            if progress:
                progress(r)
            out.append(r)
        return out
    from concurrent.futures import CancelledError, ProcessPoolExecutor, \
        as_completed
    from concurrent.futures.process import BrokenProcessPool
    results = {}
    order = sorted(range(len(flows)), key=lambda i: _flow_weight(flows[i]),
                   reverse=True)                # longest-first scheduling
    with ProcessPoolExecutor(max_workers=min(jobs, len(flows)),
                             initializer=_worker_init) as ex:
        futs = {ex.submit(run_fn, flows[i]): i for i in order}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
            except (BrokenProcessPool, CancelledError):
                continue                        # re-run isolated below
            except Exception as e:              # noqa: BLE001 — run_fn raised
                r = {"flow": flows[i],
                     "err": f"worker: {type(e).__name__}: {str(e)[:60]}"}
            results[i] = r
            if progress:
                progress(r)
    for i in order:                             # crash recovery (rare path)
        if i in results:
            continue
        r = _run_isolated(run_fn, flows[i])
        results[i] = r
        if progress:
            progress(r)
    return [results[i] for i in range(len(flows))]


def cmd_run(flows, out, jobs=1):
    # Resolve BEFORE the chdir: `--out results.json` means "here", where the
    # user is, not "wherever this tool happens to chdir to".  A no-op for the
    # documented usage (run from the repo root, where the two coincide), and
    # the difference between two files and one under --vs, whose sweeps run in
    # different directories.
    if out:
        out = os.path.abspath(out)
    os.chdir(_ROOT)                             # flow paths are repo-root-relative
    t0 = time.time()
    results = sweep(run_flow, flows, jobs,
                    progress=lambda r: print(json.dumps(r), flush=True))
    print(f"\nswept {len(results)} flows in {time.time() - t0:.1f}s "
          f"(jobs={max(1, jobs)})")
    if out:
        with open(out, "w") as fh:
            json.dump(results, fh, indent=1)
        print(f"wrote {len(results)} results -> {out}")
    return results


# ── baseline-in-a-worktree (--vs) ──────────────────────────────────────────
# The documented two-checkout recipe needs a CLEAN tree to `git checkout
# main`, which is exactly what you do not have while measuring uncommitted
# work.  The workaround people reach for — stash, build, measure, pop — makes
# the measurement a mutation of the working tree, with three failure modes
# that all produce a WRONG NUMBER rather than an error: anything else touching
# the tree mid-run corrupts it; committing mid-run leaves nothing to stash, so
# the "baseline" silently measures the branch and reports a vacuous
# no-difference; and an interrupted run leaves the tree stashed.
#
# A git worktree removes the hazard at the root: the baseline is materialized
# somewhere else entirely, so the working tree is never read, written, or
# checked out, and you may edit and commit freely while it runs.

def _sh(cmd, cwd, check=True, env=None, stream=False):
    """Run a command in `cwd`.

    `stream=True` lets it write straight to our stdout instead of capturing:
    the baseline sweep takes as long as a corpus run, and a captured one shows
    NOTHING until it finishes, which is indistinguishable from a hang for
    twenty minutes.  Errors are still caught — only the reporting differs,
    since a streamed failure has already printed its own diagnosis."""
    if stream:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env)
        if check and r.returncode != 0:
            sys.exit(f"[--vs] command failed "
                     f"({' '.join(str(c) for c in cmd)}) — see its output above")
        return r
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"[--vs] command failed ({' '.join(str(c) for c in cmd)}):\n"
                 f"{r.stdout[-1500:]}{r.stderr[-2000:]}")
    return r


_WT_PREFIX = "buda-qor-"


def baseline_worktree(rev):
    """A git worktree holding `rev`, cached under the temp dir and keyed by
    COMMIT so repeated baselines against an unchanged main reuse its build.

    Detached on purpose: a worktree that checked out the BRANCH would refuse
    (git allows one checkout per branch) and, worse, would tie the baseline to
    a moving ref."""
    commit = _sh(["git", "rev-parse", rev + "^{commit}"], _ROOT).stdout.strip()
    wt = Path(tempfile.gettempdir()) / f"{_WT_PREFIX}{commit[:12]}"
    if not (wt / "CMakeLists.txt").exists():
        # `prune` first: a cache dir deleted by hand (or by a temp sweep)
        # leaves a registration behind that makes `worktree add` refuse.
        _sh(["git", "worktree", "prune"], _ROOT)
        _sh(["git", "worktree", "add", "--detach", str(wt), commit], _ROOT)
    return wt, commit


def cached_baselines():
    """Every cached baseline worktree, newest first: (path, bytes)."""
    root = Path(tempfile.gettempdir())
    out = []
    for d in root.glob(_WT_PREFIX + "*"):
        if not d.is_dir():
            continue
        size = sum(f.stat().st_size for f in d.rglob("*")
                   if f.is_file() and not f.is_symlink())
        out.append((d, size))
    return sorted(out, key=lambda t: t[0].stat().st_mtime, reverse=True)


def cmd_clean_baselines(keep=0):
    """Remove cached baseline worktrees (each carries a full build — ~140 MB
    here), keeping the `keep` most recently used.

    They are a CACHE, not state: a removed one is simply rebuilt on the next
    `--vs` against that commit.  Worth an explicit command because main moves
    often, so the cache grows one build per baseline commit and nothing else
    would ever collect it."""
    entries = cached_baselines()
    if not entries:
        print("no cached baseline worktrees")
        return
    for i, (d, size) in enumerate(entries):
        if i < keep:
            print(f"  keep   {d}  ({size / 1e6:.0f} MB)")
            continue
        _sh(["git", "worktree", "remove", "--force", str(d)], _ROOT,
            check=False)
        shutil.rmtree(d, ignore_errors=True)      # hand-deleted / not registered
        print(f"  removed {d}  ({size / 1e6:.0f} MB)")
    _sh(["git", "worktree", "prune"], _ROOT, check=False)


def _build(tree, label):
    tree = Path(tree)
    print(f"[--vs] building {label} ({tree})...", flush=True)
    # The worktree's OWN bin/bb: it resolves the repo root as its parent dir,
    # so each side builds itself into its own build/ and neither can pick up
    # the other's artifacts.
    _sh([tree / "bin" / "bb"], tree, stream=True)


def vs_paths(out):
    """(branch json, baseline json) for a `--vs` run, both ABSOLUTE.

    Absolute is load-bearing, not tidiness: the two sweeps run in DIFFERENT
    directories (the baseline subprocess in the worktree, `cmd_run` after
    chdir'ing to the repo root), so a relative path means two different files
    and `--compare` then dies with FileNotFoundError — AFTER both sweeps have
    run, which is the most expensive moment to fail (Codex P2 on #692).
    Resolved once, up front, against the directory the user invoked from."""
    if out:
        mine = os.path.abspath(out)
        return mine, mine + ".base.json"
    d = tempfile.mkdtemp(prefix="qor-vs-")
    return os.path.join(d, "branch.json"), os.path.join(d, "base.json")


def cmd_vs(rev, out, jobs, flows=None, build=True):
    """Measure `rev` in a worktree and the CURRENT tree, then compare.

    Two properties worth being explicit about:

    * The baseline is the COMMIT, not your working tree.  `--vs HEAD` measures
      your last commit; uncommitted changes are, by design, only on the branch
      side.  The commit is echoed so the comparison is self-describing.
    * The baseline runs the BASELINE's own harness (its copy of this file),
      matching the two-checkout recipe this replaces.  If CORPUS differs
      between the two, `--compare` reports the comparable subset rather than
      pretending the sets match.

    Sequential by construction: the two sweeps never overlap, so the runtime
    column is not distorted by them contending with EACH OTHER.

    It can still be distorted by YOU.  Being free to build, test and commit
    while the sweep runs is the point of this mode, and every one of those
    competes for the same CPU — so whichever side you happened to work
    during reads slower.  Measured on this feature's own landing run: the
    QoR verdict was 41/41 unchanged (it is decision-based, so contention
    cannot move it) while the runtime column showed +4.7%, entirely from
    committing and running tests during the branch half.  The runtime diff
    was already labelled single-run and non-gating; under `--vs` treat it
    as advisory only, and use `tools/runtime_ab.py` on an idle machine when
    runtime is the question being asked.
    """
    wt, commit = baseline_worktree(rev)
    print(f"[--vs] baseline {rev} = {commit[:12]} in {wt}")
    mine, base = vs_paths(out)

    if build:
        # BOTH sides.  Measuring a stale working-tree build against a freshly
        # built baseline is the same class of silent-wrong-number this whole
        # mode exists to prevent, and `bin/bb` is incremental so it costs
        # nothing when the tree is already current.
        _build(wt, f"baseline {commit[:12]}")
        _build(_ROOT, "working tree")

    print(f"[--vs] sweeping baseline {commit[:12]}...", flush=True)
    env = {**os.environ,
           "PYTHONPATH": f"{wt}/build:{wt}/src:{wt}/tools"}
    cmd = [sys.executable, wt / "tools" / "qor_corpus.py",
           "--out", base, "-j", str(jobs)]
    if flows:
        cmd += ["--flows", *flows]
    _sh(cmd, wt, env=env, stream=True)

    print("[--vs] sweeping the working tree...", flush=True)
    cmd_run(flows or CORPUS, mine, jobs=jobs)

    print(f"\n[--vs] {commit[:12]} ({rev}) -> working tree\n")
    regressed = cmd_compare(base, mine)
    if not out:
        print(f"\n[--vs] result JSONs: {base} , {mine}")
    return regressed


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
    pass/fail guard): total corpus wall-clock plus the largest per-flow movers.

    This CANNOT resolve a per-flow cost of a few percent or less — it is for
    spotting a blow-up, not for costing a small change.  A measured case:
    a heal adding one DNUTS solve per flow "showed" +3.3% here while the
    flows that PROVABLY could not run it (gated out) moved +4.2%, i.e. pure
    host noise; the warm microbenchmark put the real per-flow cost between
    0.05% and 2.1% of flow wall — every flow below this line's figure, and
    spread ~40x across flows, which one aggregate cannot express.  Before
    citing a small delta from this line, read "Measuring a per-flow COST" in
    docs/internal/qor_corpus.md — it gives the two checks (an accidental
    control group; a warm best-of-N microbenchmark) that catch it.  Applies
    to RUNTIME only: the WL diff is informational for a different reason
    (topology changes legitimately trade WL) and IS deterministic."""
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
    # Per-class rollups (risk_reduction_plan.md R4): runtime profiles differ
    # qualitatively by design class (chip vs rnr vs big), so aggregate by the
    # flow's directory family — class-level drift is visible even when no
    # single flow crosses the per-flow noise floor below.
    by_class = {}
    for f, b, m in timed:
        cls = f.replace("flow/", "").split("/")[0] if "/" in f else "(root)"
        cb, cm = by_class.get(cls, (0.0, 0.0))
        by_class[cls] = (cb + b["sec"], cm + m["sec"])
    for cls in sorted(by_class, key=lambda c: -by_class[c][0]):
        cb, cm = by_class[cls]
        cd = cm - cb
        cpct = (100 * cd / cb) if cb else 0.0
        print(f"    {cls:<20} {cb:>7.1f}s -> {cm:>7.1f}s  "
              f"({cd:+.1f}s, {cpct:+.1f}%)")
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


def _decisions_report(base_path, mine_path):
    """Informational decision-stream diff (risk plan R2 `--decisions`):
    when both runs stored per-flow decision traces (the `--decisions`
    sidecar dirs), report which flows' decision RECORDS differ — a
    wording-independent identity check, so a prints-only change shows 0
    differing flows here even if log text moved."""
    db, dm = base_path + ".decisions", mine_path + ".decisions"
    if not (os.path.isdir(db) and os.path.isdir(dm)):
        return
    common = sorted(set(os.listdir(db)) & set(os.listdir(dm)))
    if not common:
        return
    diff = []
    for k in common:
        a = open(os.path.join(db, k)).read()
        b = open(os.path.join(dm, k)).read()
        if a != b:
            na, nb = len(a.splitlines()), len(b.splitlines())
            diff.append((k[:-6].replace("__", "/"), na, nb))
    print(f"\ndecision streams ({len(common)} flow(s) traced): "
          f"{len(diff)} differ")
    for f, na, nb in diff[:10]:
        print(f"  {f}: {na} -> {nb} record(s)")


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
    _decisions_report(base_path, mine_path)
    _runtime_report(paired)
    return n_worse


def cmd_check(path):
    """Exit non-zero if any flow in PATH errored.  Returns the bad-row count.

    `cmd_run` records a flow that RAISES as {"flow": ..., "err": ...} and still
    exits 0, and `cmd_compare` prints a row that is NEW to the corpus as "(new)"
    without counting it in n_worse.  So an errored sweep silently reads as "no
    regression" unless something looks for err rows explicitly.

    Lives here rather than inline in a workflow so it is testable, and so the
    nightly and the PR gate share one implementation instead of two copies that
    can drift.
    """
    with open(path) as fh:
        rows = json.load(fh)
    bad = [r for r in rows if "err" in r]
    for r in bad:
        print(f"::error::corpus flow errored in {path}: {r['flow']}: {r['err']}")
    if bad:
        print(f"{len(bad)} of {len(rows)} flow(s) errored in {path}")
    else:
        print(f"all {len(rows)} corpus flows in {path} completed")
    return len(bad)


# --------------------------------------------------------------------------
# Candidate discovery (--candidates)
#
# ADVISORY ONLY.  Nothing here edits CORPUS; membership is the owner's call.
# --------------------------------------------------------------------------

CANDIDATE_ROOTS = ("flow", "demo")

# Mode keywords per command: an argument is coverage IFF it appears here.
#
# A CLOSED VOCABULARY, not a position rule.  Position does not separate modes
# from objects — `set_bundling <prefix> <mode>` and `set_bottom_up <cell>
# [on|off]` put an OBJECT first, so a position rule emits the data-dependent
# `set_bundling:clk_` (every new prefix looks like new coverage) while missing
# `strict` vs `combined` entirely.  Position also cannot find a mode that
# moves: `run_hier_bundler depth 3 COMBINED` has its strategy third.  Matching
# a known vocabulary anywhere in the argument list fixes both, and an object
# name can never collide with it.
#
# The trade is that an UNLISTED mode is silently not coverage.  That errs in
# the safe direction — the report under-claims rather than manufacturing a
# finding — but it means this table must grow when a command gains a mode.
_MODE_WORDS = {
    "run_bundler": ("strict", "convergent", "bidirectional", "combined"),
    "run_hier_bundler": ("strict", "convergent", "bidirectional", "combined"),
    "run_planner": ("hier", "post_nuts", "signal_tracks"),
    "run_nuts_on_layer": (),
    "run_detailed_nuts": ("lo_hi", "hi_lo"),
    "load_pipeline": ("expanded",),
    "set_bundling": ("strict", "no_convergent", "no_bidirectional", "combined"),
    "set_bottom_up": ("on", "off"),
    "set_feedthru": ("on", "off"),
    "set_max_bundle_bits": ("auto", "off", "for"),
    "set_drop_dangling": ("on", "off", "drop", "clamp", "clamp_drop"),
    "set_prune_dominated": ("on", "off"),
    "set_dedup_loci": ("on", "off"),
    "set_pair_align_heal": ("on", "off"),
    "set_dead_span_escalate": ("on", "off"),
    "check_template_tracks": ("stop", "independent"),
    "align_bottom_up": ("max_shift", "force"),
    "negotiate_congestion": ("class_moves",),
    "refine_selection": ("chase_overlaps",),
    "ripup_reroute": (
        "use_edge_candidates", "no_global", "no_class_moves",
        "no_release_moves", "fast_trials", "no_fast_trials", "screen",
        "no_screen", "warm_trials", "no_warm_trials", "converge_guard",
        "no_converge_guard", "no_parallel_sweep"),
    # The planner knobs are named features, not objects — `kPeak` and
    # `charge_pull_target` are different coverage.
    "set_planner_param": (
        "kcong", "kspan", "base_cost_non_top", "kwl", "ksegs", "ksegsrel",
        "healersahead", "kbalance", "kheight", "kpeak", "track_cap_slack",
        "refine_passes", "nontop_dead_span_gate", "kwlspread",
        "charge_pull_target", "band_span_charge"),
}
_GEN_FLAGS = ("center_mode", "double_detour", "multi_trunk", "no_hanan_loci",
              "hanan_loci", "spine_relays")
for _g in ("generate_topologies", "generate_hier_topologies",
           "generate_more_topologies", "generate_topologies_for_bundle",
           "generate_topologies_for_hbundle"):
    _MODE_WORDS[_g] = _GEN_FLAGS

# Commands that cannot change what gets routed, so they are not coverage.
# The sweep runs headless (`no_viz`) and calls `check_design` itself, so a
# flow containing these drives no code the harness would not drive anyway.
_IGNORED_COMMANDS = frozenset((
    "visualize", "visualize_topologies", "report_wl", "report_wirelength",
    "report_overhead", "dump_topologies", "dump_hbundles", "dump_user_ops",
    "check_design", "check_connectivity",
))

# Ends the run wherever it is reached, including inside a sourced file: the
# CLI raises SystemExit, so nothing after it executes.
_TERMINATOR = "exit"


def _scan(path, seen):
    """(tokens, terminated) for one file and everything it sources.

    `terminated` means an `exit` was REACHED, so the caller must stop too —
    the CLI raises SystemExit, and it does not matter which file it was raised
    in.  Without this a flow's unreachable tail counts as coverage:
    `rnr/mix2_repro.buda` exits on line 15 and has `run_detailed_nuts` on line
    20, which a plain read would score as a full pipeline it never runs.
    """
    real = os.path.realpath(path)
    if real in seen or not os.path.isfile(real):
        return set(), False
    seen.add(real)
    toks, base = set(), os.path.dirname(real)
    with open(real, errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            cmd, args = parts[0].lower(), [a.lower() for a in parts[1:]]
            if cmd == _TERMINATOR:
                return toks, True
            if cmd == "source":
                if args:
                    sub, stop = _scan(os.path.join(base, parts[1]), seen)
                    toks |= sub
                    if stop:
                        return toks, True
                continue
            if cmd in _IGNORED_COMMANDS:
                continue
            toks.add(cmd)
            # An argument is coverage only if it is a KNOWN MODE WORD for this
            # command — never by position, which cannot tell a mode from an
            # object selector (a net prefix, a cell name, a bus hint).
            modes = _MODE_WORDS.get(cmd)
            if modes:
                toks |= {f"{cmd}:{a}" for a in args if a in modes}
    return toks, False


def flow_tokens(path):
    """The set of FEATURE TOKENS a flow exercises — the command names it
    REACHES, plus `cmd:mode` for each recognised mode word it passes them.

    This is a PROXY for coverage, not a proof of it.  Two flows can call an
    identical command set and still drive different code — different geometry,
    different congestion, a different candidate winning selection.  Read a
    'no new tokens' verdict as 'nothing OBVIOUSLY new', never as 'redundant'.
    """
    return _scan(path, set())[0]


def corpus_coverage(corpus=None):
    """Feature tokens the current corpus exercises, unioned over its flows."""
    covered = set()
    for f in (corpus or CORPUS):
        covered |= flow_tokens(os.path.join(_ROOT, f))
    return covered


def discover_candidates(roots=CANDIDATE_ROOTS, corpus=None):
    """Full-pipeline `.buda` flows under ROOTS that are NOT corpus members.

    'Full-pipeline' means the flow REACHES `run_detailed_nuts` — the same bar
    the corpus is drawn to.  Reaches, not contains: `rnr/mix2_repro.buda` has
    the command but `exit`s five lines above it, so under the shipped CLI it
    stops at `run_nuts` and is not a full-pipeline flow.  Eligibility is read
    off the same reachable-token scan as coverage, so the two cannot disagree.

    Sourced fragments (track fixtures, block lists) never reach it either, and
    that is what keeps this from listing every file in the tree.
    """
    member = set(corpus or CORPUS)
    found = []
    for root in roots:
        for dp, _, fns in os.walk(os.path.join(_ROOT, root)):
            for fn in sorted(fns):
                if not fn.endswith(".buda"):
                    continue
                path = os.path.join(dp, fn)
                try:
                    rel = os.path.relpath(path, _ROOT)
                except ValueError:
                    # Windows: an out-of-tree root on another DRIVE (pytest tmp
                    # on C: vs the checkout on D:) has no repo-relative form.
                    # Keep the absolute path: it can never collide with the
                    # repo-relative corpus member names, so the exclusion
                    # semantics are unchanged -- and on Linux an out-of-tree
                    # root already yields a non-member '../..' form anyway.
                    # (Measured: doc-validation run 12; third occurrence of
                    # the cross-drive relpath pathology in this tree.)
                    rel = path
                # Normalize to forward slashes: corpus member names use '/',
                # and on Windows BOTH relpath and the absolute fallback return
                # backslashes -- without this, corpus members would never match
                # (and be listed as their own candidates).  Round-1's fix
                # stopped the ValueError but missed this half (measured:
                # doc-validation run 13, both MSVC lanes).
                rel = rel.replace(os.sep, "/")
                if rel in member:
                    continue
                if "run_detailed_nuts" in flow_tokens(path):
                    found.append(rel)
    return sorted(found)


def cmd_candidates(roots=CANDIDATE_ROOTS, quantify=False, jobs=1):
    """Report flows that could join the corpus, ranked by NEW coverage.

    Prints and returns; it never edits CORPUS.  Adding or removing a row is a
    judgement about what the benchmark should defend and what runtime that is
    worth — the tool supplies the evidence, the owner makes the call.
    """
    os.chdir(_ROOT)
    covered = corpus_coverage()
    cands = discover_candidates(roots)
    rows = []
    for rel in cands:
        new = sorted(flow_tokens(os.path.join(_ROOT, rel)) - covered)
        rows.append({"flow": rel, "new_tokens": new})

    fresh = [r for r in rows if r["new_tokens"]]
    same = [r for r in rows if not r["new_tokens"]]
    # Most-new-coverage first; ties alphabetical so the report is stable.
    fresh.sort(key=lambda r: (-len(r["new_tokens"]), r["flow"]))

    print(f"QoR corpus candidates — ADVISORY ONLY.  This tool never edits "
          f"CORPUS;\nwhich flows to add or remove is the owner's decision.\n")
    print(f"corpus:     {len(CORPUS)} flows, {len(covered)} distinct feature tokens")
    print(f"scanned:    {', '.join(roots)}")
    print(f"candidates: {len(cands)} full-pipeline flows outside the corpus "
          f"({len(fresh)} with new coverage, {len(same)} without)\n")

    if quantify:
        import qor_table                            # local: qor_table imports us
        measured = {r["flow"]: r for r in
                    sweep(qor_table.run_flow, [r["flow"] for r in fresh], jobs)}
        for r in fresh:
            r.update({k: v for k, v in measured.get(r["flow"], {}).items()
                      if k != "flow"})

    print("== NEW COVERAGE — nothing in the corpus exercises these tokens ==\n")
    for r in fresh:
        print(f"  {r['flow']}")
        if quantify:
            if "err" in r:
                print(f"      ERRORED: {r['err']}  (not a candidate until fixed)")
            else:
                print(f"      bund={r.get('bund')} busS={r.get('busS')} "
                      f"netS={r.get('netS')} busWL={r.get('busWL')} | "
                      f"{r.get('ovl')}/{r.get('unpl')}/{r.get('viol')} "
                      f"| {r.get('sec')}s")
        print(f"      new: {' '.join(r['new_tokens'])}")
    if not fresh:
        print("  (none — the corpus already covers every candidate's tokens)")

    print(f"\n== NO NEW TOKENS — {len(same)} flows ==")
    print("   Not proof of redundancy: identical commands can still drive")
    print("   different geometry and congestion.  Judge these on what they")
    print("   would DEFEND, not on this list.\n")
    for r in same:
        print(f"  {r['flow']}")
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="Run the QoR corpus and/or compare two runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See the module docstring for the baseline-vs-branch recipe.")
    ap.add_argument("--flows", nargs="+", metavar="FLOW",
                    help="run these flows instead of the default corpus")
    ap.add_argument("--out", metavar="PATH",
                    help="write the run's results as JSON to PATH")
    ap.add_argument("--vs", metavar="REV",
                    help="measure REV in a git WORKTREE and the current tree, "
                         "then compare — the baseline-vs-branch recipe without "
                         "touching the working tree, so uncommitted work needs "
                         "no stash and you may edit or commit while it runs.  "
                         "The baseline is the COMMIT, not your working tree")
    ap.add_argument("--clean-baselines", nargs="?", type=int, const=0,
                    metavar="KEEP",
                    help="remove cached --vs baseline worktrees (each holds a "
                         "full build), keeping the KEEP most recent (default "
                         "0 = all).  They are a cache: the next --vs against "
                         "that commit rebuilds it")
    ap.add_argument("--no-build", action="store_true",
                    help="with --vs: skip building both sides (they must "
                         "already match their sources; a stale build measures "
                         "code you are not looking at)")
    ap.add_argument("--compare", nargs=2, metavar=("BASE", "BRANCH"),
                    help="diff two result JSONs (from earlier --out runs) on "
                         "QoR + runtime; exits non-zero if any flow regressed")
    ap.add_argument("--check", metavar="PATH",
                    help="exit non-zero if any flow in PATH errored.  For "
                         "unattended use: an errored sweep must not be cached, "
                         "promoted, or compared as if it were data.")
    ap.add_argument("--candidates", action="store_true",
                    help="report full-pipeline flows OUTSIDE the corpus, "
                         "ranked by feature coverage the corpus lacks.  "
                         "Advisory: never edits CORPUS — adding or removing a "
                         "flow is the owner's call")
    ap.add_argument("--quantify", action="store_true",
                    help="with --candidates: also RUN each new-coverage "
                         "candidate for its sizes, QoR and runtime, so its "
                         "cost is on the table beside its coverage")
    ap.add_argument("--decisions", action="store_true",
                    help="with --out: also store each flow's decision-trace "
                    "records in <out>.decisions/ so --compare can diff runs "
                    "decision-wise (wording-independent)")
    ap.add_argument("-j", "--jobs", type=int, default=default_jobs(),
                    metavar="N",
                    help="worker processes for the sweep (default: CPU count "
                         "= %(default)s; 1 = serial, timing-faithful sec)")
    args = ap.parse_args()

    if args.check:
        sys.exit(1 if cmd_check(args.check) else 0)

    if args.clean_baselines is not None:
        cmd_clean_baselines(keep=args.clean_baselines)
        return

    if args.candidates:
        cmd_candidates(quantify=args.quantify, jobs=args.jobs)
        return
    if args.quantify:
        ap.error("--quantify is only meaningful with --candidates")
    if args.no_build and not args.vs:
        ap.error("--no-build is only meaningful with --vs")

    if args.compare:
        sys.exit(1 if cmd_compare(*args.compare) else 0)

    if args.vs:
        sys.exit(1 if cmd_vs(args.vs, args.out, args.jobs,
                             flows=args.flows,
                             build=not args.no_build) else 0)

    if args.decisions and args.out:
        _dd = os.path.abspath(args.out + ".decisions")
        # A reused --out must not inherit a previous run's traces: workers
        # overwrite only the flows THEY run, so a subset/errored rerun
        # would leave stale files that _decisions_report attributes to the
        # current experiment (Codex #621).
        shutil.rmtree(_dd, ignore_errors=True)
        os.environ["BUDA_QOR_DECISIONS_DIR"] = _dd
    cmd_run(args.flows or CORPUS, args.out, jobs=args.jobs)


if __name__ == "__main__":
    main()
