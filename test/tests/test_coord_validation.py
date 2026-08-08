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

"""Numeric validation of geometry arguments.

Two opposite failures, both silent-ish, both fixed here:

* Block / keepout / override coordinates are an INTEGER grid (`Rect` is
  `int x1,y1,x2,y2`).  `add_block` raised a bare `int()` traceback naming
  nothing; `add_keepout` and `add_grid_override` silently TRUNCATED, so a
  keepout written `10.9` blocked only to `10` — a zone smaller than asked for,
  which is the dangerous direction to round.
* Track-slot widths ARE fractional (`double`, and `SIGNAL 0.4 0.4` ships), so
  the pattern commands validate the SIGN instead: a zero/negative width was
  accepted and produced a layer with no tracks, surfacing six stages later as
  a DetailedNUTS warning with every bit stranded — and exit 0.
"""
import contextlib
import io

import buda_cli

_BASE = ("def_layer 3 M3 V TOP 0", "def_layer 4 M4 H TOP 0")


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


# ── integer coordinates: accepted, refused, and never truncated ─────────────

def test_add_block_takes_integer_coords():
    s, code, out = _run("add_block a 0 0 100 100")
    assert code is None, _err(out)
    assert s.fp.has_block("a")


def test_add_block_refuses_a_fractional_coord():
    # Used to be a raw `ValueError: invalid literal for int()` traceback.
    _s, code, out = _run("add_block a 0 0 100.5 100")
    assert code == 1
    msg = _err(out)
    assert "add_block" in msg and "<x2>" in msg      # names the argument
    assert "whole number" in msg and "100.5" in msg


def test_add_block_accepts_an_integral_value_spelled_as_a_float():
    # `100.0` and `1e2` name exact integers — nothing is lost by taking them,
    # so only a genuinely fractional value is refused.
    s, code, out = _run("add_block a 0 0 100.0 100", "add_block b 200 0 1e2 100")
    assert code is None, _err(out)
    assert s.fp.has_block("a") and s.fp.has_block("b")


def test_add_block_multi_rect_names_the_offending_rect():
    _s, code, out = _run(
        "add_block a rect 0 0 100 100 rect 200 0 300.5 100")
    assert code == 1
    assert "rect 2" in _err(out)


def test_add_keepout_refuses_rather_than_shrinking_the_zone():
    # The whole point: `10.9` silently became `10`, so the zone was ~1 unit
    # smaller than written on both axes and routing looked legal over it.
    _s, code, out = _run("add_keepout 0 0 10.9 10.9 3")
    assert code == 1
    assert "whole number" in _err(out)


def test_add_keepout_still_takes_integer_coords():
    s, code, out = _run("add_keepout 0 0 10 10 3")
    assert code is None, _err(out)
    assert len(s.fp.get_keepout_zones()) == 1


def test_add_grid_override_refuses_a_fractional_region():
    _s, code, out = _run("add_grid_override 3 0 0 50.5 50 0 _ 1 1")
    assert code == 1
    assert "whole number" in _err(out)


# ── track slots: fractional is FINE, non-positive is not ───────────────────

def test_track_slot_widths_may_be_fractional():
    # Sub-unit patterns are a supported, shipped case — the sign check must
    # not become an integrality check.
    s, code, out = _run("def_track_pattern 3 0 SIGNAL 0.4 0.4 POWER 0.8 0")
    assert code is None, _err(out)
    pat = s.routing_grid.get_layer_grid(3).global_pattern()
    assert abs(pat.unit_pitch() - 1.6) < 1e-9


def test_zero_width_slot_is_a_hard_error():
    # Accepted before: unit_pitch=0, a layer with no tracks, and the failure
    # only appeared at DetailedNUTS with every bit stranded.
    _s, code, out = _run("def_track_pattern 3 0 SIGNAL 0 0")
    assert code == 1
    msg = _err(out)
    assert "<width>" in msg and "positive" in msg


def test_negative_width_slot_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 3 0 SIGNAL -1 1")
    assert code == 1
    assert "<width>" in _err(out)


def test_negative_space_after_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 3 0 SIGNAL 1 -1")
    assert code == 1
    assert "<space_after>" in _err(out)


def test_zero_space_after_is_allowed():
    # Abutting slots are legitimate — `POWER 0.8 0` ships in flow/g1_bundle.
    _s, code, out = _run("def_track_pattern 3 0 POWER 0.8 0 SIGNAL 0.4 0.4")
    assert code is None, _err(out)


def test_infinite_width_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 3 0 SIGNAL inf 1")
    assert code == 1
    assert "<width>" in _err(out)


def test_repetition_group_slots_are_validated_too():
    # The group expands into the same slot parser, so the guard must cover it.
    _s, code, out = _run("def_track_pattern 3 0 (_ 0 1)x4")
    assert code == 1
    assert "<width>" in _err(out)


def test_add_grid_override_slots_are_validated_too():
    _s, code, out = _run("def_track_pattern 3 0 _ 1 1",
                         "add_grid_override 3 0 0 50 50 0 _ -1 1")
    assert code == 1
    assert "<width>" in _err(out)
