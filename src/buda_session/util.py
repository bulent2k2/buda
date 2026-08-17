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

"""Module-level helpers shared by the BudaSession mixins.

Moved verbatim from buda_cli so the mixin modules (and buda_cmds) can
import them without a buda_cli import cycle; buda_cli re-exports them
for compatibility.
"""
import functools
import os


# THE `.buda` line rules live in `buda_script` — standalone and engine-free,
# so the tools that also parse `.buda` (buda2tcl, buda2bdb, qor_corpus,
# scan_fanin) reach the same code without importing the compiled extension.
# Re-exported here for `buda_cli`, which exposes both under these names
# (`tools/buda2tcl.py` imports one of them from there).  A command HANDLER
# imports from `buda_script` DIRECTLY — one import path for one rule, so a
# reader never has to ask which spelling a handler took.
from buda_script import (strip_inline_comment,                    # noqa: F401
                         sole_path_arg)

# The engine-side spelling, kept because that is the name buda_cli exposes.
_strip_inline_comment = strip_inline_comment


def resolve_script_path(session, path, *, is_read=False):
    """Resolve a script-declared relative path against the enclosing script's
    directory — the innermost `source`d file — falling back to the CWD when no
    script is running (interactive use, the Tcl front end, the Python API).

    This is THE path rule, shared by every path-taking command.  It used to be
    only `open_bdb` / `save_bdb` / `source`'s rule, while the import/export
    commands resolved against the CWD — so two same-looking relative paths on
    adjacent lines meant different files, and `flow/def/chip.buda` ran from
    exactly one directory (docs/internal/opens_interchange.md item 4).  One
    rule makes a `.buda` script a location-independent artifact, the same
    semantics as every language's include.

    `is_read=True` adds the migration aid: when the script-relative candidate
    does not exist but the CWD-relative one DOES, say so (BUDA-1609) before
    the open fails — a script written against the old CWD rule should get a
    diagnosis naming both roots, not a bare file-not-found for a file its
    author can see exists.  The note never changes the resolution: the rule
    stays deterministic, and the read then fails on the resolved path.
    """
    if not path or os.path.isabs(path):
        return path
    stack = getattr(session, "_script_stack", None)
    if not stack:
        return path
    base = os.path.dirname(stack[-1])
    resolved = os.path.normpath(os.path.join(base, path))
    if is_read and not os.path.exists(resolved) and os.path.exists(path):
        import buda_diag
        buda_diag.emit(
            "BUDA-1609",
            f"'{path}' does not exist relative to the script ({base}) but "
            f"does relative to the CWD ({os.getcwd()}); script paths resolve "
            f"against the script's directory — move the file or restate the "
            f"path relative to the script")
    return resolved


