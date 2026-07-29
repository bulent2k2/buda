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

"""Per-layer solver threads inside an orientation-group sweep (rnr P3).

Layers of one orientation group are independent (alignment lookups are
same-layer, junction preferences read only perpendicular partners,
occupancy/keepouts/constraints are per layer), so the group's solve_layer
calls may run on worker threads with results identical to the sequential
loop.  Auto mode gates on the group's parallelizable work remainder (the
work outside its heaviest layer), which affects speed only; an explicit
BUDA_NUTS_THREADS > 1 bypasses the gate — exactly what these tests use to
force the threaded path on a small design and pin its identity.
"""

import contextlib
import io

import buda_cli


def _build_and_solve(monkeypatch, threads):
    """A multi-layer congested design solved through run_nuts with the
    given BUDA_NUTS_THREADS setting; returns the placed segments."""
    if threads is None:
        monkeypatch.delenv("BUDA_NUTS_THREADS", raising=False)
    else:
        monkeypatch.setenv("BUDA_NUTS_THREADS", str(threads))
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 6 M6 H TOP 50",
            "def_layer 5 M5 V TOP 50", "def_layer 7 M7 V TOP 50"]
    for r in range(4):
        for c in range(4):
            x, y = c * 300, r * 300
            cmds.append(f"add_block b{r}_{c} {x} {y} {x+100} {y+100}")
    # Enough crossing buses that overlaps persist into the orientation
    # sweeps (the fixpoint only iterates while best_ov > 0).
    pairs = [(0, 0, 3, 3), (0, 3, 3, 0), (1, 0, 1, 3), (2, 3, 2, 0),
             (0, 1, 3, 1), (0, 2, 3, 2), (1, 1, 2, 2), (2, 1, 1, 2),
             (3, 0, 0, 0), (3, 3, 0, 3), (1, 3, 2, 0), (2, 2, 0, 1)]
    for i, (r1, c1, r2, c2) in enumerate(pairs):
        cmds.append(f"add_bus t{i}[8] b{r1}_{c1}.p b{r2}_{c2}.p")
    cmds += ["run_bundler", "generate_topologies", "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s.nuts_result


def _placement(nr):
    return sorted((ts.bundle_id, ts.seg_idx, ts.layer, ts.placed,
                   round(ts.track_position, 6) if ts.placed else None,
                   round(ts.span_lo, 6), round(ts.span_hi, 6))
                  for ts in nr.segments)


def test_threaded_group_solve_matches_sequential(monkeypatch):
    """BUDA_NUTS_THREADS=4 (explicit — bypasses the size gate, so the
    threaded path runs even on this small design) must place every segment
    exactly as the sequential solve does."""
    seq = _build_and_solve(monkeypatch, 1)
    par = _build_and_solve(monkeypatch, 4)
    assert _placement(par) == _placement(seq)
    assert par.num_overlaps == seq.num_overlaps
    assert par.num_violations == seq.num_violations


def test_auto_mode_matches_sequential(monkeypatch):
    """Auto (unset env): whether or not the size gate engages threads,
    the result must equal the sequential solve."""
    seq = _build_and_solve(monkeypatch, 1)
    auto = _build_and_solve(monkeypatch, None)
    assert _placement(auto) == _placement(seq)
    assert auto.num_overlaps == seq.num_overlaps
