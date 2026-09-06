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

"""`set_bus_layers` — which layers a named bus may route on, and
`emit_pin_def expect_layer` — the check that the constraint held.

Measured on the phase-0 toy (docs/internal/librelane_hier_flow.md §8 step
3b): the planner moved a 32-bit bus from met3 to met1 after a 0.8 um
placement change, the block's pin template followed it off the layer the
handoff is built around, and the block's own wire went +26 % to +49 %.
`TOP` is a preference the cost function outvotes; this is the constraint.
"""
import contextlib
import io

import pytest

import buda_cli
from test_emit_pin_def import (_HIER_ROUTE, _HIER_SETUP, _bus, _def, _lef,
                               _macro_pins, _pins, _session, _TWO)


def _hier_with(tmp_path, before, tail):
    """The hier fixture with commands injected BEFORE the route — a scope
    declared after `run_planner` governs nothing, which is the whole point
    of declaring it."""
    lef = tmp_path / "blk.lef"
    lef.write_text(_lef(_macro_pins()))
    d = tmp_path / "top.def"
    d.write_text(_def(_TWO, _bus("mid", "u0", "u1")))
    return _session(_HIER_SETUP + [f"import_lef_tech {lef}",
                                   f"import_def_lef {d} {lef}"]
                    + list(before) + _HIER_ROUTE + list(tail))

# M1 and M3 are both HORIZONTAL and only M3 is TOP, so a free plan takes
# M3 and a constraint to M1 is visible in the placed result — the shape the
# toy measured, where the bus left met3 for met1 and the pins followed.
# `d` and `e` go to DIFFERENT receiver blocks so STRICT bundling keeps
# them apart (one driver/receiver pair is one bundle, and a bundle is one
# bus); both run east-west, so both are comparable on an H layer.
_BASE = [
    "def_layer 1 M1 H 30",
    "def_layer 2 M2 V 30",
    "def_layer 3 M3 H TOP 30",
    "def_layer 4 M4 V TOP 30",
    "def_track_pattern 1 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "def_track_pattern 2 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "def_track_pattern 3 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "def_track_pattern 4 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "add_block a 0 0 100 100",
    "add_block b 300 0 400 100",
    "add_block c 700 0 800 100",
    "add_bus d[4] a.o b.i",
    "add_bus e[4] a.o2 c.i2",
]
_TAIL = ["run_bundler STRICT", "generate_topologies", "run_planner 3",
         "run_nuts", "run_detailed_nuts"]


def _run(extra=(), tail=None):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in _BASE + list(extra) + (list(tail) if tail is not None else _TAIL):
            s.do_command(c)
    return s, buf.getvalue()


def _layers_of(s, prefix):
    """The layers the placed bit-wires of a bus are on."""
    names = {w.input.original_bundle.id: list(w.input.original_bundle.get_net_names())
             for w in s.bundles}
    out = set()
    for ns in s.detailed_result.net_segments:
        nm = names.get(ns.bundle_id, [])
        if 0 <= ns.bit_index < len(nm) and nm[ns.bit_index].startswith(prefix):
            out.add(ns.layer)
    return sorted(out)


def test_a_free_plan_takes_the_cheap_layer_and_the_constraint_moves_it():
    """The whole point: `TOP` is a preference, so the bus lands where the
    cost function sends it until something says otherwise."""
    s, _log = _run()
    assert _layers_of(s, "d") == [3]
    s, log = _run(["set_bus_layers d M1"])
    assert _layers_of(s, "d") == [1], log
    assert "set_bus_layers: 'd' -> M1" in log


def test_only_the_named_bus_moves():
    """A scope is a bus, not the design: `e` keeps the free choice."""
    s, _log = _run(["set_bus_layers d M1"])
    assert _layers_of(s, "d") == [1] and _layers_of(s, "e") == [3]


def test_star_is_the_default_and_a_longer_prefix_outranks_it():
    """The resolution `set_bundling` and `set_ndr` use.  Checked on the
    MASKS as well as the placement: `* M1` reaches both bundles, and what
    each then does with one H layer is a capacity question, not a scoping
    one (M1's band holds one of these buses, not both)."""
    s, _log = _run(["set_bus_layers * M1"])
    assert all(list(w.input.allowed_layers) == [1] for w in s.bundles)
    s, _log = _run(["set_bus_layers * M3", "set_bus_layers d M1"])
    assert _layers_of(s, "d") == [1] and _layers_of(s, "e") == [3]