def ensure_parent_dir(path):
    """`mkdir -p` the parent directory of an output `path` before writing it.

    The advisory writers (emit_guides / export_def_blockages / export_gds) open
    their destination directly, but a flow's output directory (e.g.
    flow/def/out/) may not exist on a fresh checkout — it is commonly
    git-ignored.  Without this the writer fails with FileNotFoundError *after*
    the pipeline has already routed cleanly, which reads as a routing success
    that then can't write.  See docs/internal/opens_interchange.md item 4.
    No-op for a bare filename (no directory part) or an existing directory."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)

# ripup_reroute tuning (greedy hill-climb over topology selections).
_RR_MAX_CANDIDATES_PER_BUNDLE = 8   # alternate candidates tried per contender / iter
_RR_DEFAULT_MAX_ITER = 10           # outer-loop cap when no arg given

# Global-occupant pass (runs at a stall; wishlist-healer "global-overlap
# re-route of NON-contended bundles").
_RR_GLOBAL_TOP_K = 3                # band occupants ranked per contention site
_RR_GLOBAL_MOVES_PER_OCC = 6        # index alternates tried per occupant
_RR_GLOBAL_MAX_TRIALS = 36          # hard trial budget per stall

# Bottom-up template CLASS moves (runs after the global pass at a stall;
# docs/internal/bottomup_healer_templates.md plan item A).  A `hier.locked`
# instance can't be re-pinned individually — its routing is a fixed copy of
# its cell template — so when a locked bundle holds the residual contention
# the healer re-pins the TEMPLATE (one move re-routes every instance of the
# rotation class) and re-runs the cell-local solve for correct layers.
_RR_CLASS_TOP_N = 8                 # template alternates tried per class / stall
_RR_CLASS_MAX_TRIALS = 32           # hard trial budget per stall (the
#                                     _RR_GLOBAL_MAX_TRIALS symmetry — class
#                                     trials are the most expensive kind, so a
#                                     many-class stall must not cost
#                                     TOP_N x N_classes of them; issue #474)
# Parallel stall sweep (rnr runtime P1, trial_sweep.cpp): evaluate the
# deferred stall-certificate moves on C++ worker threads (GIL released) —
# metrics implement the sequential fast-trial semantics exactly, and any
# winning move is replayed through the sequential trial before committing.
# `no_parallel_sweep` opts out; BUDA_SWEEP_THREADS caps the thread count
# (0 = hardware concurrency).
_RR_PARALLEL_SWEEP_DEFAULT = True

_RR_RELEASE_MAX_TRIALS = 24         # release-pass hard trial budget per stall
#                                     (same rationale: each locked-open
#                                     instance costs up to 1 + _RR_CLASS_TOP_N
#                                     full NUTS/DNUTS reruns — a many-instance
#                                     infeasible design must not run
#                                     unbounded; Codex #487)

# Fast trials (RR round 3): trials skip metric-neutral passes (tighten_pulls
# in stage a — overlap-non-increasing, so the trial metric is an upper bound
# and accepts stay sound; via emission in stage b — pure output).  Commits
# always re-run the FULL pipeline, so the committed state is never degraded.
# Rejections can rarely be spurious (a move tighten would have improved past
# the bar) — corpus-measured before this default was set.
_RR_FAST_TRIALS_DEFAULT = True

# Fixed-context screen (RR round 3, final lever): before full-trialing a
# contender's alternates, place each candidate's segments ALONE against every
# other bundle's baseline placement frozen as fixed occupancy (~ms per
# candidate vs ~100ms+ per full trial) and full-trial only the
# _RR_SCREEN_TOP_N best-screened moves.  Screened-out moves are DEFERRED, not
# dropped: a stalled iteration sweeps them before the global pass, so the
# loop still stops only when a FULL sweep proves no improving move — and the
# accept decision always runs on the true full metric, so a bad screen can
# only reorder work, never commit a wrong move.  Default corpus-measured
# (the #290/#291 pattern); `no_screen` / `screen` tokens override per run.
_RR_SCREEN_DEFAULT = True
_RR_SCREEN_TOP_N = 2

# Warm trials (RR round 4): before paying a full COLD trial for a move, run
# the warm-start single-bundle re-solve (rerun_bundle_warm — place the moved
# bundle against the frozen baseline, then repair/corner/tighten the
# unfrozen union) and skip the cold trial unless the WARM metric strictly
# improves.  The warm metric is a measured PREDICTOR (Phase-0 study:
# 91-100% accept agreement, 4.6-6x cheaper per solve on bigHalf), never the
# accept basis: warm-improving moves still run the existing cold trial +
# commit path (a false-accept costs one cold trial), and warm-rejected
# moves are cold-swept at the iteration's stall point before any
# stop/global verdict — the loop's certificate remains a full COLD sweep,
# so a false-reject costs time, never the endpoint.
#
# Default OFF, corpus-measured: with the #293 screen already cutting cold
# trials to near-minimum (bigHalf stage b: 11) and fast trials cutting
# their cost, the pre-filter is cost-NEUTRAL to slightly negative on the
# current corpus (bigHalf 12.3 vs 12.7s — wash; mix wash; big2 +0.09s) —
# every warm-accepted move pays warm+cold, and a ~41-70ms warm eval is only
# ~2.7x cheaper than a post-screen fast-cold trial.  Opt in with
# `warm_trials` on designs where per-trial cold cost grows past ~3x the
# warm eval (the study's crossover); `no_warm_trials` forces off.
_RR_WARM_TRIALS_DEFAULT = False

# Dead-span escalation folded into the healers (2026-07-19): before a
# stage-b (DNUTS-open) healer hill-climb, escalate every genuinely-dead LOW
# segment (zero keepout-clear signal tracks over its placed geometry — a
# guaranteed open no candidate re-pin can fix) to a TOP layer and re-solve,
# so the healer starts from an opens-reduced state and absorbs any collateral
# overlap.  Default ON: only stage-b healer runs are affected, and escalation
# strictly reduces opens; a healer-less flow (e.g. mempool_tile) is untouched.
_RR_HEAL_DEAD_SPANS_DEFAULT = True

# Ripup convergence guard (2026-07-20): stop stage-b ripup early on an
# over-capacity design that can't converge, to save the (large) runtime it
# would otherwise burn for ~zero gain.  Measured trajectories: converging
# flows reach the primary metric's floor in a FEW iterations (bigHalf 2,
# mix2 5 — sometimes plateauing then jumping to 0 in the LAST iter), so a
# naive "opens stalled" stop is unsafe; a hopeless flow (tc3a) stays at
# 86%+ of its opens after 30 iters (~40 cleared/iter out of 8000+ — ~180
# more iters needed).  The guard fires only when ALL of: (a) ≥ WINDOW
# committed iterations have run, (b) the primary metric is still ≥ FLOOR —
# far above any converging corpus flow's mid-ripup residual (≤48) — and
# (c) < FRAC of the window-start metric was cleared over the last WINDOW
# iterations.  Provably cannot fire on a flow that converges in < WINDOW
# iters or drops below FLOOR; trades a tiny endpoint change on genuinely
# non-converging flows for a large runtime saving.  STAGE-B ONLY (gated on
# stage == 'b' in _ripup_reroute): a stage-a run clears NUTS overlaps that
# DetailedNUTS depends on and must exhaust its requested budget rather than
# stop early.  Default ON; disable per call with the `no_converge_guard`
# keyword.
_RR_CONVERGE_GUARD_DEFAULT = True
_RR_CONVERGE_WINDOW = 6
_RR_CONVERGE_FLOOR = 100
_RR_CONVERGE_FRAC = 0.03


def find_tug_of_war_pairs(segs, net_pull=None):
    """Detect outward opposite-pull connector pairs on a topology's segments.

    A *tug-of-war* (wishlist-nuts "Opposite-pull connector pairs") is the
    structural signature of NUTS realization risk: two connectors riding one
    interior segment T, pulling in opposite OUTWARD directions, so each locally
    shortens its own perpendicular leg while jointly STRETCHING T between them.
    The pull-target breakpoint clamp bounds each pull's overshoot, but the pair
    stays the mechanism that re-opens under contention or a breakpoint gap (the
    canonical b44 bundle-1 case: seg5 tugged by seg1(-)/seg3(+)).

    A rider R connects to T at a perpendicular T-junction, so R's slide axis is
    parallel to T's along-axis and R's signed ``net_pull`` names a direction
    ALONG T: net_pull<0 pulls its junction toward lower-along-T, net_pull>0
    toward higher.  When a -puller sits BELOW a +puller on T (distinct junction
    positions) the two diverge and stretch T; a -puller ABOVE a +puller
    converges (benign) and is not flagged.

    Pure read-only over the already-derived ConnSeg data (``net_pull`` from
    derive_net_pull, junction ``at_pos`` from the seg conns) — no extra passes,
    no mutation, so it never changes selection or placement.

    ``segs`` is a list of ConnSeg (``ConnTopology.segs()``).  ``net_pull`` is an
    optional per-segment EFFECTIVE net_pull override — a bundle plan's
    ``seg_net_pull`` list (post-dogleg): a dogleg pins the value NUTS actually
    placed with when ConnTopology would recompute the split-topology pull wrongly,
    so the report must read it too.  Applied exactly as NUTS's ``build_nuts_maps``
    does — an entry wins over ``cs.net_pull`` only when the array length matches
    ``segs`` and the entry is not the ``INT_MIN`` sentinel — so a stale array
    (a later run_planner reselected a different-length topology) is ignored.

    Returns a list of ``(t, lo_rider, hi_rider)`` tuples: T's segment index and
    the two rider indices, ``lo_rider`` the lower-positioned (-pull) rider and
    ``hi_rider`` the higher (+pull).  One tuple per divergent (-,+) rider pair.
    """
    from buda import SegConnKind
    _INT_MIN = -2147483648
    _ov_ok = net_pull is not None and len(net_pull) == len(segs)

    def _eff_pull(i):
        if _ov_ok and net_pull[i] != _INT_MIN:
            return net_pull[i]
        return segs[i].net_pull

    pairs = []
    for t, seg in enumerate(segs):
        # Riders: SEG-conn neighbours, each at a junction position along T
        # (at_pos) and carrying its own net_pull (a direction along T).
        negs = []   # (at_pos, rider_idx) for riders pulling toward lower-along-T
        poss = []   # (at_pos, rider_idx) for riders pulling toward higher-along-T
        for c in seg.conns:
            if c.kind != SegConnKind.SEG:
                continue
            pull = _eff_pull(c.seg_idx)
            if pull < 0:
                negs.append((c.at_pos, c.seg_idx))
            elif pull > 0:
                poss.append((c.at_pos, c.seg_idx))
        # A -puller strictly BELOW a +puller diverges → stretches T.
        for (pn, ni) in negs:
            for (pp, pi) in poss:
                if pn < pp:
                    pairs.append((t, ni, pi))
    return pairs


def _batched(method):
    """Run a BDB-persist method inside ONE transaction (see BudaSession._bdb_batch).

    Every add_* row insert autocommits by default, so a bulk persist (1000s of
    rows) pays one WAL fsync per statement — this collapses them to a single
    commit (measured: generate_hier_topologies persist 23s -> ~1.7s on mix).
    Nestable: the C++ depth counter makes composing persist helpers (e.g.
    _persist_nuts -> _persist_planner_output) issue only one real BEGIN/COMMIT.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._bdb_batch():
            return method(self, *args, **kwargs)
    return wrapper


