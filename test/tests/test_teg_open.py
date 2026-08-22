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

"""TEG_OPEN — the missing-bridge audit (teg_multirect_status.md open 1(b)).

A `teg_mode over` multi-rect block declares its rects NOT internally
connected, so every rect needs external attachment by the bundle's PLACED
metal.  Bridges are generation-only today (nothing downstream of generation
consumes Topology::bridge_segments), so a selected bridged candidate routes
WITHOUT the bridge — and before this audit, check_design reported Success on
that electrically open net (measured: the §1.1 repro in
docs/internal/teg_multirect_status.md).

These tests drive the full flat pipeline through BudaSession and read
check_design's structured verdict, so they pin the audit end to end at both
placed stages.  check_topo deliberately does NOT carry the kind (it feeds
generation gates, dogleg trials and healer metrics — a reporting audit must
not start dropping candidates), so there is no topo-stage assertion here.
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


def _lshape_cmds(teg):
    return [
        "add_block src 500 150 600 250",
        f"add_block L rect 0 0 100 400 rect 0 0 400 100{teg}",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS


def _pin_bridged_candidate(s):
    """Pin the first candidate carrying a bridge over L (1-based index)."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if dict(c.bridge_segments):
            with contextlib.redirect_stdout(io.StringIO()):
                s.do_command(f"select_topology 1 {i + 1}")
            return c
    raise AssertionError("no bridged candidate generated for the L-shape")


def test_missing_bridge_is_teg_open_at_nuts_and_dnuts():
    # The §1.1 repro: pin a bridged candidate, route, audit.  The bridge is
    # placed by nothing, so the rect it was meant to connect is untouched.
    s = _session(_lshape_cmds(" teg_mode over"))
    cand = _pin_bridged_candidate(s)
    assert dict(cand.bridge_segments), "vehicle must rely on a bridge"

    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "OVER block 'L'" in out and "(nuts)" in out

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "(dnuts)" in out
    # The message names the defect precisely: a DECLARED bridge, unrealized.
    assert "unrealized" in out


def test_thru_block_is_exempt_by_design():
    # Same geometry, default thru: internal equivalence is the declared
    # meaning, so the audit must stay silent whatever candidate wins.
    s = _session(_lshape_cmds(""))
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_gap_stub_retraction_fires_teg_open_alongside_face_kinds():
    # The disjoint-gap OVER branch stubs BOTH rects to the trunk, so IF the
    # stubs placed as generated, connectivity would hold through the trunk
    # and the audit would pass (its union-face bridge is redundant metal, not
    # a missing link — teg_multirect_status.md §1.3, last paragraph).
    #
    # Measured while building this test: they do NOT place as generated — in
    # BOTH orientations NUTS retracts the far gap-stub to the trunk (span
    # [150,158] against a face at 300; the wishlist-topo "gap-stub pair
    # emitted at rect CENTRES" bug family), so the placed route genuinely is
    # open at the far rect.  The audit must say so, corroborating the
    # geometric kinds that already fire (BUSTERM_FACE, and ANTENNA at dnuts)
    # with the semantic verdict they lack: WHICH rect of the OVER block ended
    # up unreached.  When the gap-stub placement is fixed, this test's
    # expectation flips to TEG_OPEN == 0 — that flip is the fix's proof.
    s = _session([
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 150 500 250",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # Pin an H trunk in the gap (y 100..300): the gap-stub pair + bridge shape.
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y") and dict(c.bridge_segments):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 100 < y < 300:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no gap-trunk bridged candidate found"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert verdict["by_kind"].get("BUSTERM_FACE", 0) >= 1, out
    # The far rect is the unreached one, named in the message.
    assert "rect#0 (0,300)-(200,400)" in out


def test_all_span_trunk_touches_every_rect_no_teg_open():
    # A trunk crossing EVERY rect of the OVER block (all_span at generation:
    # no bridge emitted, none needed) must audit clean: placed contact per
    # rect is the predicate, not bridge presence.
    s = _session([
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "add_block src 500 20 600 80",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # An H trunk at y<100 crosses BOTH rects (base y 0..100, arm y 0..400).
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y") and not dict(c.bridge_segments):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no bridgeless low trunk candidate found"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_check_topo_does_not_carry_teg_open():
    # The kind is placed-stage only: check_topo feeds generation gates and
    # healer metrics, so it must not report TEG_OPEN even on the repro
    # candidate (the pool must not shrink over a reporting audit).
    s = _session(_lshape_cmds(" teg_mode over"))
    cand = _pin_bridged_candidate(s)
    ct = buda.ConnTopology()
    ct.build(cand, s.fp)
    res = buda.check_topo(ct, cand, s.fp, 1)
    kinds = {v.kind for v in res.violations}
    assert buda.ViolationKind.TEG_OPEN not in kinds
