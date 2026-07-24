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

"""Web backend Phase 1 — struct -> JSON render serializers.

Drives a BudaSession through the canonical b44 flow (same pattern as
test_tug_of_war.py) and asserts the serializers produce well-typed,
JSON-round-trippable payloads with the expected fields and sentinel handling.
"""
import io
import contextlib
import json
import os

import buda
import buda_cli
from web import runner, serialize

_GOLDEN = os.path.join(os.path.dirname(__file__), "data", "web_golden",
                       "b44_generation.json")


_B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _run(s, cmds):
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in cmds:
            runner.run_one(s, c)


def _b44_generated():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, _B44_SETUP + ["run_bundler", "generate_topologies"])
    return s


def _b44_routed():
    s = _b44_generated()
    _run(s, ["run_planner", "run_nuts", "run_detailed_nuts"])
    return s


def _json_roundtrip(obj):
    """Assert the payload is pure JSON (no leaked C++ objects) and return it."""
    return json.loads(json.dumps(obj))


def test_serialize_floorplan_shape():
    s = _b44_generated()
    fp = _json_roundtrip(serialize.serialize_floorplan(s.fp))
    names = {b["name"] for b in fp["blocks"]}
    assert {"blk_07", "blk_23", "io_pad_tl"} <= names
    blk = next(b for b in fp["blocks"] if b["name"] == "blk_07")
    assert blk["bbox"] == {"x1": 2960, "y1": 9750, "x2": 4660, "y2": 10250}
    assert "corner_margin" in blk and "is_container" in blk
    assert isinstance(fp["keepouts"], list)