# ── wrapper invariant validator (risk_reduction_plan.md R1, Phase C) ─────────
# The mutable pybind surface means a bad Python-side write surfaces stages
# later; these are the cross-field invariants of the historical bug classes.
# Called at stage entries when BUDA_VALIDATE=1 (conftest turns it on for the
# whole test suite; zero-cost otherwise).

def validate_wrappers(bundles, where=""):
    """Return a list of invariant-violation strings (empty = clean)."""
    bad = []
    for w in bundles:
        bid = w.input.original_bundle.id
        ncand = len(w.input.candidates)
        sel = w.plan.selected_topology_index
        if not (-1 <= sel < ncand):
            bad.append(f"b{bid}: selected_topology_index {sel} out of "
                       f"range (ncand {ncand})")
            continue
        if sel >= 0 and w.plan.seg_layers:
            nseg = len(w.input.candidates[sel].segments)
            if len(w.plan.seg_layers) != nseg:
                bad.append(f"b{bid}: seg_layers len "
                           f"{len(w.plan.seg_layers)} != selected "
                           f"candidate's {nseg} segment(s)")
        # pinned_seg_layers WITHOUT a pin is legitimate (edit_commit forces
        # layers on the currently-selected candidate) — the historical
        # hazard is the SHAPE mismatch: forced layers surviving a re-select
        # onto a different-segment-count candidate (the unpin hazard).
        if w.input.pinned_seg_layers and sel >= 0:
            nseg = len(w.input.candidates[sel].segments)
            if len(w.input.pinned_seg_layers) != nseg:
                bad.append(f"b{bid}: pinned_seg_layers len "
                           f"{len(w.input.pinned_seg_layers)} != selected "
                           f"candidate's {nseg} segment(s) (the unpin "
                           f"hazard — forced layers applied to a "
                           f"different-shaped candidate)")
        for gi in (w.input.pinned_group or []):
            if not (0 <= gi < ncand):
                bad.append(f"b{bid}: pinned_group index {gi} out of range "
                           f"(ncand {ncand})")
    return [f"[validate{':' + where if where else ''}] {m}" for m in bad]


