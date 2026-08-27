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

"""Feedthru on a MULTI-RECT block (teg_multirect_status.md limitation 7).

Feedthru used to be a single-rect MVP: `add_trunk`'s relay loop skipped any
block with more than one rect, so `set_feedthru` on a multi-rect block was
inert (BUDA-1908 said so, but the declaration did nothing).

Resolved by TEG MODE, because the two declarations speak about the same
thing — the block's own internal routing:

  * `thru` (the default) declares the rects internally connected, which is
    the SAME trust a feedthru asks for, so a feedthru relays across the
    ALONG-HULL of the rects the spine's band actually crosses.  The hull is
    what keeps the relay honest: a rect in another band is not under the
    trunk, so its extent must not be claimed as relayable.
  * `over` declares the rects NOT internally connected, which contradicts
    the relay claim — refused, loudly, by BUDA-1908 (retargeted here).
"""
import contextlib
import io

import buda_cli


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    for c in cmds:
        with contextlib.redirect_stdout(out):
            s.do_command(c)
    return s, out.getvalue()


_LAYERS = [
    "def_layer 3 M3 H 20",
    "def_layer 4 M4 V 20",
    "def_layer 5 M5 H TOP 10",
    "def_layer 6 M6 V TOP 10",
]
_TAIL = [
    "add_bus b[4] src.out mid.in,dst.in",
    "run_bundler STRICT",
    "generate_topologies",
]


def _flow(mid_decl, feedthru=True):
    cmds = list(_LAYERS) + [
        "add_block src 0 200 100 300",
        mid_decl,
        "add_block dst 900 200 1000 300",
    ]
    if feedthru:
        cmds.append("set_feedthru mid *")
    return cmds + _TAIL


# Two rects side by side in the SAME perpendicular band, with a gap along the
# trunk axis — the shape a feedthru is for: the trunk enters the block's metal
# and leaves it again.
_MID_BAND = "add_block mid rect 400 150 500 350 rect 600 150 700 350"
# One rect under the trunk, one in a DIFFERENT band: only the first is
# relayable, so the union bbox (400..700) would claim empty space.
_MID_OFFBAND = "add_block mid rect 400 150 500 350 rect 600 500 700 700"
# The single-rect equivalent of _MID_BAND's union — the control the multi-rect
# THRU result must reproduce.
_MID_SOLID = "add_block mid 400 150 700 350"


def _cand(s, type_prefix):
    for c in s.bundles[0].input.candidates:
        if c.type.startswith(type_prefix):
            return c
    raise AssertionError(
        f"no candidate {type_prefix}; have "
        f"{[c.type for c in s.bundles[0].input.candidates]}")


def _spine_spans(cand):
    """Along-spans of the candidate's H segments at the trunk locus."""
    return sorted((min(sg.start.x, sg.end.x), max(sg.start.x, sg.end.x))
                  for sg in cand.segments if sg.start.y == sg.end.y)


def test_thru_multirect_feedthru_splits_the_trunk_at_the_band_hull():
    s, _ = _session(_flow(_MID_BAND))
    c = _cand(s, "TRUNK_H@y200")
    assert list(c.feedthru_blocks) == ["mid"], list(c.feedthru_blocks)
    # Two spine pieces, split at the along-hull of the two band rects.
    assert _spine_spans(c) == [(100, 400), (700, 900)], _spine_spans(c)
    # And the relay is what the single-rect block of the same union does.
    ctl, _ = _session(_flow(_MID_SOLID))
    cc = _cand(ctl, "TRUNK_H@y200")
    assert _spine_spans(cc) == _spine_spans(c)
    assert c.estimated_wirelength == cc.estimated_wirelength


def test_thru_multirect_relay_stops_at_the_rects_under_the_trunk():
    # The honesty guard: mid's second rect sits in another band, so the trunk
    # never meets it.  Relaying across the UNION bbox (400..700) would delete
    # metal over empty space — a fabricated relay.  The split is the crossed
    # rect's own faces.
    s, _ = _session(_flow(_MID_OFFBAND))
    c = _cand(s, "TRUNK_H@y200")
    assert list(c.feedthru_blocks) == ["mid"], list(c.feedthru_blocks)
    assert _spine_spans(c) == [(100, 400), (500, 900)], _spine_spans(c)


def test_thru_multirect_feedthru_no_longer_warns():
    _, out = _session(_flow(_MID_BAND))
    assert "BUDA-1908" not in out, out


