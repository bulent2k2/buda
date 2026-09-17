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
                     "run_detailed_nuts")):
    """The two-instance vehicle solved bottom-up under the reservation, with
    `pre` declared before bundling (where a sourced plan goes)."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
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