def _mixin_validate_stage_entry(self, where):
    """Session hook: run validate_wrappers at a stage entry when
    BUDA_VALIDATE=1 and fail LOUD (raise) on any violation — catching the
    bad write where it was made, not stages later."""
    import os
    if os.environ.get("BUDA_VALIDATE") != "1" or not self.bundles:
        return
    bad = validate_wrappers(self.bundles, where)
    if bad:
        raise RuntimeError("wrapper invariant violation(s):\n" +
                           "\n".join(bad))


# ── Unit consistency (Phase 1, docs/internal/lefdef_interface_plan.md) ──────
# The engine is UNIT-AGNOSTIC: `Point`/`Rect` are plain ints and nothing in
# topology/nuts/congestion_planner/routing_grid claims they are microns.  That
# is what makes a DBU-exact import cheap — but it also means NOTHING stops a
# design from mixing scales: block coordinates imported at one scale and a
# track pattern hand-declared at another.  The result is not a crash; it is a
# silently optimistic plan (buses reserving a fraction of the space they need)
# that looks feasible and means nothing.
#
# The crisp signal is TRACKS ACROSS THE DESIGN: the design's extent divided by
# a layer's track pitch.  It is a pure RATIO of two engine-unit lengths, so it
# is invariant under any consistent choice of unit — which is exactly why it
# detects an INCONSISTENT one.  A scale mismatch moves it by the scale ratio
# (~2000x for DBU-vs-micron), orders of magnitude outside anything buildable.
#
# The bounds below are deliberately far outside BOTH ends of the real range,
# because a guard that stops a legitimate run is worse than one that misses a
# subtle case.  Two references fix them:
#
#   measured   the tree's flows (2026-08, `BUDA_UNIT_SIGNAL=1`) span 3.66
#              (flow/four_blocks M7 — a 150-unit toy on a 41-unit pitch) to
#              797.2 (flow/chip/chip_topdown M2-M5) tracks across.  The
#              small end matters: the first calibration sampled only the QoR
#              corpus, whose minimum is 24.4, and a bound set from that broke
#              six legitimate unit-test fixtures.
#   physical   the widest reticle die (~33 mm) at the finest production metal
#              pitch (~28 nm) is ~1.2e6 tracks across — the physical ceiling.
#              At the other end, a design under HALF a track pitch across is
#              smaller than one wire of a layer it claims to route on.
#
# So MAX is ~8x past the physical ceiling and ~1e4x past anything measured;
# MIN is ~7x below the smallest measured design.  `set_unit_check off|warn`
# is the escape for the case we did not foresee — the fault text names it.
UNIT_TRACKS_MIN = 0.5
UNIT_TRACKS_MAX = 1.0e7

