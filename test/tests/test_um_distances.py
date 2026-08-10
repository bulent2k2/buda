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

"""`um`-suffixed script distances (opens_interchange.md item 6).

The engine is unit-agnostic and `set_import_scale` deliberately does not
rescale script distances — so changing the scale silently changes what every
hand-typed number MEANS.  The `um` suffix writes the intent down instead:
`corner_margin dx 2um` is two microns at ANY import scale, converted through
the declared `lu_per_um` at parse time.  A bare number stays a layout-unit
distance, byte-identical to before.
"""
import contextlib
import io

import pytest

import buda_cli

_BASE = ("def_layer 3 M3 V TOP 0", "def_layer 4 M4 H TOP 0")
# 2000 layout units per micron — the DBU-flavoured scale the vehicle uses.
# (A literal `set_import_scale dbu` defers resolution to import_def_lef and
# REFUSES um suffixes until then — pinned below.)
_SCALED = ("open_bdb :memory:", "set_import_scale 2000") + _BASE


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return s, code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


# ── the suffix converts through the declared scale ─────────────────────────

def test_um_equals_bare_at_the_default_scale():
    a, _, _ = _run(*_BASE, "set_min_stub_length 2um")
    b, _, _ = _run(*_BASE, "set_min_stub_length 2")
    assert a._min_stub["global"] == b._min_stub["global"] == 2


def test_um_scales_with_the_declared_import_scale():
    s, code, out = _run(*_SCALED, "set_min_stub_length 0.5um")
    assert code is None, _err(out)
    assert s._min_stub["global"] == 1000              # 0.5 um x 2000 lu/um


def test_track_pattern_widths_take_the_suffix():
    s, code, out = _run(*_SCALED,
                        "def_track_pattern 3 0 SIGNAL 0.07um 0.12um")
    assert code is None, _err(out)
    (slot,) = s.routing_grid.get_layer_grid(3).global_pattern().slots
    assert slot.width == pytest.approx(140.0)         # 0.07 um x 2000
    assert slot.space_after == pytest.approx(240.0)


def test_repetition_groups_and_um_compose():
    s, code, out = _run(*_SCALED,
                        "def_track_pattern 3 0 (SIGNAL 0.07um 0.12um)x4")
    assert code is None, _err(out)
    slots = s.routing_grid.get_layer_grid(3).global_pattern().slots
    assert len(slots) == 4
    assert all(sl.width == pytest.approx(140.0) for sl in slots)


def test_corner_margin_takes_the_suffix():
    # A block declared AFTER the global margin inherits it; the block's
    # resolved per-block margin is the observable.
    s, code, out = _run(*_SCALED, "corner_margin dx 1um dy 0.5um",
                        "add_block a 0 0 100000 100000")
    assert code is None, _err(out)
    m = s.fp.get_block_corner_margin("a")
    assert (m.dx, m.dy) == (2000, 1000)


def test_integer_coords_accept_um_that_lands_on_the_grid():
    s, code, out = _run(*_SCALED, "add_block a 0 0 0.5um 0.5um",
                        "add_keepout 0 0 0.25um 0.25um 3")
    assert code is None, _err(out)
    assert s.fp.has_block("a")


def test_integer_coords_refuse_um_that_misses_the_grid():
    # At the DEFAULT scale (1 lu/um), 0.5um is 0.5 layout units — refused
    # with the scale named, not truncated.
    _s, code, out = _run(*_BASE, "add_block a 0 0 0.5um 1")
    assert code == 1
    msg = _err(out)
    assert "0.5um" in msg and "scale 1" in msg and "whole number" in msg


def test_detour_channel_and_track_pitch_take_the_suffix():
    s, code, out = _run(*_SCALED, "detour_channel N 0.05um",
                        "set_track_pitch 0.001um")
    assert code is None, _err(out)
    assert s._nuts_pitch == pytest.approx(2.0)        # 0.001 um x 2000


# ── the suffix fails loud, never guesses ───────────────────────────────────

def test_garbage_before_the_suffix_is_a_hard_error():
    _s, code, out = _run(*_BASE, "set_min_stub_length xum")
    assert code == 1
    assert "before 'um'" in _err(out)


def test_declaring_the_scale_after_a_suffix_warns():
    """The suffix converts AT PARSE TIME, so a later set_import_scale means
    the earlier distances resolved under the old scale — the exact silent
    re-meaning the suffix exists to prevent.  Loud, not fatal."""
    _s, _code, out = _run("open_bdb :memory:", *_BASE,
                          "set_min_stub_length 2um",
                          "set_import_scale 2000")
    assert "PREVIOUS" in out and "um" in out


def test_bare_numbers_are_untouched():
    """The suffix is opt-in: a suffix-free script parses byte-identically."""
    s, code, out = _run(*_SCALED, "set_min_stub_length 7",
                        "def_track_pattern 3 0 SIGNAL 0.4 0.4",
                        "add_keepout 0 0 10 10 3")
    assert code is None, _err(out)
    assert s._min_stub["global"] == 7
    (slot,) = s.routing_grid.get_layer_grid(3).global_pattern().slots
    assert slot.width == 0.4                          # NOT rescaled


def test_um_is_refused_while_the_dbu_scale_is_pending():
    """`set_import_scale dbu` DEFERS the factor until import_def_lef reads
    the DEF's UNITS.  In that window a `um` suffix would convert through the
    stale value and silently mean the wrong thing (Codex P1 on #666) — so it
    is refused, naming the two ways out."""
    _s, code, out = _run("open_bdb :memory:", "set_import_scale dbu", *_BASE,
                         "set_min_stub_length 2um")
    assert code == 1
    msg = _err(out)
    assert "dbu" in msg and "import_def_lef" in msg


def test_def_layer_spans_take_the_suffix():
    """span_min/span_max are distances too (Codex P2 on #666) — the setup-wide
    suffix promise has to hold for them."""
    s, code, out = _run("open_bdb :memory:", "set_import_scale 2000",
                        "def_layer 3 M3 V TOP 0 span_min 0.5um span_max 2um")
    assert code is None, _err(out)
    # LayerStack has no span getter; the session mirrors what it applied.
    assert s._layer_spans[3] == (1000, 4000)
