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

"""The top's plan handed down (convergence ladder item 6c):
`derive_top_plan` and `pin_plan`.

E5 measured why the informed loop had no fixpoint: the informed round
re-plans the top from scratch after the templates moved, so 4 of 13
top-level topologies and no seat survived between the round a reservation
was derived from and the round that routed under it.  A track preference
(6b) could not fix that; a pin can.  What is pinned here:

  * the derivation writes one `pin_plan` line per globally planned bundle
    — the candidate by content uid AND type spec, the planner's layers,
    the abstract seat as a width-wide window — and NOT the bottom-up
    templates' bundles (solved in their own frame under the budget);
  * a second session sourcing the lines BEFORE bundling holds them until
    `run_planner hier` and then routes the top IDENTICALLY: same
    candidate, layers, seat and bit tracks; `dump_pins` shows the pin;
  * a seat is a HARD window: a doctored seat inside the slide range moves
    the bus there, one outside it is reported as not honoured (never
    silently re-seated);
  * `unpin_topology` frees the selection, the layers and the seats
    together; a template's bundle, an unknown bundle and a malformed line
    are each said.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda  # noqa: E402
import buda_cli  # noqa: E402

from test_layer_demand import _DESIGN, _cmd  # noqa: E402
from test_cell_layer_reserve import _LINE  # noqa: E402


def _run(*pre, tail=("run_nuts", "check_template_tracks on_mismatch independent",
                     "run_detailed_nuts"), script_path=None):
    """The two-instance vehicle solved bottom-up under the reservation, with
    `pre` declared before bundling (where a sourced plan goes); `script_path`
    names the flow a selections sidecar would sit beside."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
    if script_path:
        s.script_path = str(script_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*_DESIGN[:i], "set_bottom_up top_cell", _LINE, *pre,
                  *_DESIGN[i:], *tail]:
            s.do_command(c)
    return s, buf.getvalue()


