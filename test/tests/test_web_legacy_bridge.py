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

"""Legacy-load TEG bridges in the WEB clients
(teg_multirect_status.md limitation 6).

`src/web/serialize.py` already emitted a restored candidate's
`bridge_segments` and no web renderer drew them, while the matplotlib explorer
and main viewer both do (`viz_common.draw_legacy_bridges`, pinned by
test_viz_legacy_bridge.py).  A bridge is UNREALIZED metal — the wire the
`TEG_OPEN` audit names as "declared bridge is unrealized" — so the web user
read a message about a wire nothing on screen showed.

The fixture here is a REAL restore, not a hand-made JSON stub: the build
session declares the OVER L-shape, puts the legacy bridge on every candidate,
persists through the session's own `_persist_topologies` (the v11
`topology_bridge_segment` rows a pre-#828 writer wrote), plans and solves NUTS,
saves — and a SECOND session re-declares the setup, opens the same checkpoint
and `load_pipeline`s it.  Everything asserted below therefore comes out of the
real persist/restore path and the real serializer.

Two payloads carry bridges:

* the GENERATION payload, per candidate (`serialize_topology`), which the
  serializer already emitted; and
* the PLACED (NUTS) payload, which has no candidate list at all — the selected
  candidate's bridges are emitted as a flat `legacy_bridges` list, mirroring
  what the matplotlib main viewer draws in ITS nuts view (the audit that names
  the bridge fires at the placed stage).

The JS half is EXECUTED under node in the `test_web_js_port.py` idiom: the pure
`legacyBridgeWires` is extracted from `index.html` and run over the real
serialized payloads.  The Scala.js half cannot be executed here (Renderer.scala
needs scalajs-dom, and the link step is the open recorded in
docs/internal/opens_ci.md §4), so it is held by a source-parity assertion —
stated as such rather than dressed up as an execution test.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_web_js_port import (  # noqa: E402 — one extractor, one idiom
    _NO_NODE, _node, extract_js_function)

import buda           # noqa: E402
import buda_cli       # noqa: E402
from web import serialize   # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_INDEX_HTML = os.path.join(_ROOT, "src", "web", "static", "index.html")
_SCALA_INDEX = os.path.join(_ROOT, "src", "web", "static", "scala", "index.html")
_SCALA_RENDERER = os.path.join(
    _ROOT, "web", "src", "main", "scala", "buda", "web", "render",
    "Renderer.scala")
_VIZ_COMMON = os.path.join(_ROOT, "src", "viz_common.py")

#: The one phrase all three renderers annotate a restored bridge with.
_LABEL = "unrealized bridge (legacy checkpoint)"

# The §1.1 union-face shape: an L-shaped OVER block whose union bbox has a
# right face at x=400 — where a pre-emission generator parked the bridge.
_SETUP = [
    "add_block src 500 150 600 250",
    "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
    "def_layer 4 M4 H TOP 0",
    "def_layer 5 M5 V TOP 0",
    "add_net clk src.tx L.rx",
]
_BRIDGE_XY = (400, 0, 400, 400)


def _run(session, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        session.do_command(cmd)
    return buf.getvalue()


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        _run(s, c)
    return s


def _legacy_segment():
    sg = buda.Segment()
    x1, y1, x2, y2 = _BRIDGE_XY
    sg.start = buda.Point(x1, y1)
    sg.end = buda.Point(x2, y2)
    sg.layer_hint = 5
    return sg


def _routed_session(tmp_path, with_bridges):
    """A ROUTED session whose candidates came back through the BDB.

    `with_bridges=True` reproduces a pre-#828 checkpoint: the bridge is put on
    every candidate and persisted through the session's own topology-persist
    choke point, so the checkpoint holds real `topology_bridge_segment` rows and
    the restored candidates' `topo_uid`s still match (no lossy-reload warning).
    `with_bridges=False` is the same design with nothing injected — the control
    every live design is.
    """
    ck = str(tmp_path / ("bridged.bdb" if with_bridges else "clean.bdb"))
    s = _session(_SETUP)
    _run(s, f"open_bdb {ck}")
    _run(s, "run_bundler STRICT")
    _run(s, "generate_topologies_for_bundle clk src L")
    if with_bridges:
        for w in s.bundles:
            cands = list(w.input.candidates)
            for c in cands:
                c.bridge_segments = {"L": _legacy_segment()}
            w.input.candidates = cands
        s._persist_topologies()
    _run(s, "run_planner")
    _run(s, "run_nuts")
    _run(s, "save_bdb")

    s2 = _session(_SETUP)
    _run(s2, f"open_bdb {ck}")
    out = _run(s2, "load_pipeline")
    assert "rehydrated" in out, out
    assert "lossy checkpoint" not in out, out
    return s2


# ── the restore itself ───────────────────────────────────────────────────────
def test_restore_brings_the_bridge_back_and_teg_open_fires(tmp_path):
    """The fixture's premise, asserted rather than assumed: the checkpoint
    really does restore bridges, and the audit really does name them."""
    s = _routed_session(tmp_path, with_bridges=True)
    bridged = [c for w in s.bundles for c in w.input.candidates
               if len(c.bridge_segments)]
    assert bridged, "no candidate came back with a bridge — fixture is inert"
    audit = _run(s, "check_design")
    assert "declared bridge is unrealized" in audit, audit


# ── serializer ───────────────────────────────────────────────────────────────
def test_generation_payload_carries_the_restored_bridge(tmp_path):
    s = _routed_session(tmp_path, with_bridges=True)
    gen = serialize.serialize_generation(s)
    cands = gen["bundles"][0]["candidates"]
    brs = [c["bridge_segments"] for c in cands if c["bridge_segments"]]
    assert brs, "generation payload lost the restored bridge"
    b = brs[0]["L"]
    assert (b["start"]["x"], b["start"]["y"],
            b["end"]["x"], b["end"]["y"]) == _BRIDGE_XY
    assert b["layer_hint"] == 5


def test_nuts_payload_carries_the_selected_candidate_bridge(tmp_path):
    """The placed payload has no candidate list, so the selected candidate's
    bridges ride a flat `legacy_bridges` list — what the matplotlib NUTS view
    draws, and what the web NUTS view had no way to see."""
    s = _routed_session(tmp_path, with_bridges=True)
    payload = serialize.serialize_render_nuts(s)
    assert "legacy_bridges" in payload, \
        "the placed payload carries no bridges at all"
    brs = payload["legacy_bridges"]
    assert len(brs) == 1, brs
    b = brs[0]
    assert b["block_name"] == "L"
    assert (b["start"]["x"], b["start"]["y"],
            b["end"]["x"], b["end"]["y"]) == _BRIDGE_XY
    assert b["bundle_id"] == s.bundles[0].input.original_bundle.id
    # The placed segments are still there — the overlay is additive.
    assert payload["nuts"]["segments"]


def test_live_design_carries_no_bridges_in_either_payload(tmp_path):
    """Generation emits no bridges since open 1(a), so a live design's payloads
    must be bridge-free — the web render is unchanged for every real design."""
    s = _routed_session(tmp_path, with_bridges=False)
    gen = serialize.serialize_generation(s)
    assert all(not c["bridge_segments"]
               for c in gen["bundles"][0]["candidates"])
    assert serialize.serialize_render_nuts(s)["legacy_bridges"] == []


# ── the JS client, EXECUTED ──────────────────────────────────────────────────
def _run_js(payloads):
    with open(_INDEX_HTML) as fh:
        src = fh.read()
    driver = extract_js_function("legacyBridgeWires", src) + """
