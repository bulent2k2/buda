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

# ripup_reroute tuning (greedy hill-climb over topology selections).
_RR_MAX_CANDIDATES_PER_BUNDLE = 8   # alternate candidates tried per contender / iter
_RR_DEFAULT_MAX_ITER = 10           # outer-loop cap when no arg given

# Global-occupant pass (runs at a stall; wishlist-ripup "global-overlap
# re-route of NON-contended bundles").
_RR_GLOBAL_TOP_K = 3                # band occupants ranked per contention site
_RR_GLOBAL_MOVES_PER_OCC = 6        # index alternates tried per occupant
_RR_GLOBAL_MAX_TRIALS = 36          # hard trial budget per stall

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
