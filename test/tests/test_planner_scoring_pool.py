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

"""plan_bundle's scoring workers persist across calls (#972 lever B1).

Each scoring thread keeps its overlay — a value and a stamp per BAND of the
planner's grid — in a thread_local, so a thread that lives for the process
sizes it once per grid generation.  Fresh std::threads per call did not:
every worker re-created its overlay on every plan_bundle call (45 MB each at
the bigsoc NQ=8 dial, 266 MB at NQ=62), which made the 4-thread planner
slower than the 1-thread one.  `plan_overlay_inits()` counts those
(re)sizings; a multi-bundle plan on 4 threads must add at most one per
thread (the main thread's plus the pool's), not one per worker per call.
Decision identity across thread counts is `test_planner_parallel_scoring.py`'s.
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda  # noqa: E402
import buda_cli  # noqa: E402

# Several bundles with rich candidate pools (>= 8 candidates each, so the
# auto policy would thread them too; the explicit request makes it certain).
_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_block D 12000 500 13000 1500",
    "add_block E 6000 16000 7000 17000",
    "add_block F 15000 12000 16000 13000",
    "add_block G 3000 9000 4000 10000",
    # Twelve distinct driver/receiver signatures = twelve bundles, so a
    # per-call spawn (3 overlay re-creations per plan_bundle call) would
    # read >= 36 — above any pool a test in this process may have grown.
    "add_bus b01[52] A.p B.p,C.p",
    "add_bus b02[32] D.q A.q,E.q",
    "add_bus b03[16] B.r D.r,E.r",
    "add_bus b04[8] C.s E.s,D.s",
    "add_bus b05[24] F.a A.a,G.a",
    "add_bus b06[24] G.b F.b,C.b",
    "add_bus b07[12] A.c F.c,D.c",
    "add_bus b08[12] E.d G.d,B.d",
    "add_bus b09[20] B.e F.e,E.e",
    "add_bus b10[20] C.f G.f,A.f",
    "add_bus b11[16] D.g B.g,G.g",
    "add_bus b12[16] F.h C.h,E.h",
    "run_bundler", "generate_topologies",
]


def test_workers_keep_their_overlays_across_calls():
    prev = os.environ.get("BUDA_PLAN_THREADS")
    os.environ["BUDA_PLAN_THREADS"] = "4"
    try:
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()):
            for c in _SETUP:
                s.do_command(c)
            before = buda.plan_overlay_inits()
            s.do_command("run_planner")
            after = buda.plan_overlay_inits()
        n_bundles = len(s.bundles)
        assert n_bundles >= 12
        n_cands = sum(len(w.input.candidates) for w in s.bundles)
        assert n_cands >= 8 * n_bundles
        # One grid generation: at most one (re)size per thread that exists —
        # the main thread plus every pool thread (an earlier test in this
        # process may have grown the pool past this call's 3 workers, and
        # any of them may pick up a job) — however many plan_bundle calls the
        # pass made.  Per-call re-creation would read >= 3 * n_bundles.
        pool = buda.plan_scoring_pool_threads()
        assert pool >= 3
        assert after - before <= pool + 1, (before, after, pool, n_bundles)
        assert pool + 1 < 3 * n_bundles, "pool too large for the bound to bite"
        # A second pass is bounded the same way (a thread already sized for
        # this grid generation adds nothing; a re-generated grid at most one
        # per thread), never per call.
        before2 = buda.plan_overlay_inits()
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("run_planner")
        assert buda.plan_overlay_inits() - before2 <= pool + 1
    finally:
        if prev is None:
            del os.environ["BUDA_PLAN_THREADS"]
        else:
            os.environ["BUDA_PLAN_THREADS"] = prev
