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

"""Legacy-load TEG bridge overlay (teg_multirect_status.md open 10(a)).

Since open 1(a), generation emits TEG-over connection metal as ordinary
segments and `Topology.bridge_segments` is non-empty ONLY on a candidate
restored from a pre-emission checkpoint — where the bridge is UNREALIZED
metal that TEG_OPEN reports as "declared bridge is unrealized" while no
renderer drew the wire the message names.  The matplotlib explorer and the
main viewer now draw restored bridges as a dashed, labeled
"unrealized bridge (legacy checkpoint)" overlay through ONE shared helper
(`viz_common.draw_legacy_bridges`); a bridge-less topology (every generated
candidate) draws none — byte-identical viz for every live design.

Headless (Agg), in the test_explorer_multirect.py style: construct the
viewers, assert the artists.
"""
import contextlib
import io

import matplotlib
matplotlib.use("Agg")   # headless; no window

import pytest

import buda
import buda_cli
import buda_viz


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    import matplotlib.pyplot as plt
    plt.close('all')


def _lshape_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in [
        "add_block src 500 150 600 250",
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_net clk src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies_for_bundle clk src L",
        "run_planner",
        "run_nuts",
    ]:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _legacy_bridge():
    """The §1.1 union-face bridge shape, as a pre-emission checkpoint would
    restore it: a V segment on the union bbox's right face."""
    seg = buda.Segment()
    seg.start = buda.Point(400, 0)
    seg.end = buda.Point(400, 400)
    seg.layer_hint = 5
    return seg


def _inject_bridge_everywhere(s):
    """Simulate the legacy-load state: every candidate carries the restored
    bridge (so whichever candidate a viewer shows is bridged).  The
    candidates list is value-converted by the binding, so mutate copies and
    write the list back."""
    for w in s.bundles:
        cands = list(w.input.candidates)
        for c in cands:
            c.bridge_segments = {"L": _legacy_bridge()}
        w.input.candidates = cands


def _bridge_lines(artists):
    return [a for a in artists if getattr(a, "get_gid", lambda: None)()
            == "legacy_bridge" and hasattr(a, "get_linestyle")
            and not hasattr(a, "get_text")]


def _bridge_labels(artists):
    return [a for a in artists if hasattr(a, "get_text")]


def test_explorer_draws_injected_legacy_bridge(tmp_path):
    s = _lshape_session()
    _inject_bridge_everywhere(s)
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sel.json"),
        layer_stack=s.layers)
    artists = exp._legacy_bridge_artists
    lines = _bridge_lines(artists)
    assert lines, "explorer must draw the restored bridge"
    ln = lines[0]
    assert ln.get_linestyle() != '-', "the overlay must be dashed"
    xs, ys = ln.get_xdata(), ln.get_ydata()
    assert (list(xs), list(ys)) == ([400, 400], [0, 400])
    labels = _bridge_labels(artists)
    assert labels and "unrealized bridge (legacy checkpoint)" in \
        labels[0].get_text()
    assert "L" in labels[0].get_text()


def test_explorer_draws_no_bridge_for_generated_candidates(tmp_path):
    # Every generated candidate has empty bridge_segments since open 1(a);
    # the overlay must add NO artists (byte-identical viz for live designs).
    s = _lshape_session()
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sel.json"),
        layer_stack=s.layers)
    assert exp._legacy_bridge_artists == []


def test_main_viewer_draws_and_registers_the_bridge():
    s = _lshape_session()
    _inject_bridge_everywhere(s)
    v = buda_viz.BudaVisualizer(s.fp, s.bundles, layer_stack=s.layers)
    v.draw_buses()
    lines = _bridge_lines(v._legacy_bridge_artists)
    assert lines, "main viewer (abstract view) must draw the restored bridge"
    # Registered in the artist registry, so click-to-highlight dims and
    # brightens the bridge with its bundle.
    bid = s.bundles[0].input.original_bundle.id
    registered = {id(e['artist']) for e in v._bundle_artists.get(bid, [])}
    assert id(lines[0]) in registered
    assert _bridge_labels(v._legacy_bridge_artists)


def test_main_viewer_nuts_view_draws_the_bridge_too():
    # The audit that names the bridge fires at the PLACED stages, so the
    # placed (NUTS) view must show the unrealized wire as well — at its
    # recorded nominal coordinates, since it is unplaced by definition.
    s = _lshape_session()
    _inject_bridge_everywhere(s)
    v = buda_viz.BudaVisualizer(s.fp, s.bundles, layer_stack=s.layers)
    v.draw_nuts_tracks(s.nuts_result)
    assert _bridge_lines(v._legacy_bridge_artists)


def test_reroute_redraw_does_not_accumulate_bridge_artists():
    # Codex P2 on #834: the reroute path (`_redraw_nuts_tracks`) removes
    # registry artists — the bridge LINES — but the LABEL annotations are
    # not registered, so resetting the list alone orphaned the old text and
    # every interactive reroute of a restored legacy checkpoint stacked a
    # duplicate stale label (the Codex #484 endpoint-label shape).
    # `_clear_legacy_bridges` now detaches lines AND labels before redraw.
    s = _lshape_session()
    _inject_bridge_everywhere(s)
    v = buda_viz.BudaVisualizer(s.fp, s.bundles, layer_stack=s.layers)
    v.draw_nuts_tracks(s.nuts_result)

    def on_axes():
        lines = [a for a in v.ax.lines if a.get_gid() == 'legacy_bridge']
        labels = [t for t in v.ax.texts if t.get_gid() == 'legacy_bridge']
        return len(lines), len(labels)

    first = on_axes()
    assert first == (1, 1), first
    # Two interactive reroutes: the axes must hold exactly one line and one
    # label after each, never an accumulation.
    v._redraw_nuts_tracks(s.nuts_result)
    assert on_axes() == first
    v._redraw_nuts_tracks(s.nuts_result)
    assert on_axes() == first


def test_main_viewer_draws_none_without_bridges():
    s = _lshape_session()
    v = buda_viz.BudaVisualizer(s.fp, s.bundles, layer_stack=s.layers)
    v.draw_buses()
    assert v._legacy_bridge_artists == []
    v.draw_nuts_tracks(s.nuts_result)
    assert v._legacy_bridge_artists == []
