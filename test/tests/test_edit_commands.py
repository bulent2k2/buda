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


def test_unpin_topology_takes_select_topology_selectors():
    """The inverse of select_topology takes select_topology's SELECTOR: a
    net-name hint, id:N, net:PFX — not just the numeric id.  It accepted only
    the numeric form, so `unpin_topology d` failed on exactly the name
    `select_topology d 1` had just accepted (found by flow/tcl/design.tcl's
    prompt, whose pin/unpin verbs pass the same word through)."""
    s = _session()
    w = s.bundles[0]

    _quiet(s, "select_topology d 1")                  # pin by net-name hint
    assert w.input.topology_pinned is True
    out = _out(s, "unpin_topology d")                 # ...unpin by the same hint
    assert w.input.topology_pinned is False
    assert "Unpinned" in out

    _quiet(s, "select_topology d 1")
    _quiet(s, "unpin_topology net:d")                 # forced-hint spelling
    assert w.input.topology_pinned is False

    out = _out(s, "unpin_topology nosuch")            # a bad hint says so
    assert "no bundle whose first net starts with" in out


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


# ---------------------------------------------------------------------------
# Multi-rect tap-face selection (teg_multirect_status.md open 16):
# edit_add_stub used the block's UNION bbox for overlap + face, so on a
# multi-rect block the hand-built stub could land in the notch — a face that
# does not physically exist.  It now routes through generation's per-rect
# selection (best_rect, restricted to the rects the fixed target span can
# reach).  Measured pre-fix truth, recorded per the status doc: edit_status's
# verdict DID flag the notch landing (BUSTERM_FACEx1 on the L-shape repro
# below — stub at (200,400), a union face over the notch), so the shape was
# loud but the suggested geometry was still wrong; the no-rect-overlap TEG
# shape produced a floating gap stub the same way.
# ---------------------------------------------------------------------------

def _lshape_session(extra=()):
    """The Sec-1.1 L-shape as a multi-rect OVER block: tall arm x0..100 y0..400,
    wide base x0..400 y0..100 — union bbox (0,0)-(400,400), notch above the
    base for x>100."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 4 M4 H TOP 20",
           "def_layer 5 M5 V TOP 20",
           *extra,
           "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
           "add_block B 100 550 700 650",
           "add_bus d[4] L.p B.q",
           "run_bundler",
           "generate_topologies")
    return s


def test_edit_add_stub_multirect_taps_real_rect_face_not_union_notch():
    """H trunk above the block: the union face is y=400 across x0..400, but
    only the tall arm (x<=100) physically reaches it.  Pre-fix the stub landed
    at the overlap centre of the UNION (x=200 — the notch, BUSTERM_FACE);
    post-fix best_rect picks the arm (nearest perp face) and the stub taps its
    real top face, verdict clean."""
    s = _lshape_session()
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk H 500",
               "edit_add_stub L 0",
               "edit_status")
    assert "BUSTERM_FACE" not in out, out
    assert "violations: none" in out
    stub = s._edit_topo.segments[1]
    xs = sorted([stub.start.x, stub.end.x])
    ys = sorted([stub.start.y, stub.end.y])
    assert xs[0] == xs[1] and 0 <= xs[0] <= 100, (xs, "stub must be over the arm")
    assert ys == [400, 500], ys                    # arm top face -> trunk
    _quiet(s, "edit_abort")


def test_edit_add_stub_multirect_skips_rect_outside_target_span():
    """A trunk whose span misses the nearest rect: best_rect alone would pick
    the arm (perp face y=400, distance 100) but the trunk spans only
    x150..600, which the arm (x0..100) cannot reach — the choice is restricted
    to REACHABLE rects, so the stub taps the base's top face (y=100) at the
    overlap centre x=(150+400)/2=275.  Pre-fix: union overlap centre x=275
    with union face y=400 — floating in the notch."""
    s = _lshape_session()
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk H 500 150 600",
               "edit_add_stub L 0",
               "edit_status")
    assert "violations: none" in out, out
    stub = s._edit_topo.segments[1]
    assert stub.start.x == stub.end.x == 275
    assert sorted([stub.start.y, stub.end.y]) == [100, 500]
    _quiet(s, "edit_abort")


def test_edit_add_stub_multirect_gap_only_span_fails_loud():
    """Pure-TEG disjoint rects with the target span wholly inside the gap:
    the union bbox overlaps the span but NO rect does, so pre-fix the stub
    landed in the gap on the union face; now the command refuses with the
    no-overlap message and adds no segment."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 4 M4 H TOP 20",
           "def_layer 5 M5 V TOP 20",
           "add_block G rect 0 0 100 100 rect 300 0 400 100 teg_mode over",
           "add_block B 0 550 700 650",
           "add_bus d[4] G.p B.q",
           "run_bundler", "generate_topologies")
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk H 200 120 280",
               "edit_add_stub G 0")
    assert "do not overlap on the stub axis" in out, out
    assert len(s._edit_topo.segments) == 1          # trunk only, no notch stub
    _quiet(s, "edit_abort")


