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
