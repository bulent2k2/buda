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

"""`( <slots> )x<count>` repetition groups in def_track_pattern / add_grid_override.

A dense pattern is mostly one slot repeated, and spelling out twelve identical
`_ 1 1` triples buries the intent (and hides a typo in the middle of them).
`(_ 1 1)x12` says it once.  The expansion is purely syntactic: the grouped form
must produce a slot list byte-identical to the longhand it replaces, which is
what most of these tests assert.
"""
import contextlib
import io

import buda_cli

_BASE = ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50")


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in _BASE + cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return s, code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


def _slots(*cmds, layer=4):
    """Layer's global-pattern slots as (type, label, width, space_after)."""
    s, code, out = _run(*cmds)
    assert code is None, f"unexpected error: {_err(out)}"
    g = s.routing_grid.get_layer_grid(layer).global_pattern()
    return [(sl.type, sl.label, sl.width, sl.space_after) for sl in g.slots]


# ── expansion is exactly the longhand ────────────────────────────────────────

def test_group_expands_to_the_same_slots_as_longhand():
    grouped = _slots("def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1")
    longhand = _slots("def_track_pattern 4 0 VDD 2 1 " + "_ 1 1 " * 12
                      + "GND 2 1")
    assert grouped == longhand
    assert len(grouped) == 14


def test_group_preserves_unit_pitch_and_density():
    s, code, _ = _run("def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                      "def_track_pattern 5 0 VDD 2 1 " + "_ 1 1 " * 12
                      + "GND 2 1")
    assert code is None
    a = s.routing_grid.get_layer_grid(4).global_pattern()
    b = s.routing_grid.get_layer_grid(5).global_pattern()
    assert a.unit_pitch() == b.unit_pitch()
    assert a.signal_density() == b.signal_density()


def test_multiple_groups_with_a_lone_slot_between_them():
    # The symmetric case: a wider slot centred in a run of narrow ones.
    slots = _slots("def_track_pattern 4 0 (_ 1 1)x5 _ 2 1 (_ 1 1)x5")
    assert [w for _t, _l, w, _s in slots] == [1] * 5 + [2] + [1] * 5


def test_group_may_hold_several_slots():
    slots = _slots("def_track_pattern 4 0 (VDD 2 1 _ 1 1)x3 GND 2 1")
    assert [t for t, _l, _w, _s in slots] == [
        "POWER", "SIGNAL", "POWER", "SIGNAL", "POWER", "SIGNAL", "GROUND"]


def test_count_may_be_spaced_and_capital_x():
    a = _slots("def_track_pattern 4 0 (_ 1 1)x3")
    b = _slots("def_track_pattern 4 0 (_ 1 1)x 3")
    c = _slots("def_track_pattern 4 0 ( _ 1 1 ) X 3")
    assert a == b == c
    assert len(a) == 3


def test_count_of_one_is_a_plain_slot():
    assert (_slots("def_track_pattern 4 0 (_ 1 1)x1")
            == _slots("def_track_pattern 4 0 _ 1 1"))


def test_paren_free_pattern_is_untouched():
    # The normalize/split round-trip must not perturb the classic form.
    slots = _slots("def_track_pattern 4 0 POWER 2 1 SIGNAL 1 1 GROUND 2 1")
    assert slots == [("POWER", "power", 2, 1), ("SIGNAL", "signal", 1, 1),
                     ("GROUND", "ground", 2, 1)]


def test_add_grid_override_supports_groups_too():
    # Both slot-list commands share one parser, so the syntax cannot drift.
    s, code, out = _run("def_track_pattern 4 0 _ 1 1",
                        "add_grid_override 4 0 0 50 50 0 (_ 1 1)x6 GND 2 1")
    assert code is None, _err(out)
    assert "7 slots" in out


# ── malformed groups fail LOUD (never silently mis-expand) ───────────────────

def test_zero_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)x0")
    assert code == 1
    assert "not a positive integer" in _err(out)


def test_missing_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)")
    assert code == 1
    assert "missing its count" in _err(out)


def test_non_numeric_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)xz")
    assert code == 1
    assert "not a positive integer" in _err(out)


def test_unterminated_group_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1 GND 2 1")
    assert code == 1
    assert "unterminated '('" in _err(out)


def test_unmatched_close_paren_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 _ 1 1)x3")
    assert code == 1
    assert "unmatched ')'" in _err(out)


def test_empty_group_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 ()x3")
    assert code == 1
    assert "empty repetition group" in _err(out)


def test_partial_triple_in_group_is_a_hard_error():
    # `(_ 1)x3` would silently expand to a mis-aligned slot stream.
    _s, code, out = _run("def_track_pattern 4 0 (_ 1)x3")
    assert code == 1
    assert "not a whole number of" in _err(out)


def test_nested_groups_are_a_hard_error():
    # The grammar is deliberately flat, so say so rather than mis-parse.
    _s, code, out = _run("def_track_pattern 4 0 ((_ 1 1)x2)x3")
    assert code == 1
    assert "nested '('" in _err(out)