def test_over_multirect_feedthru_is_refused_loudly_and_stays_inert():
    s, out = _session(_flow(_MID_BAND + " teg_mode over"))
    line = next((l for l in out.splitlines() if "BUDA-1908" in l), None)
    assert line is not None, out
    assert "WARNING" in line
    assert "teg_mode over" in line and "mid" in line
    # Inert by design: the spine is NOT split.
    c = _cand(s, "TRUNK_H@y200")
    assert list(c.feedthru_blocks) == []
    assert _spine_spans(c) == [(100, 900)], _spine_spans(c)


def test_single_rect_and_undeclared_multirect_are_untouched():
    # Control 1: single-rect feedthru unchanged (2 pieces, no warning).
    s, out = _session(_flow(_MID_SOLID))
    assert "BUDA-1908" not in out, out
    assert _spine_spans(_cand(s, "TRUNK_H@y200")) == [(100, 400), (700, 900)]
    # Control 2: the same multi-rect block with NO feedthru declaration is one
    # continuous pass-through wire, in either teg mode.
    for decl in (_MID_BAND, _MID_BAND + " teg_mode over"):
        s2, out2 = _session(_flow(decl, feedthru=False))
        assert "BUDA-1908" not in out2, out2
        c = _cand(s2, "TRUNK_H@y200")
        assert list(c.feedthru_blocks) == []
        assert _spine_spans(c) == [(100, 900)], _spine_spans(c)


def test_thru_multirect_feedthru_routes_and_audits_clean_end_to_end():
    s, _ = _session(_flow(_MID_BAND) + [
        "def_track_pattern 5 0 (SIGNAL 2 2)x8",
        "def_track_pattern 6 0 (SIGNAL 2 2)x8",
        "def_track_pattern 3 0 (SIGNAL 2 2)x8",
        "def_track_pattern 4 0 (SIGNAL 2 2)x8",
    ])
    idx = next(i for i, c in enumerate(s.bundles[0].input.candidates)
               if c.type.startswith("TRUNK_H@y200"))
    for c in (f"select_topology 1 {idx + 1}", "run_planner 3", "run_nuts",
              "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    for stage in ("nuts", "dnuts"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            verdict = s._check_design(stage)
        assert verdict["violations"] == 0, (stage, buf.getvalue())


# ── the hier half ─────────────────────────────────────────────────────────
# A multi-rect footprint can also be declared on a BDB CELL (`set_cell_rects`,
# schema v30), and `set_feedthru` is replayed onto every derived generation
# frame — so the relay has to compose with a cell-local template, where the
# block is known by its LOCAL name.  Nothing in the fix is hier-aware; this
# measures the composition rather than assuming it.
_HIER = [
    "def_layer 3 M3 H 20",
    "def_layer 4 M4 V 20",
    "def_layer 5 M5 H TOP 10",
    "def_layer 6 M6 V TOP 10",
    "open_bdb :memory:",
    "add_cell drv    100  80",
    "add_cell relay  200 400",
    "add_cell sink   100  80",
    "add_cell unit   700 500",
    # Two slabs with a gap along x, both spanning the cell's full height —
    # the union is exactly the cell's extent, as set_cell_rects requires.
    "set_cell_rects relay rect 0 0 80 400 rect 120 0 200 400",
    "add_inst_to_cell unit drv_i   drv    30 200",
    "add_inst_to_cell unit relay_i relay 250  50",
    "add_inst_to_cell unit sink_i  sink  550 200",
    "add_inst u0 unit - 100 100",
    "derive_busterms 1",
    "add_blocks_from_bdb 0",
    "add_blocks_from_bdb 1 skip",
    "bdb_net_mode on",
    "add_bus d0[4] u0/drv_i.out u0/relay_i.in,u0/sink_i.in",
]


def _hier_relay_candidates(feedthru):
    cmds = list(_HIER)
    if feedthru:
        # The cell-local frame knows the child by its LOCAL name.
        cmds.append("set_feedthru relay_i *")
    cmds += ["run_hier_bundler", "generate_hier_topologies"]
    s, out = _session(cmds)
    return [c for w in s.bundles for c in w.input.candidates
            if list(c.feedthru_blocks)], out


def test_the_relay_composes_with_a_cell_declared_multirect_footprint():
    with_ft, out = _hier_relay_candidates(True)
    assert "BUDA-1908" not in out, out          # THRU cell: not warned
    assert with_ft, "no candidate relays through the cell-local macro"
    assert all(list(c.feedthru_blocks) == ["relay_i"] for c in with_ft)
    # Control: the same design without the declaration relays nowhere.
    without, _ = _hier_relay_candidates(False)
    assert without == []