def test_several_layers_leave_the_planner_the_choice_among_them():
    s, _log = _run(["set_bus_layers d M1,M3"])
    assert _layers_of(s, "d") == [3]           # free to keep its preference
    s, log = _run(["set_bus_layers d M3,M1"])
    assert "'d' -> M1, M3" in log               # reported in layer order


def test_a_scope_is_listed_and_cleared():
    s, log = _run(["set_bus_layers d M1", "set_bus_layers"], tail=[])
    assert "set_bus_layers d -> M1" in log
    s, log = _run(["set_bus_layers d M1", "set_bus_layers d off",
                   "set_bus_layers"], tail=[])
    assert "cleared scope 'd'" in log and "no bus layer scopes declared" in log
    # `* off` clears every scope, like `set_cell_layer_cap * off`.
    s, log = _run(["set_bus_layers d M1", "set_bus_layers e M1",
                   "set_bus_layers * off", "set_bus_layers"], tail=[])
    assert "cleared 2 scope(s) (d, e)" in log and "no bus layer scopes" in log
    # Clearing the cleared, and a `*` scope cleared by name.
    _s, log = _run(["set_bus_layers d off"], tail=[])
    assert "no scope 'd' to clear" in log
    s, log = _run(["set_bus_layers * M1", "set_bus_layers * off",
                   "set_bus_layers"], tail=[])
    assert "cleared scope '*'" in log and "no bus layer scopes" in log


def test_declaring_nothing_leaves_the_route_untouched():
    """The byte-identity guarantee: an undeclared design plans as before."""
    a, _log = _run()
    b, _log = _run(["set_bus_layers d M1,M3", "set_bus_layers d off"])
    assert ([(n.bundle_id, n.bit_index, n.layer, n.track_position)
             for n in a.detailed_result.net_segments]
            == [(n.bundle_id, n.bit_index, n.layer, n.track_position)
                for n in b.detailed_result.net_segments])


def test_an_unknown_layer_and_a_malformed_scope_are_refused():
    _s, log = _run(["set_bus_layers d M9"], tail=[])
    assert "unknown layer 'M9'" in log and "declared: M1, M2, M3, M4" in log
    _s, log = _run(["set_bus_layers d"], tail=[])
    assert "usage: set_bus_layers" in log
    _s, log = _run(["set_bus_layers d ,"], tail=[])
    assert "no layer named" in log


def test_a_bundle_whose_nets_disagree_is_a_hard_error():
    """A bundle routes as ONE bus, so its bits cannot take different
    layers; the bundler put them together, so the refusal names both."""
    with pytest.raises(SystemExit):
        _run(["set_bus_layers d_0 M3", "set_bus_layers d_1 M1"])


def test_a_scope_declared_after_bundling_still_reaches_the_plan():
    """The bundling hook is one-shot, and the PLAN is where a layer is
    settled — so a scope declared (or changed, or cleared) after
    `run_bundler` is re-applied before every full plan (Codex #889)."""
    late = ["run_bundler STRICT", "set_bus_layers d M1", "generate_topologies",
            "run_planner 3", "run_nuts", "run_detailed_nuts"]
    s, _log = _run(tail=late)
    assert _layers_of(s, "d") == [1]
    # Changed after bundling: the last word wins, not the first.
    changed = ["run_bundler STRICT", "set_bus_layers d M1",
               "set_bus_layers d M3", "generate_topologies", "run_planner 3",
               "run_nuts", "run_detailed_nuts"]
    s, _log = _run(tail=changed)
    assert _layers_of(s, "d") == [3]
    # CLEARED after bundling: the bundle goes back to what governed it
    # before, which here is nothing — the free choice, not a stale mask.
    cleared = ["set_bus_layers d M1", "run_bundler STRICT",
               "set_bus_layers d off", "generate_topologies", "run_planner 3",
               "run_nuts", "run_detailed_nuts"]
    s, _log = _run(tail=cleared)
    assert _layers_of(s, "d") == [3]
    assert all(not list(w.input.allowed_layers) for w in s.bundles)


