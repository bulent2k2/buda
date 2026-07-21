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

"""Phase E3b — the `.buda` TopoEdit command family.

edit_topology opens a transactional working copy (or an empty topology with
'new'); edit_add_trunk / edit_add_stub / edit_set_span / edit_connect /
edit_disconnect / edit_remove_segment mutate it (each printing its verdict);
edit_commit appends it to the bundle's pool by uid (the E4 user-candidate
entry point) and optionally pins it; edit_abort discards.  The committed
candidate must route through the real pipeline end-to-end.
"""
import contextlib
import io

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _out(session, *cmds):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 4 M4 H TOP 20",
           "def_layer 5 M5 V TOP 20",
           "add_block A 0 0 200 400",
           "add_block B 600 100 800 500",
           "add_bus d[8] A.p B.q",
           "run_bundler",
           "generate_topologies")
    return s


def test_unpin_topology_clears_pin_and_forced_layers():
    """unpin_topology is the inverse of the pin: it drops topology_pinned AND
    the forced pinned_seg_layers that `edit_commit pin` (after edit_set_layer)
    stores.  CongestionPlanner honors pinned_seg_layers for ANY candidate
    independent of topology_pinned, so leaving them set would keep forcing stale
    layers onto a re-chosen topology (Codex #390)."""
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    _quiet(s, f"edit_topology {bid} 1", "edit_set_layer 0 5", "edit_commit pin")
    assert w.input.topology_pinned is True
    assert len(w.input.pinned_seg_layers) > 0        # forced layers stored
    _quiet(s, f"unpin_topology {bid}")
    assert w.input.topology_pinned is False           # pin cleared
    assert list(w.input.pinned_seg_layers) == []      # forced layers cleared too


def test_scripted_user_topology_routes_end_to_end():
    """Build a hand topology purely from .buda commands, pin it, and run the
    full pipeline on it — the E3 loop closed at script level."""
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    n_before = len(w.input.candidates)

    out = _out(s,
               f"edit_topology {bid} new",
               "edit_add_trunk V 450",             # Hanan-channel column, full span
               "edit_add_stub A 0",
               "edit_add_stub B 0",
               "edit_status",
               "edit_commit pin")
    assert "session opened" in out
    assert "trunk added" in out and out.count("stub added") == 2
    assert "<< clean" in out, out                   # the final ops verdict is clean
    assert "committed as candidate" in out and "Pinned" in out

    assert len(w.input.candidates) == n_before + 1
    assert w.input.topology_pinned
    sel = w.plan.selected_topology_index
    assert sel == n_before
    assert w.input.candidates[sel].type == "USER"

    # The user candidate routes: planner honors the pin, NUTS places it.
    _quiet(s, "run_planner 3", "set_track_pitch 4", "run_nuts")
    assert s.nuts_result is not None
    placed = [t for t in s.nuts_result.segments if t.bundle_id == bid and t.placed]
    assert len(placed) == 3, f"expected the 3 user segments placed, got {len(placed)}"
    assert s.nuts_result.num_violations == 0


def test_edit_copy_modify_commit_is_new_candidate():
    """Editing a copy of an existing candidate never mutates the original;
    the commit lands as a new pool entry with a different uid."""
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    base_uids = [buda.topo_uid(c) for c in w.input.candidates]

    out = _out(s,
               f"edit_topology {bid} 1",
               "edit_set_span 0 0 650",
               "edit_commit")
    assert "committed as candidate" in out or "already in the pool" in out
    # Original candidate untouched.
    assert [buda.topo_uid(c) for c in w.input.candidates][:len(base_uids)] == base_uids


def test_session_guards():
    s = _session()
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, "edit_add_trunk H 100")
    assert "no edit session" in out
    out = _out(s, f"edit_topology {bid} new", f"edit_topology {bid} new")
    assert "already open" in out
    out = _out(s, "edit_commit")                    # empty topology
    assert "nothing to commit" in out
    out = _out(s, "edit_abort")
    assert "discarded" in out
    out = _out(s, "edit_status")                    # session closed again
    assert "no edit session" in out


def test_commit_dedups_identical_content():
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    _quiet(s, f"edit_topology {bid} new",
           "edit_add_trunk V 450", "edit_add_stub A 0", "edit_add_stub B 0",
           "edit_commit")
    n = len(w.input.candidates)
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk V 450", "edit_add_stub A 0", "edit_add_stub B 0",
               "edit_commit")
    assert "already in the pool" in out
    assert len(w.input.candidates) == n
