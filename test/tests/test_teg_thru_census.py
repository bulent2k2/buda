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

"""BUDA-1907 — the `teg_mode thru` census (teg_multirect_status.md open 5).

A thru multi-rect block whose rects are NOT all reached by external placed
metal is silent BY DESIGN — internal equivalence is thru's declared meaning —
but when the assumption is wrong there was no way to discover it short of
reading the topology dump.  check_design's placed stages now report, per
audited bundle and thru block, which rects are left to the block's internal
routing: an INFO diagnostic, NEVER a violation, computed by the SAME contact
scan as the OVER audit's TEG_OPEN (`detect_teg_open` / `teg_touches` — one
predicate, so the two readings of "touched" cannot drift) and verdict-keyed
memoized in the BUDA-1913/1914 style so repeats stay quiet.

OVER blocks are unaffected: they keep TEG_OPEN and never appear in the
census.
"""
import contextlib
import io

import buda
import buda_cli


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


_TRACKS = [
    "def_track_pattern 4 0 (SIGNAL 2 2)x8",
    "def_track_pattern 5 0 (SIGNAL 2 2)x8",
]


def _disjoint_cmds(teg):
    """Two disjoint rects; under THRU a trunk Direct inside the lower rect
    touches only it, so the upper rect (rect#0) is left to the block's
    interior — the census shape.  (Under OVER the same trunk now emits a
    real stub to the upper rect — open 1 residual (i) — so the OVER twin of
    this shape routes clean; the OVER-gets-TEG_OPEN test below uses the
    ADJACENT-rect Direct corner instead, the remaining checked-in
    no-connection-metal shape now that MST candidates attach their rects.)"""
    return [
        f"add_block T rect 0 300 200 400 rect 0 0 200 100{teg}",
        "add_block src 400 0 500 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS


def _pin_trunk_inside_lower_rect(s):
    for i, c in enumerate(s.bundles[0].input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                return c
    raise AssertionError("no trunk-inside-lower-rect candidate found")


def _route(s, *cmds):
    for cmd in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)


def test_census_fires_naming_the_unreached_rects_on_thru():
    s = _session(_disjoint_cmds(""))          # default thru
    _pin_trunk_inside_lower_rect(s)
    _route(s, "run_planner", "run_nuts")
    verdict, out = _check(s, "nuts")
    # INFO, never a violation: the audit itself stays clean.
    assert verdict["violations"] == 0, out
    assert "Success: no violations found." in out
    line = next((l for l in out.splitlines() if "BUDA-1907" in l), None)
    assert line is not None, out
    assert "INFO" in line
    assert "teg_mode thru block 'T'" in line
    assert "rect#0 (0,300)-(200,400)" in line, line   # the untouched rect
    assert "rect#1" not in line                        # the touched one is not
    assert "internal routing" in line
    # No uppercase "TEG" token: existing flow tests assert its absence on
    # clean thru runs, and the census is deliberately not a violation kind.
    assert "TEG" not in line


def test_census_is_verdict_memoized_across_stages_and_repeats():
    s = _session(_disjoint_cmds(""))
    _pin_trunk_inside_lower_rect(s)
    _route(s, "run_planner", "run_nuts")
    _, out1 = _check(s, "nuts")
    assert "BUDA-1907" in out1
    # Same verdict, said once: a repeat check and the dnuts-stage check with
    # unchanged placement both stay quiet.
    _, out2 = _check(s, "nuts")
    assert "BUDA-1907" not in out2
    _route(s, "run_detailed_nuts")
    _, out3 = _check(s, "dnuts")
    assert "BUDA-1907" not in out3


def test_census_silent_when_every_rect_is_reached():
    # L-shape thru block: an H trunk at 0<y<100 crosses BOTH rects, so
    # nothing is left to the block's interior and the census says nothing.
    s = _session([
        "add_block L rect 0 0 100 400 rect 0 0 400 100",
        "add_block src 500 20 600 80",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    pinned = None
    for i, c in enumerate(s.bundles[0].input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None
    _route(s, "run_planner", "run_nuts", "run_detailed_nuts")
    _, out = _check(s, "nuts")
    assert "BUDA-1907" not in out, out
    _, out = _check(s, "dnuts")
    assert "BUDA-1907" not in out, out


def test_over_block_gets_teg_open_not_the_census():
    # An OVER block whose rect the route misses: the miss is a VIOLATION
    # (TEG_OPEN), and the census — thru's report — must not double-report
    # it.  Finding a still-dirty shape is the moving part here: the trunk
    # shapes (open 1 residuals (i)/(ii)), the MST attachment pass (Final-state
    # limitation 1) and now the ADJACENT-rect Direct corner (limitation 2,
    # 2026-08-27) have each been resolved out from under this vehicle in turn.
    # What remains is BITRUNK (Final-state limitation 4, bbox-only BY SCOPING —
    # no rect selection, no TEG connection metal): rect#1 at x 900..1000 lies
    # beyond the rungs' along-span and its union-centre stub lands in the gap,
    # so no placed metal of the bundle touches it.  Same pin as
    # test_teg_open.py::test_bitrunk_on_over_block_fires_teg_open_end_to_end,
    # which is where that guarantee is pinned end to end.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    pin = next((i for i, c in enumerate(s.bundles[0].input.candidates)
                if c.type.startswith("BITRUNK_H")), None)
    assert pin is not None, "no BITRUNK_H candidate found"
    _route(s, f"select_topology 1 {pin + 1}", "run_planner", "run_nuts")
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "rect#1 (900,0)-(1000,100)" in out, out
    assert "BUDA-1907" not in out


def test_single_rect_blocks_never_appear_in_the_census():
    # An ordinary single-rect design has no rects to leave to anyone; the
    # census is structurally silent (byte-identity where the feature is
    # unused).
    s = _session([
        "add_block a 0 0 100 100",
        "add_block b 400 0 500 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] a.tx b.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    _route(s, "run_planner", "run_nuts", "run_detailed_nuts")
    _, out = _check(s, "nuts")
    assert "BUDA-1907" not in out
    _, out = _check(s, "dnuts")
    assert "BUDA-1907" not in out


def test_the_ids_appear_in_dump_messages():
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("dump_messages")
    out = buf.getvalue()
    assert "BUDA-1907" in out and "INFO" in out
    assert "BUDA-1908" in out
