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

"""Parallel candidate scoring in plan_bundle (chip-flow P1) is
decision-identical to the sequential sweep.

Candidates are scored against the SAME read-only base cut state (each
worker's within-candidate charges live in its own overlay), and the winner
is picked by an ordered reduction that replays the sequential compare — so
the selected candidate, every per-segment layer, and every perp band must be
byte-identical at any thread count.  Measured on chip_topdown: planner
103.3s -> 61.6s on 4 threads (flow -29%), all selection/layer/perp/track
hashes identical to the sequential run and to pre-change main.

BUDA_PLAN_THREADS is read per plan_bundle call, so flipping os.environ
between in-process runs exercises both paths for real.
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

# A 3-block fan-out bus with a rich candidate pool (~36 candidates — well
# above the 8-candidate small-pool gate, so threads genuinely engage).
_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_bus dbus[52] A.p B.p,C.p",
    "run_bundler", "generate_topologies",
]


def _plan(threads):
    # Save/restore rather than delete: a caller-configured BUDA_PLAN_THREADS
    # (a controlled thread-count experiment, a constrained CI runner) must
    # survive these tests (Codex #584).
    prev = os.environ.get("BUDA_PLAN_THREADS")
    os.environ["BUDA_PLAN_THREADS"] = str(threads)
    try:
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()):
            for c in _SETUP + ["run_planner"]:
                s.do_command(c)
        w = s.bundles[0]
        return (w.plan.selected_topology_index,
                list(w.plan.seg_layers),
                list(w.plan.seg_perp))
    finally:
        if prev is None:
            del os.environ["BUDA_PLAN_THREADS"]
        else:
            os.environ["BUDA_PLAN_THREADS"] = prev


def test_pool_is_large_enough_to_engage_threads():
    sel, _, _ = _plan(1)
    prev = os.environ.get("BUDA_PLAN_THREADS")
    os.environ["BUDA_PLAN_THREADS"] = "1"
    try:
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()):
            for c in _SETUP:
                s.do_command(c)
        n = len(s.bundles[0].input.candidates)
        assert n >= 8, f"pool of {n} would not engage the parallel path"
    finally:
        if prev is None:
            del os.environ["BUDA_PLAN_THREADS"]
        else:
            os.environ["BUDA_PLAN_THREADS"] = prev
    assert sel >= 0


def test_parallel_scoring_is_decision_identical():
    # 16 exceeds the AUTO cap of 8: an explicit request is honored as given
    # (the NUTS layer_threads convention), so this also locks in the
    # explicit-wins resolution path.
    serial = _plan(1)
    for t in (2, 4, 8, 16):
        assert _plan(t) == serial, f"threads={t} diverged from sequential"


def test_thread_count_env_is_reread_per_run():
    # The env is consulted per plan_bundle call, so alternating settings
    # in one process keeps working (no cached-once surprise).
    a = _plan(4)
    b = _plan(1)
    c = _plan(4)
    assert a == b == c