const input = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify(input.map(legacyBridgeWires)));
"""
    with tempfile.TemporaryDirectory() as td:
        js = os.path.join(td, "driver.js")
        data = os.path.join(td, "in.json")
        with open(js, "w") as fh:
            fh.write(driver)
        with open(data, "w") as fh:
            json.dump(payloads, fh)
        p = subprocess.run([_node, js, data], capture_output=True, text=True)
    assert p.returncode == 0, f"node failed:\n{p.stderr}"
    return json.loads(p.stdout)


@pytest.mark.skipif(_node is None, reason=_NO_NODE)
def test_js_client_turns_both_payload_shapes_into_the_same_wire(tmp_path):
    """Run the browser's own `legacyBridgeWires` over the REAL serializer
    output — the generation map AND the placed list — and pin the geometry and
    the label it draws."""
    s = _routed_session(tmp_path, with_bridges=True)
    gen = serialize.serialize_generation(s)
    sel = gen["bundles"][0]["selected_index"]
    gen_map = gen["bundles"][0]["candidates"][sel]["bridge_segments"]
    nuts_list = serialize.serialize_render_nuts(s)["legacy_bridges"]
    from_map, from_list = _run_js([gen_map, nuts_list])

    x1, y1, x2, y2 = _BRIDGE_XY
    for got, where in ((from_map, "generation map"), (from_list, "nuts list")):
        assert len(got) == 1, (where, got)
        w = got[0]
        assert (w["x1"], w["y1"], w["x2"], w["y2"]) == (x1, y1, x2, y2), where
        assert w["label"] == f"{_LABEL}: L", where
    assert from_map == from_list, "the two payload shapes must draw the same wire"


@pytest.mark.skipif(_node is None, reason=_NO_NODE)
def test_js_client_draws_nothing_for_a_live_design(tmp_path):
    """A live design's empty map / empty list must produce no wire at all —
    the byte-identical-render control.  Absent and null too: an older server
    build sends neither field."""
    s = _routed_session(tmp_path, with_bridges=False)
    gen = serialize.serialize_generation(s)
    sel = gen["bundles"][0]["selected_index"]
    cases = [gen["bundles"][0]["candidates"][sel]["bridge_segments"],
             serialize.serialize_render_nuts(s)["legacy_bridges"],
             None]
    assert _run_js(cases) == [[], [], []]


def test_js_client_calls_the_overlay_from_both_views():
    """The pure function is executed above; these are its call sites, which a
    node run of a pure function cannot see."""
    with open(_INDEX_HTML) as fh:
        src = fh.read()
    gen = extract_js_function("drawGeneration", src)
    nuts = extract_js_function("drawNuts", src)
    assert "drawLegacyBridges(g, cand.bridge_segments)" in gen, \
        "the generation view stopped drawing restored bridges"
    assert "drawLegacyBridges(g, RENDER.legacy_bridges)" in nuts, \
        "the placed view stopped drawing restored bridges"


# ── three renderers, one visual language ─────────────────────────────────────
def test_all_three_renderers_share_the_label_and_the_colour():
    """matplotlib, the JS reference client and the Scala.js client draw the same
    thing, so they must say the same thing in the same colour.

    The Scala half is a SOURCE assertion, not an execution: `Renderer.scala`
    needs scalajs-dom and the link step is still open (docs/internal/opens_ci.md
    §4), so nothing here runs it.  It is cheap and it catches the one failure
    that actually happens — one client updated, the other not (issue #554)."""
    # The LABEL is written where the wire is drawn: the Python helper, the JS
    # function, the Scala one.
    for path in (_VIZ_COMMON, _INDEX_HTML, _SCALA_RENDERER):
        assert _LABEL in open(path).read(), \
            f"{path} does not name a restored bridge"
    # The COLOUR is written where the style lives: the Python helper's constant
    # and each web client's own stylesheet (the Scala renderer only names the
    # class, so its colour is in static/scala/index.html).
    for path in (_VIZ_COMMON, _INDEX_HTML, _SCALA_INDEX):
        assert "#cc3344" in open(path).read(), \
            f"{path} uses a different bridge colour"
    scala = open(_SCALA_RENDERER).read()
    assert 'drawLegacyBridges(g, c.selectDynamic("bridge_segments"))' in scala
    assert 'drawLegacyBridges(g, payload.selectDynamic("legacy_bridges"))' in scala
    # Both web clients must carry the CSS their renderers reference.
    for page in (_INDEX_HTML, _SCALA_INDEX):
        css = open(page).read()
        assert re.search(r"\.legacybridge\s*\{", css), page
        assert re.search(r"\.legacybridgelbl\s*\{", css), page
