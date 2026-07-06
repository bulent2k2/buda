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

"""Phase 0 resume leg (docs/internal/topo_conn_unification.md §10).

Checkpoint -> reopen -> load_pipeline -> continue, asserting:

1. **Analysis equivalence across the BDB round-trip**: every reloaded
   candidate's full derived analysis (all ConnSeg fields, ordered conns) is
   byte-identical to its in-memory twin from the original session — the
   serialization is tools/topo_snapshot.py's, i.e. the same bytes the golden
   gate freezes.  This extends test_seg_busterm_persist.py's "reloaded topology
   drives ConnTopology/check_topo identically" to the complete analysis, and is
   the invariant the unification's cache (Phase B) must keep intact: a
   rehydrated topology computes its analysis exactly like a fresh one.

   edge_id is excluded from the comparison: it is documented as not persisted
   (topology.h — TopoSegRow stores only x/y/layer/is_jog), the round-trip gap
   Phase E1 closes.  When E1 lands, drop include_edge_id=False here.

2. **Resume determinism**: two independent resumed sessions continuing with the
   same run_nuts produce identical route_snapshot fingerprints and row counts.
   (The ORIGINAL session's fingerprint may legitimately differ from a resumed
   one: seg_perp — the planner's charged-band placement preference — is
   deliberately not persisted, so a resumed run_nuts may place tracks
   differently.  What must hold is that resuming is deterministic, and that the
   bus/via row COUNTS match the original — the same segments get routed.)
"""
import contextlib
import io

import buda_cli
import topo_snapshot as ts

PITCH = 4


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


_SETUP = (
    "source flow/rnr/mix_tracks.buda",
    "add_block A 0 0 200 400",
    "add_block B 300 600 500 900",
    "add_block M rect 900 0 980 400 rect 1020 0 1100 400 teg_mode over",
)


def _fresh_session(bdb_path):
    """Original session: bundle + generate (checkpointing to the BDB)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb_path}", *_SETUP,
           "add_bus d[8] A.p M.p",
           "add_bus e[4] A.q B.r",
           "run_bundler",
           "generate_topologies")
    return s


def _resumed_session(bdb_path):
    """A brand-new session resuming from the checkpoint: setup re-declared,
    then load_pipeline rehydrates bundles + candidates + plan."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb_path}", *_SETUP, "load_pipeline")
    return s


def _snap(s):
    # edge_id excluded: documented round-trip gap (see module docstring).
    return ts.snapshot_session(s, include_edge_id=False)


def _route_row(s):
    snap = s.bdb.route_snapshot()
    return (snap.hash, snap.n_bus_segments, snap.n_bus_vias)


def test_reloaded_analysis_matches_in_memory(tmp_path):
    """The full derived analysis survives the checkpoint byte-for-byte."""
    path = str(tmp_path / "resume.bdb")
    s1 = _fresh_session(path)
    baseline = _snap(s1)         # post-generation, pre-planner: pure checkpoint
    assert baseline.count("bundle ") >= 2
    del s1

    s2 = _resumed_session(path)
    assert _snap(s2) == baseline, \
        "reloaded candidates' analysis deviates from the in-memory original"


def test_resume_continue_is_deterministic(tmp_path):
    """Two independent resumes continuing plan+NUTS route identically, and
    route the same segment/via population as the original session."""
    path = str(tmp_path / "resume.bdb")
    s1 = _fresh_session(path)
    _quiet(s1, "run_planner 3", f"set_track_pitch {PITCH}", "run_nuts")
    orig = _route_row(s1)
    del s1

    rows = []
    for _ in range(2):
        s = _resumed_session(path)
        _quiet(s, f"set_track_pitch {PITCH}", "run_nuts")
        rows.append(_route_row(s))
        del s
    assert rows[0] == rows[1], f"resume is nondeterministic: {rows}"
    # Same routed population as the original (positions may differ: seg_perp
    # is deliberately not persisted — see module docstring).
    assert rows[0][1:] == orig[1:], \
        f"resumed run_nuts routed a different population: {rows[0]} vs {orig}"