def test_serialize_floorplan_multi_rect_block():
    """A multi-rect / TEG block serializes its REAL rects (not just the bbox), so
    the client can draw the solid rects and leave the gaps open rather than
    painting the whole bbox solid (Codex P2)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ["add_block solid 0 0 100 100",
             "add_block teg rect 0 0 40 100 rect 60 0 100 100"])  # gap 40..60
    fp = _json_roundtrip(serialize.serialize_floorplan(s.fp))
    solid = next(b for b in fp["blocks"] if b["name"] == "solid")
    teg = next(b for b in fp["blocks"] if b["name"] == "teg")
    assert solid["rects"] == []                     # single-rect: draw the bbox
    assert len(teg["rects"]) == 2                    # two real rects with a gap
    assert [0, 0, 40, 100] in teg["rects"]
    assert [60, 0, 100, 100] in teg["rects"]


def test_serialize_hanan_shape():
    s = _b44_generated()
    h = _json_roundtrip(serialize.serialize_hanan(s.fp))
    assert h["xs"] and h["ys"]
    assert all(isinstance(v, int) for v in h["xs"] + h["ys"])


def test_serialize_topology_and_analysis():
    s = _b44_generated()
    w = s.bundles[0]
    fp = serialize.bundle_floorplan(s, w)
    cand = next(c for c in w.input.candidates
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)
    t = _json_roundtrip(serialize.serialize_topology(cand, fp))
    assert t["type"] == "TRUNK_H+MST@y11915"
    assert t["estimated_wirelength"] == 3510
    assert len(t["segments"]) == len(t["analysis"]) == 6   # index-aligned
    seg0 = t["segments"][0]
    assert set(seg0) >= {"start", "end", "horiz", "layer_hint", "is_jog"}
    # the canonical tug segment: seg5 has two SEG-conn riders (seg1, seg3)
    a5 = t["analysis"][5]
    riders = {c["seg_idx"] for c in a5["conns"] if c["kind"] == "SEG"}
    assert {1, 3} <= riders
    # SEG conns carry no face_coord garbage
    for a in t["analysis"]:
        for c in a["conns"]:
            if c["kind"] == "SEG":
                assert c["face_coord"] is None


def test_free_slide_windows_serialize_as_null():
    """A free (unbounded) ConnTopology slide window keeps the sentinel; it must
    become JSON null (not a ~1e9 int) so the client can tell 'unconstrained' from
    a bound.  The surviving free-window carriers are the LEGIT OOB detour trunks
    (each open on its outward side) — the redundant OOB+MST hybrids that used to
    carry many free legs are now dropped at generation by the seed-trunk
    removability gate."""
    import buda
    s = _b44_generated()
    w = s.bundles[0]
    fp = serialize.bundle_floorplan(s, w)
    # pick any candidate with at least one free slide window
    target = None
    for c in w.input.candidates:
        ct = buda.ConnTopology(); ct.build(c, s.fp)
        if any(abs(cs.perp_lo) >= serialize._SENTINEL or
               abs(cs.perp_hi) >= serialize._SENTINEL for cs in ct.segs()):
            target = c; break
    assert target is not None, "expected a candidate with a free slide window"
    t = _json_roundtrip(serialize.serialize_topology(target, fp))
    nulls = sum(1 for a in t["analysis"]
                if a["perp_lo"] is None or a["perp_hi"] is None)
    assert nulls >= 1    # the free leg(s) serialized as null, not a sentinel int
    # no residual sentinel-magnitude ints leaked through
    for a in t["analysis"]:
        for k in ("perp_lo", "perp_hi"):
            assert a[k] is None or abs(a[k]) < serialize._SENTINEL


def test_serialize_generation_compose():
    s = _b44_generated()
    payload = _json_roundtrip(serialize.serialize_generation(s))
    assert set(payload) == {"floorplan", "hanan", "bundles", "state"}
    assert payload["state"]["stages_run"]["topologies"] is True
    b = payload["bundles"][0]
    assert b["id"] == 1 and len(b["net_names"]) == 52
    assert len(b["candidates"]) == len(s.bundles[0].input.candidates)


def test_serialize_generation_focused_candidate():
    s = _b44_generated()
    payload = _json_roundtrip(
        serialize.serialize_generation(s, bundle_id=1, candidate=2))
    b = payload["bundles"][0]
    assert len(b["candidates"]) == 1
    assert b["candidate_offset"] == 2


def test_serialize_nuts():
    s = _b44_routed()
    payload = _json_roundtrip(serialize.serialize_render_nuts(s))
    assert payload["state"]["stages_run"]["nuts"] is True
    n = payload["nuts"]
    assert n["num_overlaps"] == 0            # b44 places cleanly
    assert len(n["segments"]) >= 1
    ts = n["segments"][0]
    assert set(ts) >= {"bundle_id", "seg_idx", "layer", "horiz",
                       "span_lo", "span_hi", "track_position", "width", "placed"}
    assert ts["placed"] is True


def test_serialize_detailed_and_bit_orientation():
    s = _b44_routed()
    payload = _json_roundtrip(serialize.serialize_render_detailed(s))
    d = payload["detailed"]
    assert d["num_unplaced"] == 0
    assert len(d["net_segments"]) == 104     # 52 bits x 2 bus segments
    assert len(d["net_vias"]) == 52          # one per bit at the L bend
    # every bit-wire inherits an orientation from its bus segment (not null)
    assert all(ns["horiz"] in (True, False) for ns in d["net_segments"])
    via = d["net_vias"][0]
    assert via["from_layer"] != via["to_layer"]
    assert set(via) >= {"bundle_id", "from_seg", "to_seg", "bit_index", "x", "y"}


def test_render_none_before_stages():
    """The result serializers are null-safe before the stage has run."""
    s = _b44_generated()          # planned/routed stages NOT run
    assert serialize.serialize_nuts(None) is None
    assert serialize.serialize_detailed(None) is None
    rn = _json_roundtrip(serialize.serialize_render_nuts(s))
    assert rn["nuts"] is None


def test_generation_golden_snapshot():
    """The generation render payload (floorplan + all b44 candidates + their
    ConnTopology analysis) is byte-frozen.  This catches any serializer drift AND
    is the reference the ported Renderer/DisplayGeom is validated against, so the
    client math cannot silently diverge from the server payload.  The payload is
    integer-valued at this stage (no NUTS float placement), so it is host-stable.

    Regenerate on an intended change:
        python -c "import io,contextlib,json,buda,buda_cli; \\
          from web import runner,serialize; s=buda_cli.BudaSession(); s.no_viz=True; \\
          [runner.run_one(s,c) for c in SETUP+['run_bundler','generate_topologies']]; \\
          p=serialize.serialize_generation(s); p.pop('state',None); \\
          open('test/tests/data/web_golden/b44_generation.json','w').write(
              json.dumps(p,indent=1,sort_keys=True)+chr(10))"
    """
    s = _b44_generated()
    payload = serialize.serialize_generation(s)
    payload.pop("state", None)
    payload = _json_roundtrip(payload)          # normalize to JSON scalars
    with open(_GOLDEN) as f:
        golden = json.load(f)
    assert payload == golden, (
        "generation render payload drifted from the golden snapshot; if "
        "intended, regenerate test/tests/data/web_golden/b44_generation.json")