def test_replanning_under_one_scope_is_stable():
    """Re-applying must restart from the pre-restriction mask, or a second
    plan would intersect an already-intersected mask with itself."""
    twice = ["run_bundler STRICT", "set_bus_layers d M1,M3",
             "generate_topologies", "run_planner 3", "run_planner 3",
             "run_nuts", "run_detailed_nuts"]
    s, _log = _run(tail=twice)
    d = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("d"))
    assert sorted(d.input.allowed_layers) == [1, 3]


def test_a_governed_net_bundled_with_an_ungoverned_one_is_refused():
    """STRICT bundles two buses that share a driver and receivers, so a
    scope on one would silently reroute the other (Codex #889)."""
    both_to_b = [
        "def_layer 1 M1 H 30", "def_layer 2 M2 V 30",
        "def_layer 3 M3 H TOP 30", "def_layer 4 M4 V TOP 30",
        "def_track_pattern 1 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
        "def_track_pattern 3 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
        "def_track_pattern 4 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
        "add_block a 0 0 100 100", "add_block b 300 0 400 100",
        "add_bus d[4] a.o b.i", "add_bus e[4] a.o b.i",   # ONE bundle
        "set_bus_layers d M1", "run_bundler STRICT",
    ]
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with pytest.raises(SystemExit):
        with contextlib.redirect_stdout(buf):
            for c in both_to_b:
                s.do_command(c)
    log = buf.getvalue()
    assert "mixes governed and ungoverned nets" in log
    assert "a bus nobody scoped" in log and "set_bundling" in log
    # Scoping the peer to the SAME layers is the remedy the message names.
    ok = both_to_b[:-2] + ["set_bus_layers d M1", "set_bus_layers e M1",
                           "run_bundler STRICT", "generate_topologies",
                           "run_planner 3", "run_nuts", "run_detailed_nuts"]
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ok:
            s.do_command(c)
    assert sorted({n.layer for n in s.detailed_result.net_segments}) == [1]


def test_different_prefixes_with_the_same_layers_are_accepted():
    """The refusal's own remedy is "give the scopes the same layers", so
    doing that has to work — the rule is the resolved MASK, not the prefix
    count (Codex #889)."""
    s, log = _run(["set_bus_layers d_0 M1", "set_bus_layers d_1 M1",
                   "set_bus_layers d_2 M1", "set_bus_layers d_3 M1"])
    assert _layers_of(s, "d") == [1], log
    assert "different" not in log.lower() or "Error" not in log


def test_the_restriction_survives_the_hier_resolver(tmp_path):
    """`_apply_layer_policies` OWNS allowed_layers and rewrites it at every
    wrapper-set transition, so the scope is RE-APPLIED after it — the same
    rule the NDR layer restriction follows."""
    out = tmp_path / "blk.def"
    free, _log = _hier_with(tmp_path, [], [])
    assert sorted({n.layer for n in free.detailed_result.net_segments}) == [3]
    s, log = _hier_with(tmp_path, ["set_bus_layers mid met1"],
                        [f"emit_pin_def {out} blk"])
    got = sorted({n.layer for n in s.detailed_result.net_segments})
    assert got == [1], (got, log)


def test_expect_layer_refuses_a_template_written_off_the_intended_layer(tmp_path):
    """The writer's half: the check that the constraint held.  Without the
    constraint the pins follow the plan onto the wrong layer, and the
    refusal names the pins and the remedy."""
    out = tmp_path / "blk.def"
    s, log = _hier_with(tmp_path, [],
                        [f"emit_pin_def {out} blk expect_layer met1"])
    assert "are not on the expected layer(s) met1" in log
    assert "set_bus_layers <prefix> met1" in log and not out.exists()
    # With the constraint, the same command writes the template.
    s, log = _hier_with(tmp_path, ["set_bus_layers mid met1"],
                        [f"emit_pin_def {out} blk expect_layer met1"])
    assert "[PinDEF]" in log and out.exists()
    assert {p[2] for n, p in _pins(out.read_text()).items()
            if n.startswith(("d[", "q["))} == {"met1"}
    # An unknown expected layer is refused by name.
    _s, log = _hier_with(tmp_path, [],
                         [f"emit_pin_def {out} blk expect_layer met9"])
    assert "unknown expect_layer 'met9'" in log