def _top(s):
    """The one cross-instance bundle's (uid, layers, seats, type)."""
    w = [w for w in s.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    sel = w.plan.selected_topology_index
    seats = sorted((t.seg_idx, t.layer, t.track_position)
                   for t in s.nuts_result.segments if t.bundle_id == bid)
    return (buda.topo_uid(w.input.candidates[sel]), list(w.plan.seg_layers),
            seats, w.input.candidates[sel].type)


def _bits(s):
    w = [w for w in s.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    return sorted((n.seg_idx, n.bit_index, n.layer, n.track_position)
                  for n in s.detailed_result.net_segments
                  if n.bundle_id == bid)


def test_the_derivation_hands_down_the_top_and_not_the_templates(tmp_path):
    a, _ = _run()
    plan = tmp_path / "plan.buda"
    out = _cmd(a, f"derive_top_plan file {plan}")
    assert "1 bundle(s) handed down (1 segment(s), 1 seated)" in out
    assert "2 bottom-up copy/copies not handed down" in out
    text = plan.read_text()
    assert "# scope: top_cell" in text and "# bundles: 1" in text
    lines = [l for l in text.splitlines() if l.startswith("pin_plan ")]
    assert len(lines) == 1
    uid, layers, seats, ttype = _top(a)
    (si, lid, pos), = seats
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    ts, = [t for t in a.nuts_result.segments
           if t.bundle_id == w.input.original_bundle.id]
    lo, hi = pos - ts.width / 2, pos + ts.width / 2
    assert lines[0] == (f"pin_plan net:x_0 {ttype} uid {uid} layers M6 "
                        f"seats {lo:g}:{hi:g}"), lines[0]
    # an unknown token, an empty cells list, and no NUTS result are refused
    assert "unknown token 'bogus'" in _cmd(a, "derive_top_plan bogus")
    assert "names no cell" in _cmd(a, "derive_top_plan cells ,")
    fresh = buda_cli.BudaSession()
    fresh.no_viz = True
    assert "needs a NUTS result" in _cmd(fresh, "derive_top_plan")


def test_a_session_sourcing_the_plan_routes_the_top_identically(tmp_path):
    a, _ = _run()
    plan = tmp_path / "plan.buda"
    _cmd(a, f"derive_top_plan file {plan}")
    b, log = _run(f"source {plan}")
    # held before bundling, applied at the planner, honoured by NUTS
    assert "pin_plan: net:x_0 held until run_planner" in log
    assert "[PlanPin] 1 of 1 handed-down plan(s) applied (1 seat(s) pinned)" \
        in log
    assert "[PlanPin] seated 1 of 1 handed-down seat(s)" in log
    assert "Pinned bundle" in log and "[TopoSpec]" not in log   # by uid
    assert _top(a) == _top(b)
    assert _bits(a) == _bits(b) and len(_bits(a)) == 8
    # the seat is pinned by a width-wide window, and the bits keep the
    # segment's NATURAL window — the source's own — not the pinned one
    # (TrackSegment.seat_nat, the stage-4 -> stage-9 handoff's window)
    ta = [t for t in a.nuts_result.segments if t.bundle_id ==
          [w for w in a.bundles if not w.input.original_bundle.instances][0]
          .input.original_bundle.id][0]
    tb = [t for t in b.nuts_result.segments if t.bundle_id ==
          [w for w in b.bundles if not w.input.original_bundle.instances][0]
          .input.original_bundle.id][0]
    assert ta.seat_nat_lo != ta.seat_nat_lo                # NaN: no pin
    assert (tb.interval_lo, tb.interval_hi) == (tb.track_position - tb.width / 2,
                                                tb.track_position + tb.width / 2)
    assert (tb.seat_nat_lo, tb.seat_nat_hi) == (ta.interval_lo, ta.interval_hi)
    pins = _cmd(b, "dump_pins")
    assert "(x_0) -> topo" in pins and "layers[M6] seats[1 of 1] (pin_plan)" \
        in pins
    # the Tcl bridge's query: entries applied seated of
    sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
    import buda_server
    assert buda_server._QUERIES["plan_pins"](b) == "1 1 1 1"
    assert buda_server._QUERIES["plan_pins"](a) == "0 0 -1 -1"
    # unpin frees the selection, the layers AND the seats together
    assert "Unpinned bundle" in _cmd(b, "unpin_topology x")
    w = [w for w in b.bundles if not w.input.original_bundle.instances][0]
    assert not w.input.topology_pinned
    assert list(w.input.pinned_seg_layers) == []
    assert list(w.plan.seg_slide_lo) == [] and list(w.plan.seg_slide_hi) == []


def test_a_seat_is_a_hard_window_honoured_or_reported():
    a, _ = _run()
    uid, layers, seats, ttype = _top(a)
    (_, _, pos), = seats
    # inside the slide range: the bus moves there (a pin, not a preference)
    c, log = _run(f"pin_plan net:x_0 {ttype} uid {uid} layers M6 seats 50:84",
                  tail=("run_nuts",))
    assert "[PlanPin] seated 1 of 1" in log
    assert _top(c)[2] == [(0, 6, 67.0)] and pos != 67.0
    # outside it: NUTS cannot, and says which seat and where it landed
    d, log = _run(f"pin_plan net:x_0 {ttype} layers M6 seats 900:934",
                  tail=("run_nuts",))
    assert "1 by type spec rather than uid" in log        # no uid given
    assert "[PlanPin] seated 0 of 1 handed-down seat(s); 1 not honoured:" \
        in log
    assert "net:x_0 seg 0: wanted 917, placed " in log
    assert d.nuts_result.num_violations >= 1


def test_the_lines_that_cannot_apply_are_each_said():
    _, log = _run("pin_plan net:loc_0 Z_HVH layers M4,M5,M4",   # a template's
                  "pin_plan net:nosuch I_H",                    # unknown
                  "pin_plan net:x_0 I_H layers M6,M5 seats 133:167,-",  # shape
                  "pin_plan 2 I_H seats 1:0",                   # malformed
                  "pin_plan net:x_0",                           # too short
                  tail=("run_nuts",))
    assert ("pin_plan: net:loc_0 is a bottom-up template's bundle (cell "
            "top_cell)") in log
    assert "Error: pin_plan net:nosuch: no bundle whose first net is 'nosuch'" \
        in log
    assert ("Warning: pin_plan net:x_0: 2 layer/seat entries for a 1-segment "
            "candidate") in log
    assert "Error: pin_plan: seat '1:0' has hi < lo" in log
    assert "Error: pin_plan requires <bundle selector> <type-spec>" in log
    assert "[PlanPin] 1 of 3 handed-down plan(s) applied (0 seat(s) pinned" \
        in log
    # the shape-mismatched line pinned the selection only: no seat to audit
    assert "[PlanPin] seated" not in log


def test_a_pin_typed_after_the_plan_applies_at_once():
    a, _ = _run(tail=("run_nuts",))
    uid, layers, seats, ttype = _top(a)
    out = _cmd(a, f"pin_plan net:x_0 {ttype} uid {uid} layers M6 seats 50:84")
    assert "Pinned bundle" in out and "[PlanPin] 1 of 1" in out
    assert "held until run_planner" not in out
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    assert w.input.topology_pinned and list(w.plan.seg_slide_lo) == [50.0]
    _cmd(a, "run_nuts")
    assert _top(a)[2] == [(0, 6, 67.0)]


# An UNMARKED second cell beside the reserved one: its cell-local bundles
# are planned globally PER INSTANCE (expanded wrappers), so the plan carries
# one entry per instance and each must reach its own wrapper.
_SIDE = [
    "add_cell side 600 200",
    "add_inst_to_cell side p leaf 20 60",
    "add_inst_to_cell side q leaf 300 60",
    "add_inst s1 side - 50 400",
    "add_inst s2 side - 900 400",
]
_SIDE_BUSES = ["add_bus sl[8] s1/p.out s1/q.in", "add_bus sl2[8] s2/p.out s2/q.in"]


def _run_side(*pre, tail=("run_nuts",)):
    i_cell = _DESIGN.index("add_inst u1 top_cell - 50 50")
    i_bus = _DESIGN.index("run_hier_bundler depth 1")
    design = (_DESIGN[:i_cell] + _SIDE + _DESIGN[i_cell:i_bus]
              + _SIDE_BUSES + _DESIGN[i_bus:])
    i = design.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*design[:i], "set_bottom_up top_cell", _LINE, *pre,
                  *design[i:], *tail]:
            s.do_command(c)
    return s, buf.getvalue()


def _seats_by_net(s):
    out = {}
    for w in s.bundles:
        b = w.input.original_bundle
        nets = b.get_net_names()
        if not nets:
            continue
        out[nets[0]] = sorted(
            (t.seg_idx, t.layer, t.track_position)
            for t in s.nuts_result.segments if t.bundle_id == b.id)
    return out


def test_per_instance_plans_reach_their_own_wrappers_after_expansion(tmp_path):
    """Codex P1 on #939: an unmarked cell's cell-local bundles are planned
    per instance, so the plan carries one entry per instance — and before
    expansion only the template and its replicas exist, so a pin there
    would broadcast one instance's selection to every instance or land on
    a replica expansion drops.  Such entries are held for the
    post-expansion pass and pinned onto each instance's own wrapper."""
    a, _ = _run_side()
    plan = tmp_path / "plan.buda"
    out = _cmd(a, f"derive_top_plan file {plan}")
    assert "3 bundle(s) handed down" in out, out
    lines = [l for l in plan.read_text().splitlines() if l.startswith("pin_plan ")]
    assert sorted(l.split()[1] for l in lines) == ["net:sl2_0", "net:sl_0", "net:x_0"]
    b, log = _run_side(f"source {plan}")
    assert ("[PlanPin] 1 of 3 handed-down plan(s) applied (1 seat(s) pinned, "
            "2 per-instance, applied after expansion)") in log, log[-3000:]
    assert "[PlanPin] 2 per-instance plan(s) applied after expansion" in log
    assert "(its own instance)" in log
    assert "[PlanPin] seated 3 of 3 handed-down seat(s)" in log, log[-3000:]
    sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
    import buda_server
    assert buda_server._QUERIES["plan_pins"](b) == "3 3 3 3"
    sa, sb = _seats_by_net(a), _seats_by_net(b)
    for net in ("x_0", "sl_0", "sl2_0"):
        assert sa[net] == sb[net], (net, sa[net], sb[net])
    # per instance, really: doctor s2's seat alone (a pitch up, inside its
    # window) and only s2's bus moves
    doctored = []
    for l in plan.read_text().splitlines():
        if l.startswith("pin_plan net:sl2_0 "):
            head, seats = l.rsplit(" seats ", 1)
            lo, hi = (float(v) for v in seats.split(":"))
            l = f"{head} seats {lo + 4.25:g}:{hi + 4.25:g}"
        doctored.append(l)
    plan2 = tmp_path / "plan2.buda"
    plan2.write_text("\n".join(doctored) + "\n")
    c, log = _run_side(f"source {plan2}")
    assert "[PlanPin] seated 3 of 3" in log, log[-3000:]
    sc = _seats_by_net(c)
    assert sc["sl_0"] == sa["sl_0"] and sc["x_0"] == sa["x_0"]
    assert sc["sl2_0"] != sa["sl2_0"]
    assert sc["sl2_0"][0][2] == sa["sl2_0"][0][2] + 4.25


def _flow_lines(path, upto):
    lines = []
    for l in path.read_text().splitlines():
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        lines.append(l)
        if l.split()[0] == upto:
            break
    return lines


def test_the_natural_window_carries_the_partner_reach_prune(tmp_path):
    """Codex P1 on #939: `seat_nat` was cut before
    `prune_unreachable_partner_windows`, so a seat pin's bit window could
    admit tracks the source round's final window had excluded.  On the
    vehicle where the prune fires, every handed-down seat's natural window
    now equals the source's own final window, and the seats and bits
    reproduce."""
    flow = Path(__file__).parents[2] / "flow" / "keepout_blocks_partner_reach.buda"
    lines = _flow_lines(flow, "run_detailed_nuts")
    root = flow.parent
    lines = [("source " + str(root / l.split()[1]) if l.startswith("source ") else l)
             for l in lines]

    def run(extra):
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in extra + lines:
                s.do_command(c)
        return s, buf.getvalue()

    a, log_a = run([])
    assert "seat window(s) pruned" in log_a, log_a[-2000:]   # the prune fires
    plan = tmp_path / "plan.buda"
    _cmd(a, f"derive_top_plan file {plan}")
    b, log_b = run([f"source {plan}"])
    assert "[PlanPin] seated" in log_b and "not honoured" not in log_b, log_b[-2000:]
    ta = {(t.bundle_id, t.seg_idx): t for t in a.nuts_result.segments}
    tb = {(t.bundle_id, t.seg_idx): t for t in b.nuts_result.segments}
    assert ta.keys() == tb.keys()
    for k, t in tb.items():
        assert t.track_position == ta[k].track_position, k
        assert (t.seat_nat_lo, t.seat_nat_hi) == (ta[k].interval_lo, ta[k].interval_hi), k
    bits = lambda s: sorted((n.bundle_id, n.seg_idx, n.bit_index, n.layer, n.track_position)
                            for n in s.detailed_result.net_segments)
    assert bits(a) == bits(b) and a.detailed_result.num_unplaced == b.detailed_result.num_unplaced


def test_the_natural_window_reads_the_partners_natural_window_too(tmp_path):
    """Codex P1 on #939, second round: the prune's natural verdict used the
    PARTNER's pinned width-wide window, which "cannot slide clear" of a zone
    its natural window steps past — so a pinned stub cut the trunk's natural
    window where the source round, judging the stub by its own window, had
    not.  An M7 keepout covering the stub's pinned window (x 710..737) but
    not its natural one (710..790), lying under the trunk's seat inside the
    trunk's window: the source keeps [110, 190], and so must the replay
    (it read [150, 190] before)."""
    tracks = str(Path(__file__).parents[2] / "flow" / "tracks" / "tracks.buda")
    lines = [f"source {tracks}", "corner_margin dx 10 dy 10",
             "add_keepout 705 120 745 150 7",
             "add_block drv 100 100 200 200", "add_block rcv 700 600 800 700",
             "add_bus b[4] drv.tx rcv.rx", "run_bundler strict",
             "generate_topologies", "select_topology 1 L_HV", "run_planner",
             "run_nuts"]

    def run(extra):
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in extra + lines:
                s.do_command(c)
        return s, buf.getvalue()

    a, log_a = run([])
    assert "pruned" not in log_a                     # the stub slides clear
    ta = {t.seg_idx: t for t in a.nuts_result.segments}
    assert (ta[0].interval_lo, ta[0].interval_hi) == (110.0, 190.0)
    assert (ta[1].interval_lo, ta[1].interval_hi) == (710.0, 790.0)
    plan = tmp_path / "plan.buda"
    _cmd(a, f"derive_top_plan file {plan}")
    b, log_b = run([f"source {plan}"])
    assert "[PlanPin] seated 2 of 2" in log_b, log_b[-2000:]
    tb = {t.seg_idx: t for t in b.nuts_result.segments}
    assert abs(tb[1].interval_hi - tb[1].interval_lo - tb[1].width) < 1e-9   # the pin
    assert (tb[0].seat_nat_lo, tb[0].seat_nat_hi) == (110.0, 190.0)
    assert (tb[1].seat_nat_lo, tb[1].seat_nat_hi) == (710.0, 790.0)


def test_unpin_all_frees_the_plans_seats_and_bookkeeping(tmp_path):
    """Codex P2 on #939: `unpin_topology *` has its own loop; it must drop
    the seat windows a plan set (or the freed bundle stays seat-bound on
    the next run_nuts), forget the bids, and stop counting the entries as
    applied — and a later run_planner must not re-apply what the user
    unpinned."""
    a, _ = _run()
    plan = tmp_path / "plan.buda"
    _cmd(a, f"derive_top_plan file {plan}")
    b, _ = _run(f"source {plan}")
    sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
    import buda_server
    assert buda_server._QUERIES["plan_pins"](b) == "1 1 1 1"
    assert "Unpinned all bundles" in _cmd(b, "unpin_topology *")
    w = [w for w in b.bundles if not w.input.original_bundle.instances][0]
    assert list(w.plan.seg_slide_lo) == [] and list(w.plan.seg_slide_hi) == []
    assert not b._plan_pin_bids
    assert buda_server._QUERIES["plan_pins"](b) == "1 0 0 0"
    out = _cmd(b, "run_planner hier 3")
    assert "Pinned bundle" not in out and "[PlanPin]" not in out
    assert not w.input.topology_pinned
    _cmd(b, "run_nuts")
    assert "[PlanPin] seated" not in _cmd(b, "run_nuts")


# ── Codex round 3 on #939 ────────────────────────────────────────────────

def test_a_users_own_window_is_not_a_seat_pin():
    """Codex P1 on #939: a seat pin was inferred from the override's WIDTH,
    so an `edit_set_slide` / explorer override exactly the segment's width
    also handed the bits the natural window — outside the window the
    command documents as a NUTS constraint.  A seat pin is what `pin_plan`
    FLAGS (`plan.seg_seat_pin`); a user's window of the same width bounds
    the bits too."""
    a, _ = _run(tail=("run_nuts",))
    uid, layers, seats, ttype = _top(a)
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    # The user's own window, exactly a seat's width, through the edit
    # session (what edit_commit writes: the window with NO seat flag).
    _cmd(a, f"edit_topology {bid} {w.plan.selected_topology_index + 1}")
    # (a seat's width, inside the segment's slide range [110, 190])
    assert "applies at edit_commit" in _cmd(a, "edit_set_slide 0 120 154")
    out = _cmd(a, "edit_commit pin")
    assert "Applied 1 slide window(s)" in out, out
    assert list(w.plan.seg_slide_lo) == [120.0] and \
        list(w.plan.seg_seat_pin) == []
    _cmd(a, "run_nuts")
    (ta,) = [t for t in a.nuts_result.segments if t.bundle_id == bid]
    assert (ta.interval_lo, ta.interval_hi) == (120.0, 154.0)
    assert ta.seat_nat_lo != ta.seat_nat_lo             # NaN: not a seat pin
    _cmd(a, "check_template_tracks on_mismatch independent")
    _cmd(a, "run_detailed_nuts")
    for _, _, _, pos in _bits(a):
        assert 120.0 <= pos <= 154.0, pos                 # the bits stay inside
    # The same window handed down by pin_plan IS a seat pin: flagged, and
    # the bits get the natural window.
    b, _ = _run(f"pin_plan net:x_0 {ttype} uid {uid} layers M6 seats 120:154",
                tail=("run_nuts",))
    wb = [w for w in b.bundles if not w.input.original_bundle.instances][0]
    assert list(wb.plan.seg_seat_pin) == [1]
    (tb,) = [t for t in b.nuts_result.segments if t.bundle_id == bid]
    assert (tb.interval_lo, tb.interval_hi) == (120.0, 154.0)
    assert tb.seat_nat_lo == tb.seat_nat_lo             # set
    assert tb.seat_nat_hi - tb.seat_nat_lo > tb.width
    # and unpinning forgets the flag with the window
    _cmd(b, "unpin_topology x_0")
    assert list(wb.plan.seg_seat_pin) == [] and list(wb.plan.seg_slide_lo) == []


def test_a_copy_carries_the_natural_window_with_its_seat():
    """Codex P2 on #939: `transform_track_segment` / `offset_track_segment`
    moved the interval, bounds and position into the copy's frame and left
    `seat_nat` in the source's, so a reflected or translated instance's
    bits were admitted from the wrong place."""
    ts = buda.TrackSegment()
    ts.bundle_id, ts.seg_idx, ts.layer = 7, 0, 6
    ts.horiz = True
    ts.span_lo, ts.span_hi = 100.0, 300.0
    ts.interval_lo, ts.interval_hi = 40.0, 74.0
    ts.track_position, ts.width = 57.0, 34.0
    ts.seat_nat_lo, ts.seat_nat_hi = 20.0, 120.0
    # translate: N at (0,0) -> N at (1000, 500): every y moves by 500
    n = buda.transform_track_segment(ts, "N", 400, 200, 0, 0, 1000, 500, 8)
    assert (n.interval_lo, n.interval_hi) == (540.0, 574.0)
    assert (n.seat_nat_lo, n.seat_nat_hi) == (520.0, 620.0)
    o = buda.offset_track_segment(ts, 1000, 500, 8)
    assert (o.seat_nat_lo, o.seat_nat_hi) == (520.0, 620.0)
    # mirror the perpendicular axis (FN: y -> 200 - y) at the same origin:
    # the ends swap and the window is re-ordered like the interval's
    s = buda.transform_track_segment(ts, "FN", 400, 200, 0, 0, 0, 0, 8)
    assert (s.interval_lo, s.interval_hi) == (126.0, 160.0)
    assert (s.seat_nat_lo, s.seat_nat_hi) == (80.0, 180.0)
    # no seat pin: NaN rides through both
    ts.seat_nat_lo = ts.seat_nat_hi = float("nan")
    n = buda.transform_track_segment(ts, "N", 400, 200, 0, 0, 1000, 500, 8)
    o = buda.offset_track_segment(ts, 1000, 500, 8)
    assert n.seat_nat_lo != n.seat_nat_lo and o.seat_nat_lo != o.seat_nat_lo


def test_the_plan_line_quotes_the_selector_whole():
    """Codex P2 on #939: a net name with whitespace was written
    `net:"foo bar"`, which the tokenizer (a quote counts only where a token
    BEGINS) splits in two, so the plan could not be replayed."""
    from buda_script import split_quoted_args
    line = buda_cli.BudaSession._top_plan_line(
        {"net": "foo bar", "type": "TRUNK_H@y100", "uid": "abc",
         "layers": ["M6"], "seats": [(50.0, 84.0)]})
    assert line.startswith('pin_plan "net:foo bar" TRUNK_H@y100 '), line
    toks = split_quoted_args(line)
    assert toks[:2] == ["net:foo bar", "TRUNK_H@y100"], toks
    # a name carrying whitespace AND a double quote takes the other
    # delimiter (`quote_arg`, the tokenizer's inverse — Codex round 4)
    line = buda_cli.BudaSession._top_plan_line(
        {"net": 'foo"bar baz', "type": "I_H", "uid": "abc",
         "layers": ["M6"], "seats": [None]})
    assert line.startswith("pin_plan 'net:foo\"bar baz' I_H "), line
    assert split_quoted_args(line)[:2] == ['net:foo"bar baz', "I_H"]
    # a plain name is unquoted, as before
    line = buda_cli.BudaSession._top_plan_line(
        {"net": "x_0", "type": "I_H", "uid": "abc", "layers": ["M6"],
         "seats": [None]})
    assert line == "pin_plan net:x_0 I_H uid abc layers M6 seats -", line


# ── Codex round 5 on #939 ────────────────────────────────────────────────

def test_a_dogleg_adopted_bundle_hands_down_its_pre_split_candidate(tmp_path):
    """Codex P1 on #939: NUTS's adopted dogleg is an appended, geometry-
    mutated copy of the selected candidate, so a line written from it named
    a uid no fresh pool holds and layers/seats indexing the split; the type
    spec then landed on the unsplit candidate and the segment-count guard
    dropped every layer and seat.  The pre-split candidate is handed down
    instead — its layers, every seat but the split trunk's."""
    import os
    flow = Path(__file__).resolve().parents[2] / "flow" / "dogleg2.buda"
    a = buda_cli.BudaSession()
    a.no_viz = True
    _cmd(a, f"source {flow}")
    assert a._dogleg_slot, "the vehicle adopts a dogleg"
    (bid,) = a._dogleg_slot
    w = [w for w in a.bundles if w.input.original_bundle.id == bid][0]
    orig = w.input.candidates[a._dogleg_originals[bid]]
    split = w.input.candidates[a._dogleg_slot[bid]]
    assert len(split.segments) == len(orig.segments) + 2
    plan = tmp_path / "plan.buda"
    out = _cmd(a, f"derive_top_plan file {plan}")
    assert "1 dogleg-adopted bundle(s) handed down as the pre-split " \
        "candidate" in out, out
    lines = [l for l in plan.read_text().splitlines()
             if l.startswith("pin_plan")]
    assert len(lines) == 3
    (dl,) = [l for l in lines if f"uid {buda.topo_uid(orig)}" in l]
    assert f"uid {buda.topo_uid(split)}" not in plan.read_text()
    toks = dl.split()
    layers = toks[toks.index("layers") + 1].split(",")
    seats = toks[toks.index("seats") + 1].split(",")
    nseg = len(orig.segments)
    assert len(layers) == nseg and len(seats) == nseg
    assert seats.count("-") == 1 and all(":" in x for x in seats
                                          if x != "-")
    # Replay: a fresh session sources the plan after generation, and every
    # line applies with its layers and seats (no segment-count fallback).
    setup = [l.strip() for l in flow.read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    setup = setup[:setup.index("generate_topologies") + 1]
    b = buda_cli.BudaSession()
    b.no_viz = True
    log = "".join(_cmd(b, c) for c in
                  [*setup, f"source {plan}", "run_planner 1"])
    assert "layer/seat entries for a" not in log, log
    assert "[PlanPin] 3 of 3 handed-down plan(s) applied" in log
    wb = [w for w in b.bundles if w.input.original_bundle.id == bid][0]
    assert buda.topo_uid(wb.input.candidates[wb.plan.selected_topology_index]) \
        == buda.topo_uid(orig)
    assert len(wb.input.pinned_seg_layers) == nseg
    # NUTS re-derives the dogleg from the same cycle (the selection is the
    # re-adopted split, its pre-split original the handed-down candidate)
    # and every handed-down seat is honoured.
    log = _cmd(b, "run_nuts")
    assert bid in b._dogleg_slot
    assert buda.topo_uid(wb.input.candidates[b._dogleg_originals[bid]]) \
        == buda.topo_uid(orig)
    n_seat = sum(1 for l in lines for x in
                 l.split()[l.split().index("seats") + 1].split(",") if x != "-")
    assert f"[PlanPin] seated {n_seat} of {n_seat}" in log, log


def test_a_sidecar_entry_cannot_clear_the_plans_layers(tmp_path):
    """Codex P2 on #939: the plan was applied BEFORE the sidecar baseline,
    whose entry for the same bundle keeps a pinned topology but clears the
    forced layers on its no-`seg_layers` path (and a USER entry would
    replace the topology under the plan's seats).  The plan is the later,
    explicit instruction and is applied after the baseline."""
    import json
    a, _ = _run(tail=("run_nuts",))
    uid, layers, seats, ttype = _top(a)
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    other = [c for c in w.input.candidates if buda.topo_uid(c) != uid][0]
    flow = tmp_path / "flow.buda"
    (tmp_path / "flow.json").write_text(json.dumps({"selections": [{
        "bundle_hint": "x_0", "bundle_id": bid, "topo_type": other.type,
        "topo_wl": other.estimated_wirelength,
        "topo_uid": buda.topo_uid(other), "topo_index_hint": 0,
        "note": "", "selected_at": "now"}]}))
    (_, _, pos), = seats
    lo, hi = pos - 17, pos + 17
    b, log = _run(f"pin_plan net:x_0 {ttype} uid {uid} layers M6 seats {lo:g}:{hi:g}",
                  tail=("run_nuts",), script_path=flow)
    assert "Pinned bundle" in log                      # the sidecar loaded
    wb = [w for w in b.bundles if not w.input.original_bundle.instances][0]
    assert buda.topo_uid(wb.input.candidates[wb.plan.selected_topology_index]) \
        == uid                                          # the plan's candidate
    assert list(wb.input.pinned_seg_layers) == [6]     # its layers intact
    assert list(wb.plan.seg_seat_pin) == [1]
    assert "[PlanPin] seated 1 of 1" in log


# ── Codex round 6 on #939 ────────────────────────────────────────────────

def test_negotiation_moving_a_plan_pinned_bundle_drops_its_seats():
    """Codex P1 on #939: negotiate's free-bundle path unpins and re-plans
    an affected bundle but left the plan's seat windows and seat-pin flags
    on the wrapper; on a same-segment-count alternative NUTS accepted them
    and seated the new shape in the old candidate's windows."""
    import time
    a, _ = _run(tail=("run_nuts",))
    uid, layers, seats, ttype = _top(a)
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    # Hand down a DIFFERENT candidate than the planner's own choice, with
    # a seat: the unpinned re-plan will leave it for the planner's.
    sel = w.plan.selected_topology_index
    other = next(i for i, c in enumerate(w.input.candidates) if i != sel)
    oc = w.input.candidates[other]
    seats = ",".join(["120:154"] + ["-"] * (len(oc.segments) - 1))
    b, log = _run(f"pin_plan net:x_0 {oc.type} uid {buda.topo_uid(oc)} "
                  f"seats {seats}", tail=("run_nuts",))
    wb = [w for w in b.bundles if not w.input.original_bundle.instances][0]
    assert wb.plan.selected_topology_index == other
    assert list(wb.plan.seg_seat_pin)[0] == 1
    # One negotiation iteration on that bundle alone (the body the command
    # runs per affected bundle): the re-plan moves it back ...
    with contextlib.redirect_stdout(io.StringIO()):
        b._negotiate_iteration_body([bid], "a", (), (), time.perf_counter())
    assert wb.plan.selected_topology_index != other
    # ... and the plan's per-segment state went with the old shape.
    assert list(wb.plan.seg_slide_lo) == [] and list(wb.plan.seg_slide_hi) == []
    assert list(wb.plan.seg_seat_pin) == []
    (tb,) = [t for t in b.nuts_result.segments if t.bundle_id == bid]
    assert tb.seat_nat_lo != tb.seat_nat_lo               # no seat pin now


def test_the_plan_is_reapplied_on_every_planner_run(tmp_path):
    """Codex P2 on #939: the sidecar baseline runs ahead of EVERY planner
    run, so applying the plan after it on the first run alone left the
    second run's baseline free to clear the plan's layers.  The planner's
    call re-applies every live entry; a later `select_topology` on the
    bundle supersedes its entry instead."""
    import json
    a, _ = _run(tail=("run_nuts",))
    uid, layers, seats, ttype = _top(a)
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    other = [c for c in w.input.candidates if buda.topo_uid(c) != uid][0]
    flow = tmp_path / "flow.buda"
    (tmp_path / "flow.json").write_text(json.dumps({"selections": [{
        "bundle_hint": "x_0", "bundle_id": bid, "topo_type": other.type,
        "topo_wl": other.estimated_wirelength,
        "topo_uid": buda.topo_uid(other), "topo_index_hint": 0,
        "note": "", "selected_at": "now"}]}))
    b, _ = _run(f"pin_plan net:x_0 {ttype} uid {uid} layers M6 seats 120:154",
                tail=("run_nuts",), script_path=flow)
    def top(s):    # expansion builds fresh wrappers: re-fetch after a plan
        return [w for w in s.bundles if not w.input.original_bundle.instances][0]
    log = _cmd(b, "run_planner hier") + _cmd(b, "run_nuts")
    wb = top(b)
    assert "[PlanPin] 1 of 1 handed-down plan(s) applied" in log
    assert buda.topo_uid(wb.input.candidates[wb.plan.selected_topology_index]) \
        == uid
    assert list(wb.input.pinned_seg_layers) == [6]
    assert list(wb.plan.seg_seat_pin) == [1]
    assert "[PlanPin] seated 1 of 1" in log
    # A typed select_topology moving the bundle is the user's later word:
    # the entry is superseded, and the next run does not put the plan back.
    oidx = [i for i, c in enumerate(wb.input.candidates)
            if buda.topo_uid(c) == buda.topo_uid(other)][0]
    out = _cmd(b, f"select_topology x_0 {oidx + 1}")
    assert "superseded by this pin" in out, out
    assert list(wb.plan.seg_slide_lo) == [] and list(wb.plan.seg_seat_pin) == []
    log = _cmd(b, "run_planner hier")
    assert "[PlanPin]" not in log
    assert top(b).plan.selected_topology_index == oidx
    assert any(e.get("why") == "superseded by select_topology"
               for e in b._plan_pins)


def test_a_hand_built_candidate_is_not_handed_down(tmp_path):
    """Codex P2 on #939: a USER candidate is in no fresh pool, so a line
    naming it could never apply and the top would silently re-plan; the
    derivation omits it and says so."""
    a, _ = _run(tail=("run_nuts",))
    w = [w for w in a.bundles if not w.input.original_bundle.instances][0]
    bid = w.input.original_bundle.id
    _cmd(a, f"edit_topology {bid} {w.plan.selected_topology_index + 1}")
    _cmd(a, "edit_set_slide 0 120 154")
    assert "type USER" in _cmd(a, "edit_commit pin")
    _cmd(a, "run_nuts")
    plan = tmp_path / "plan.buda"
    out = _cmd(a, f"derive_top_plan file {plan}")
    assert "1 bundle(s) on a hand-built USER candidate not handed down" in out
    assert "net:x_0" not in plan.read_text()
