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

"""Multi-rect + teg_mode across a checkpoint/resume (teg_multirect_status.md
open 6, the (a) measurement made durable).

A flat stage-resume (`btcl -r -s <stage>`) replays the recorded SETUP lines
verbatim through do_command and then calls load_pipeline — measured 2026-08-23:
the recorder writes `add_block L rect ... teg_mode over` VERBATIM, so the
resumed floorplan re-declares the rects and the OVER mode, and NOTHING is
lost on that path (the status doc's earlier "a resumed session loses per-rect
geometry" claim was wrong for the flat resume — the setup replay IS the
re-declaration the load_pipeline contract requires).  These tests pin the two
halves that would silently break if that seam regressed:

* the CLEAN vehicle (flow/teg_over_audit.buda's L-shape, pinned
  TRUNK_V@x250 whose connector leg is real metal) must resume to the SAME
  routed endpoint — same bit-wires, same placed geometry, both audits clean;
* the DIRTY vehicle (trunk Direct inside one disjoint rect — the
  TEG_OPEN-firing shape) must still FIRE TEG_OPEN after the resume:
  detect_teg_open reads rects + teg_mode off the session FLOORPLAN
  (src/verify.cpp), so a resume that lost the re-declaration would read
  Success over an electrically open net — exactly the silent shape the audit
  exists to remove.

Restored candidates additionally carry busterm rects + teg_mode through the
seg-busterm persist bridge (pinned by test_seg_busterm_persist.py); these
tests cover the flow-level behavior on top of that row-level guarantee.
"""
import contextlib
import io

import buda
import buda_cli


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        _run(s, c)
    return s


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


def _bits(s):
    """Placed bit-wire geometry as a comparable set."""
    return {(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.layer,
             ns.track_position, ns.span_lo, ns.span_hi)
            for ns in s.detailed_result.net_segments}


def _lshape_setup(ckpt):
    # The teg_over_audit.buda geometry: L block (tall arm + wide base), OVER.
    return [
        f"open_bdb {ckpt}",
        "add_block src 500 150 600 250",
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "def_track_pattern 4 0 (SIGNAL 2 2)x8",
        "def_track_pattern 5 0 (SIGNAL 2 2)x8",
    ]


def test_flat_resume_keeps_rects_teg_and_routes_identically(tmp_path):
    ckpt = str(tmp_path / "ckpt.bdb")

    # ── build session: pin the leg-carrying TRUNK_V@x250, route, checkpoint ──
    s1 = _session(_lshape_setup(ckpt) + ["run_bundler STRICT",
                                         "generate_topologies"])
    pin = None
    for i, c in enumerate(s1.bundles[0].input.candidates):
        if c.type.startswith("TRUNK_V@x250"):
            pin = i + 1
            break
    assert pin is not None, "TRUNK_V@x250 not generated"
    for cmd in (f"select_topology 1 {pin}", "run_planner", "run_nuts",
                "run_detailed_nuts"):
        _run(s1, cmd)
    assert s1.detailed_result.num_unplaced == 0
    build_bits = _bits(s1)
    assert len(build_bits) == 12, "L-shape leg route is 3 segs x 4 bits"
    v1, _ = _check(s1, "dnuts")
    assert v1["by_kind"].get("TEG_OPEN", 0) == 0

    # ── resume session: the flat stage-resume shape — setup replayed
    #    verbatim (what the recorded trace holds, measured), load_pipeline,
    #    tail from the nuts cut ──
    s2 = _session(_lshape_setup(ckpt))
    # the re-declared floorplan holds the rects and the OVER mode
    assert s2.fp.get_block_teg_mode("L") == buda.TegMode.OVER
    assert len(s2.fp.get_block_rects("L")) == 2
    _run(s2, "load_pipeline")
    # restored candidates carry busterm rects + teg_mode (the persist bridge)
    sel = None
    for c in s2.bundles[0].input.candidates:
        if c.type.startswith("TRUNK_V@x250"):
            sel = c
            break
    assert sel is not None, "pinned candidate lost on restore"
    l_bts = [bt for pair in (sel.seg_busterms.get(i, (None, None))
                             for i in range(len(sel.segments)))
             for bt in pair if bt is not None and bt.block_name == "L"]
    assert l_bts and all(len(bt.rects) == 2 and
                         bt.teg_mode == buda.TegMode.OVER for bt in l_bts)
    for cmd in ("run_nuts", "run_detailed_nuts"):
        _run(s2, cmd)
    assert _bits(s2) == build_bits, "resume must reproduce the routed endpoint"
    v2, out2 = _check(s2, "dnuts")
    assert v2["by_kind"].get("TEG_OPEN", 0) == 0, out2


def test_flat_resume_keeps_teg_open_audit_armed(tmp_path):
    ckpt = str(tmp_path / "dirty.bdb")
    setup = [
        f"open_bdb {ckpt}",
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 0 500 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "def_track_pattern 4 0 (SIGNAL 2 2)x8",
        "def_track_pattern 5 0 (SIGNAL 2 2)x8",
    ]

    # ── build session: trunk Direct inside the lower rect (no stubs) — the
    #    upper rect is untouched by placed metal, so TEG_OPEN fires ──
    s1 = _session(setup + ["run_bundler STRICT", "generate_topologies"])
    pin = None
    for i, c in enumerate(s1.bundles[0].input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                pin = i + 1
                break
    assert pin is not None, "no trunk-inside-lower-rect candidate found"
    for cmd in (f"select_topology 1 {pin}", "run_planner", "run_nuts"):
        _run(s1, cmd)
    v1, out1 = _check(s1, "nuts")
    assert v1["by_kind"].get("TEG_OPEN", 0) >= 1, out1

    # ── resume session: the audit must stay ARMED — detect_teg_open reads
    #    the FLOORPLAN's rects + teg_mode, which the setup replay
    #    re-declares; a resume that lost them would report Success here ──
    s2 = _session(setup)
    _run(s2, "load_pipeline")
    _run(s2, "run_nuts")
    v2, out2 = _check(s2, "nuts")
    assert v2["by_kind"].get("TEG_OPEN", 0) >= 1, (
        "TEG_OPEN went silent across the resume — the floorplan lost its "
        "rects/teg_mode re-declaration:\n" + out2)
    assert "OVER block 'T'" in out2
