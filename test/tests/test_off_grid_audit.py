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

"""OFF_GRID: `check_design` audits that every bit-wire is on its layer's grid.

Issue #947.  The routing grid was consulted when DetailedNUTS PLACED a bit and
never again, so `check_design` had no kind for metal the technology has no
track for — which is how #946's bottom-up copies, a whole core's bus sitting
in a GROUND slot, audited clean for the whole E1/E5 series while only the
independent judge (tools/independent_audit.py) saw them.

The rule is the metal's EDGES, not its centre: the extent [pos - w/2,
pos + w/2] must start at the low edge of a SIGNAL slot and end at the high
edge of one, with no non-SIGNAL slot between.  A plain bit is a run of one;
an NDR bit of width_slots k is a run of k, centred BETWEEN slot centres when
k is even — so a centre rule, the judge's, calls every such wire off-grid
(#954).  How MANY slots a governed bit should cover stays NDR_WIDTH's
question.

Measured on the way: DetailedNUTS centred a k-slot NDR bit on the midpoint of
its end slots' CENTRES, which is the midpoint of the run's edges only when
those two slots are equally wide.  On a pattern mixing slot widths every
governed bit sat (w_hi - w_lo)/4 off its slots.  Fixed in the emitter, pinned
below by `test_a_run_of_unequal_slots_is_emitted_on_its_slots`.

Every perturbation here writes the row list back and verifies it landed
before the audit is read (test_ndr_audit_vacuity's method note: pybind hands
out copies, and a mutation that silently misses reads exactly like a check
that cannot fail).
"""
import contextlib
import io
import sys

import pytest

import buda

_STACK = """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus em_[4] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
"""
# The same stack with signal slots alternating 1 and 2 units wide: an even
# run's two end slots then differ in width.
_MIXED = _STACK.replace("(_ 1 1)x12", "(_ 1 1 _ 2 1)x6")
_RUN = """run_bundler STRICT
generate_topologies
set_track_pitch 3
run_planner 1
run_nuts
run_detailed_nuts
"""
RULES = {
    "plain": "",
    "x2": "def_ndr em width x2\nset_ndr em_ em\n",
    "x3": "def_ndr em width x3\nset_ndr em_ em\n",
    "metal": "def_ndr em width 3 metal\nset_ndr em_ em\n",
    "shielded": "def_ndr em width x2 shield bus net GND\nset_ndr em_ em\n",
    "guarded": "def_ndr em width x2 spacing x3\nset_ndr em_ em\n",
}


def _session(stack, rule):
    sys.path[:0] = ["build", "src", "tools"]
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for raw in (stack + rule + _RUN).splitlines():
            if raw.strip():
                s.do_command(raw.strip())
    return s


def _by_kind(session):
    """`check_design`'s own counts at the detailed stage — the typed kinds a
    methodology gates on, not message text."""
    with contextlib.redirect_stdout(io.StringIO()):
        return session._check_design("dnuts", False).get("by_kind", {})


def _put(session, segs):
    session.detailed_result.net_segments = segs
    return len(session.detailed_result.net_segments) == len(segs)


def _m3_pattern(session):
    return session.routing_grid.get_layer_grid(3).global_pattern()


# ── the predicate, on one wire ──────────────────────────────────────────────

def test_the_rule_reads_the_edges_of_a_run_of_signal_slots():
    """M3: VDD 0..2, then signal slots 3..4, 5..6, ... 25..26, GND 27..29,
    period 30.  A run of any length counts; a space, a rail, a half-slot
    offset, or a run across a rail does not."""
    pat = _m3_pattern(_session(_STACK, ""))
    on = buda.metal_on_signal_run
    assert on(pat, 3, 4)               # one slot
    assert on(pat, 3, 6)               # two slots and the space between
    assert on(pat, 33, 34)             # the next period
    assert not on(pat, 4, 5)           # the space between two slots
    assert not on(pat, 3.5, 4.5)       # half a slot off
    assert not on(pat, 3, 5)           # low edge right, high edge in a space
    assert not on(pat, 0, 2)           # the VDD rail itself
    assert not on(pat, 25, 34)         # across GND and VDD: a rail inside


# ── honest routes are on grid, under every rule form ────────────────────────

@pytest.mark.parametrize("rule", sorted(RULES))
@pytest.mark.parametrize("stack", ["uniform", "mixed"])
def test_every_honest_route_is_on_grid(rule, stack):
    s = _session(_STACK if stack == "uniform" else _MIXED, RULES[rule])
    assert s.detailed_result.net_segments, "nothing was placed"
    assert "OFF_GRID" not in _by_kind(s), (rule, stack)


# ── and the audit notices each way a wire can leave the grid ────────────────