def test_edit_add_stub_multirect_honors_corner_margin_inset_rects():
    """Post-#835 the per-rect margin inset is what generation's best_rect
    reads; the edit stub must land on the INSET face like a generated tap
    (arm inset to (20,10)-(80,390) under corner_margin dx 20 dy 10)."""
    s = _lshape_session(extra=("corner_margin dx 20 dy 10",))
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk H 500",
               "edit_add_stub L 0",
               "edit_status")
    assert "violations: none" in out, out
    stub = s._edit_topo.segments[1]
    assert stub.start.x == stub.end.x == 50          # inset arm centre (20+80)/2
    assert sorted([stub.start.y, stub.end.y]) == [390, 500]   # inset top face
    _quiet(s, "edit_abort")


def test_edit_add_stub_single_rect_byte_identical():
    """Single-rect guard: the per-rect path has exactly one candidate (the
    physical bbox via bt_all_rects), so geometry and every failure message
    match the historical union-bbox computation."""
    s = _session()
    bid = s.bundles[0].input.original_bundle.id
    # Geometry: A is (0,0)-(200,400); H trunk at y=500 spanning full extent
    # -> overlap centre x=100, face y=400 (the historical formula).
    _quiet(s, f"edit_topology {bid} new",
           "edit_add_trunk H 500", "edit_add_stub A 0")
    stub = s._edit_topo.segments[1]
    assert stub.start.x == stub.end.x == 100
    assert sorted([stub.start.y, stub.end.y]) == [400, 500]
    # Failure messages, all three shapes, unchanged.
    out = _out(s, "edit_add_trunk H 200")            # seg 2: crosses A (y1<200<y2)
    out = _out(s, "edit_add_stub A 2")
    assert "target crosses the block (pass-through" in out
    out = _out(s, "edit_add_trunk H 400")            # seg 3: touches A's top face
    out = _out(s, "edit_add_stub A 3")
    assert "zero-length stub" in out
    out = _out(s, "edit_add_trunk V 900 550 650")    # seg 4: beside B, above A
    out = _out(s, "edit_add_stub A 4")
    assert "do not overlap on the stub axis" in out
    _quiet(s, "edit_abort")


def test_edit_add_stub_thru_pass_through_takes_precedence():
    """Codex P2 on #840 (fails pre-fix): a V trunk @x250 CROSSES the L-block's
    base while the arm is also reachable with a real face — under THRU the
    crossed rect already connects the whole block (its internal routing joins
    the rects; generation's best_rect picks the zero-cost crossed rect and
    emits no stub), so a stub to the arm is unnecessary external metal between
    equivalent terminals.  Pre-fix the loop discarded the crossed rect and
    emitted the arm stub (100,200)-(250,200); now the pass-through verdict
    takes precedence, matching the historical union-bbox refusal.  Same
    precedence for the TOUCH shape (trunk on a rect face)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 4 M4 H TOP 20", "def_layer 5 M5 V TOP 20",
           "add_block L rect 0 0 100 400 rect 0 0 400 100",   # thru (default)
           "add_block B 100 550 700 650", "add_bus d[4] L.p B.q",
           "run_bundler", "generate_topologies")
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk V 250 0 600",       # crosses the base interior
               "edit_add_stub L 0")
    assert "target crosses the block (pass-through, no stub needed)" in out, out
    assert len(s._edit_topo.segments) == 1         # trunk only, no arm stub
    out = _out(s, "edit_add_trunk H 400 0 700",    # seg 1: ON the arm's top face
               "edit_add_stub L 1")
    assert "zero-length stub (target touches the block face)" in out, out
    assert len(s._edit_topo.segments) == 2
    _quiet(s, "edit_abort")


def test_edit_add_stub_over_crossed_rect_still_stubs_unspanned_rect():
    """The OVER twin of the precedence test: OVER declares the rects NOT
    internally connected, so the crossed base connects only itself and the
    un-spanned arm still needs real metal — the edit stub to the arm is
    exactly the connector leg generation's 1(a) rectilinear branch emits for
    the Sec-1.1 TRUNK_V@x250 shape ((100,200)-(250,200)), and it is also the
    hand fix for the trunk-Direct-inside-ONE-disjoint-rect residual that
    generation leaves to TEG_OPEN."""
    s = _lshape_session()                          # teg_mode over
    bid = s.bundles[0].input.original_bundle.id
    out = _out(s, f"edit_topology {bid} new",
               "edit_add_trunk V 250 0 600",
               "edit_add_stub L 0",
               "edit_status")
    assert "stub added" in out and "violations: none" in out, out
    stub = s._edit_topo.segments[1]
    assert sorted([stub.start.x, stub.end.x]) == [100, 250]   # the 1(a) leg
    assert stub.start.y == stub.end.y == 200
    _quiet(s, "edit_abort")
