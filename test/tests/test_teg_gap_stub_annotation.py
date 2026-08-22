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

"""TEG-over gap-stub retraction — the root-cause regression (both orientations).

The disjoint-gap OVER branch emits a stub per rect to the trunk.  The far
stub was emitted trunk → face while emit_tap_segment seeds the busterm on the
START endpoint, so its tap annotation landed on the TRUNK end:

  1. derive_conn_segs dedupes BUSTERM conns per block, so the bogus trunk-end
     tap (face_coord = trunk locus) shadowed the real face-end annotation
     annotate_endpoints filled into the .second slot;
  2. annotate_seg_conns skips a busterm-tapped endpoint, so the far stub's
     own trunk-junction record was suppressed too (the SEG conn it kept came
     only from the spine's reciprocal — the spine's pre-extended endpoint
     happens to coincide with the stub);
  3. with no face anchor, NUTS span adjustment retracted the stub to the
     trunk (measured: span [150,158] against a face at 300), yielding
     BUSTERM_FACE at nuts and BUSTERM_FACE + ANTENNA + TEG_OPEN at dnuts.

Fixed by emitting the far stub face → trunk like every other stub
(src/topology.cpp, the TEG-over gap emission).  These tests pin the
annotation (the root cause) and the placed reach (the symptom) in BOTH trunk
orientations — the retraction was orientation-independent.
"""
import contextlib
import io

import pytest

import buda
import buda_cli


_TRACKS = [
    "def_track_pattern 4 0 (SIGNAL 2 2)x8",
    "def_track_pattern 5 0 (SIGNAL 2 2)x8",
]

# H-trunk: rects stacked vertically, gap y 100..300, faces at 100 (near) and
# 300 (far).  V-trunk (P-shape): rects side by side, same coordinates on x.
_VEHICLES = {
    "H": ([
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 150 500 250",
    ], "TRUNK_H@y"),
    "V": ([
        "add_block T rect 300 0 400 200 rect 0 0 100 200 teg_mode over",
        "add_block src 150 400 250 500",
    ], "TRUNK_V@x"),
}


def _session(orient):
    blocks, pref = _VEHICLES[orient]
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in blocks + [
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s, pref


def _pin_gap_candidate(s, pref):
    """Pin a gap-trunk candidate (trunk in the 100..300 gap).

    Since the open 1(a) emission redesign the gap shape carries NO bridge —
    the per-rect stubs ARE the connection — so the gap candidate is
    identified by its trunk locus alone."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith(pref):
            v = int(c.type.split(pref[-2:])[1].split("+")[0])
            if 100 < v < 300:
                assert not dict(c.bridge_segments), \
                    "generation must no longer emit bridge_segments"
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                return c
    raise AssertionError("no gap-trunk candidate found")


@pytest.mark.parametrize("orient", ["H", "V"])
def test_gap_stub_taps_annotated_at_faces(orient):
    # ROOT CAUSE pin: each gap stub's BUSTERM conn must sit at its rect's
    # face — {100, 300} — never at the trunk locus.  The wrong-end seed put
    # the far stub's tap at the LOCUS (150 for the pinned candidate), which
    # is what left the face end anchorless.
    s, pref = _session(orient)
    cand = _pin_gap_candidate(s, pref)
    locus = cand.trunk_location
    ct = buda.ConnTopology()
    ct.build(cand, s.fp)
    taps = {}
    for si, cs in enumerate(ct.segs()):
        for c in cs.conns:
            if c.kind == buda.SegConnKind.BUSTERM and c.block_name == "T":
                taps[si] = c.face_coord
    assert sorted(taps.values()) == [100, 300], (locus, taps)
    assert locus not in taps.values()
    # Each gap stub must ALSO hold a junction to the spine (seg 0): the
    # wrong-end seed suppressed the far stub's own record and it survived
    # only by the spine-endpoint reciprocal — a coincidence of the
    # pre-extension, not a guarantee.
    for si in taps:
        assert any(c.kind == buda.SegConnKind.SEG and c.seg_idx == 0
                   for c in ct.segs()[si].conns), si


@pytest.mark.parametrize("orient", ["H", "V"])
def test_gap_stubs_place_to_their_faces(orient):
    # SYMPTOM pin: after planner + NUTS the two gap stubs' placed spans must
    # reach their faces (100 and 300) — the far stub used to retract to the
    # trunk (span [150,158]).  Both audits must be clean of the retraction
    # kinds.
    s, pref = _session(orient)
    _pin_gap_candidate(s, pref)
    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    stub_ends = set()
    for ts in s.nuts_result.segments:
        if ts.seg_idx != 0:
            stub_ends.update((ts.span_lo, ts.span_hi))
    assert 100.0 in stub_ends and 300.0 in stub_ends, stub_ends

    def verdict(stage):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            v = s._check_design(stage)
        return v["by_kind"], buf.getvalue()

    kinds, out = verdict("nuts")
    for k in ("BUSTERM_FACE", "ANTENNA", "TEG_OPEN"):
        assert kinds.get(k, 0) == 0, (k, out)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    kinds, out = verdict("dnuts")
    for k in ("BUSTERM_FACE", "ANTENNA", "TEG_OPEN"):
        assert kinds.get(k, 0) == 0, (k, out)