# The ratio signal has a HARD limit, and it is worth being explicit about it:
# a mis-scaled small design and a legitimate huge one produce the same number.
# A 2000x mismatch on a 720000-unit design reads 720000 tracks across — inside
# the physical ceiling, so the ratio check passes it.  No ratio can do better:
# without an absolute anchor there is nothing to compare against.
#
# There IS an anchor, but only sometimes.  A design that DECLARED an import
# scale (`set_import_scale`) has asserted that its layout units are physical:
# `unit_pitch / lu_per_um` is then a track pitch in real microns, and real
# metal pitches are bounded — roughly 20 nm at the finest production node up
# to ~10-20 µm for top-metal/redistribution.  So a declared-scale design gets
# a second, much sharper check.
#
# At the DEFAULT scale (1.0) it does not apply: "microns" there is nominal —
# every corpus design would fail a physical pitch test, and rightly, because
# nobody claimed those numbers were physical.  Declaring a scale is what turns
# the claim on.  Same principle as "no track pattern, no verdict": we judge
# only what the design actually asserted.
#
# Bounds ~10x outside the real range at both ends, for the same reason as
# above: a guard that stops a legitimate run is worse than one that misses.
UNIT_PITCH_UM_MIN = 0.005      # 5 nm — an order of magnitude below any node
UNIT_PITCH_UM_MAX = 500.0      # 0.5 mm — far past any redistribution layer