@pytest.mark.parametrize("rule", ["plain", "x2", "shielded"])
def test_a_bit_moved_half_a_slot_is_off_grid(rule):
    s = _session(_STACK, RULES[rule])
    assert "OFF_GRID" not in _by_kind(s)
    segs = list(s.detailed_result.net_segments)
    target = next(r for r in segs if not r.is_shield)
    target.track_position += 1.0      # onto the space next to its slot(s)
    assert _put(s, segs)
    assert "OFF_GRID" in _by_kind(s), rule


def test_a_shield_moved_off_its_slot_is_off_grid():
    s = _session(_STACK, RULES["shielded"])
    segs = list(s.detailed_result.net_segments)
    target = next(r for r in segs if r.is_shield)
    target.track_position += 1.0
    assert _put(s, segs)
    assert "OFF_GRID" in _by_kind(s)


def test_a_whole_track_move_stays_on_grid():
    """The rule is about the GRID, not about where a bit was meant to be: a
    bit moved one full track pitch sits on another signal slot and is not
    OFF_GRID (whatever else it breaks is another kind's business)."""
    s = _session(_STACK, "")
    segs = list(s.detailed_result.net_segments)
    target = segs[0]
    target.track_position += 2.0
    assert _put(s, segs)
    assert "OFF_GRID" not in _by_kind(s)


def test_a_governed_bit_one_slot_narrow_is_ndr_width_not_off_grid():
    """k is NDR_WIDTH's question: a governed bit narrowed to one slot of its
    two is still metal ON a signal slot, so it is NDR_WIDTH and not
    OFF_GRID — the two kinds say different things and must not both fire."""
    s = _session(_STACK, RULES["x2"])
    segs = list(s.detailed_result.net_segments)
    target = next(r for r in segs if not r.is_shield)
    lo = target.track_position - target.width / 2
    target.width = 1.0
    target.track_position = lo + 0.5          # the run's first slot alone
    assert _put(s, segs)
    kinds = _by_kind(s)
    assert "NDR_WIDTH" in kinds and "OFF_GRID" not in kinds, kinds


def test_a_run_of_unequal_slots_is_emitted_on_its_slots():
    """The emitter defect this audit found.  An x2 run over a 1-unit and a
    2-unit slot has edges e.g. 90..94 and centre 92; the emitter placed it at
    the midpoint of the two slot CENTRES, 91.75, so the metal ran 89.75..93.75
    — over a space below, short of the slot above.  Every governed bit must
    now start and end on its run's slot edges."""
    s = _session(_MIXED, RULES["x2"])
    pat = _m3_pattern(s)
    rows = [r for r in s.detailed_result.net_segments
            if not r.is_shield and r.layer == 3]
    assert rows and all(abs(r.width - 4.0) < 1e-9 for r in rows), \
        [(r.track_position, r.width) for r in rows]
    for r in rows:
        assert buda.metal_on_signal_run(
            pat, r.track_position - r.width / 2,
            r.track_position + r.width / 2), (r.track_position, r.width)


# ── #946 end to end: an off-grid COPY is now the engine's finding ───────────

def test_a_copy_onto_a_misaligned_sibling_is_off_grid():
    """What #946 was, reached through the engine: the bottom-up copy of a
    reference solve onto a sibling half a track pitch away.  The template
    check refuses that copy on its own, so it is FORCED here by handing
    DetailedNUTS an ALIGNED verdict — the state #946's check was in — and
    `check_design` must then report the copied bits, which before this
    audit it could not."""
    sys.path[:0] = ["build", "src", "tools", "test/tests"]
    from test_hier_bottom_up import _dnuts_flow, _two_inst_db, _run_cmd
    s, _ = _dnuts_flow(_two_inst_db(x2=500, y2=301))
    _run_cmd(s, "check_template_tracks")
    v = s._template_track_verdict["proc_cell"]
    assert v["misaligned"], "the pair must really be half a pitch apart"
    v["aligned"] = v["aligned"] + list(v["misaligned"])
    v["misaligned"] = {}
    out = _run_cmd(s, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "copied" in out, out
    kinds = _by_kind(s)
    assert kinds.get("OFF_GRID", 0) > 0, kinds


def test_a_wire_across_its_layer_reports_layer_dir_alone():
    """Codex P2 on #959: a bit of an H segment moved onto a V layer has its
    track_position on the SEGMENT's perpendicular axis (a y), and the V
    layer's grid reads positions as x — so judging it there is a coordinate
    on the wrong axis.  It is LAYER_DIR, and only LAYER_DIR.  The half-slot
    move makes it off grid on either reading, so an audit that still judged
    it would report OFF_GRID too."""
    s = _session(_STACK, "")
    segs = list(s.detailed_result.net_segments)
    target = next(r for r in segs if r.layer == 3)
    target.layer = 4
    target.track_position += 1.0
    assert _put(s, segs)
    kinds = _by_kind(s)
    assert "LAYER_DIR" in kinds and "OFF_GRID" not in kinds, kinds