def unit_consistency_signals(fp, routing_grid, max_layer_id=32):
    """Return ([(layer_id, unit_pitch, tracks_across_extent)], extent).

    Pure read-only measurement, shared by the reporting and the guard so the
    number a user is shown is the number that was judged."""
    blocks = list(fp.get_all_blocks())   # (name, Rect) pairs
    if not blocks:
        return [], 0.0
    xs1 = []; xs2 = []; ys1 = []; ys2 = []
    for _name, b in blocks:
        xs1.append(b.x1); xs2.append(b.x2); ys1.append(b.y1); ys2.append(b.y2)
    extent = float(max(max(xs2) - min(xs1), max(ys2) - min(ys1)))
    out = []
    for lid in range(1, max_layer_id + 1):
        try:
            if not routing_grid.has_layer(lid):
                continue
            up = routing_grid.get_layer_grid(lid).global_pattern().unit_pitch()
        except Exception:
            continue
        if up and up > 0.0:
            out.append((lid, float(up), extent / float(up)))
    return out, extent


def min_bit_pitch(session, no_pattern=None):
    """Smallest per-bit pitch over all pattern layers (the densest layer a
    stub could land on) — eff_bus_width(1, 0.0, lid) is bit_pitch on a
    pattern layer and 0.0 otherwise.

    With no pattern layer at all there is no grid to derive from; the caller
    says what that means.  `no_pattern=None` (the default) falls back to the
    NUTS track pitch, which is what the bit-cap callers want — an approximate
    length scale beats refusing to cap.  `set_track_pitch auto` passes 0.0
    instead, because there the fallback would be the very literal it exists
    to replace, returned as if it had been derived.

    The grid-derived length scale: `set_max_bundle_bits auto` uses it to ask
    how many bits fit a block face, and `set_track_pitch auto` to size the
    inter-bus gap.  Shared here so both mean the same thing."""
    import buda
    pitches = []
    for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL):
        for lid in session.layers.get_layer_ids_by_dir(d):
            p = session.layers.eff_bus_width(1, 0.0, lid)
            if p > 0:
                pitches.append(p)
    if pitches:
        return min(pitches)
    return float(session._nuts_pitch or 1.0) if no_pattern is None \
        else no_pattern


def unit_plausibility_faults(signals, lu_per_um=1.0):
    """The subset of `unit_consistency_signals` rows outside the bounds, each
    with why it failed:
    `[(layer_id, unit_pitch, tracks, 'low'|'high'|'pitch_lo'|'pitch_hi')]`.

    `lu_per_um` is the design's declared import scale; at the default 1.0 the
    physical pitch check is skipped (nothing claimed those units were real).

    Separated from the measurement so the predicate is testable without a
    session, and from the reporting so one verdict drives both."""
    faults = []
    declared = lu_per_um and lu_per_um > 0.0 and lu_per_um != 1.0
    for lid, up, n in signals:
        if declared:
            pitch_um = up / lu_per_um
            if pitch_um < UNIT_PITCH_UM_MIN:
                faults.append((lid, up, n, "pitch_lo"))
                continue
            if pitch_um > UNIT_PITCH_UM_MAX:
                faults.append((lid, up, n, "pitch_hi"))
                continue
        if n < UNIT_TRACKS_MIN:
            faults.append((lid, up, n, "low"))
        elif n > UNIT_TRACKS_MAX:
            faults.append((lid, up, n, "high"))
    return faults
