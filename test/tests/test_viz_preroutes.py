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

"""Phase G — the [Preroutes] visualizer layer (draw_preroutes).

Headless (Agg) checks that the layer is registered by cmd_visualize when a
routing grid exists, built LAZILY on the first cycle away from 'off', grouped
per (layer, slot type) as PatchCollections, and that the cycling mode drives
per-type visibility ('off' -> ALL -> POWER -> GROUND -> CLOCK -> SHIELD).
Mirrors the test_viz_collections.py harness.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from pathlib import Path

import pytest

pytestmark = pytest.mark.mid

_ROOT = Path(__file__).parents[2]


def _build_viz(flow_name, monkeypatch):
    import buda_cli
    import buda_viz

    monkeypatch.setattr(plt, "show", lambda *a, **k: None)

    captured = {}
    orig_show = buda_viz.BudaVisualizer.show

    def cap_show(self):
        self._ipc_session = None
        orig_show(self)
        captured["viz"] = self

    monkeypatch.setattr(buda_viz.BudaVisualizer, "show", cap_show)

    sess = buda_cli.BudaSession()
    sess.do_command(f"source {_ROOT / 'flow' / flow_name}")
    assert "viz" in captured, "visualize did not build a BudaVisualizer"
    return captured["viz"]


def test_preroutes_registered_and_lazy(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    # dnuts1 defines track patterns -> the layer is registered but NOT built.
    assert viz._has_preroute_data
    assert not viz._preroutes_built
    assert viz._preroute_artists == []
    assert viz._btn_preroutes is not None
    assert viz._btn_preroutes.ax.get_visible()
    assert viz.ui_state.preroutes_mode == 'off'


def test_preroutes_cycle_builds_and_filters(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._cycle_preroutes()                    # off -> ALL (lazy build here)
    assert viz._preroutes_built
    assert viz._preroute_artists, "no pre-route collections built"
    for e in viz._preroute_artists:
        assert isinstance(e["artist"], PatchCollection)
        assert e["slot_type"] != "SIGNAL"     # non-SIGNAL slots only
        assert e["artist"].get_visible()      # ALL shows every type
    types_present = {e["slot_type"] for e in viz._preroute_artists}
    assert "POWER" in types_present           # dnuts1's pattern has rails

    # Cycle to POWER: only POWER collections remain visible.
    viz._cycle_preroutes()
    assert viz.ui_state.preroutes_mode == 'POWER'
    for e in viz._preroute_artists:
        assert e["artist"].get_visible() == (e["slot_type"] == "POWER")

    # Complete the cycle back to off: everything hidden, no rebuild.
    for _ in range(4):                        # GROUND, CLOCK, SHIELD, off
        viz._cycle_preroutes()
    assert viz.ui_state.preroutes_mode == 'off'
    assert not any(e["artist"].get_visible() for e in viz._preroute_artists)


def test_layer_panel_hides_preroute_bands(monkeypatch):
    """Codex P2 (PR #207): unchecking a metal layer in the layer panel must
    hide that layer's pre-route bands too, not just bundle/rail artists."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._cycle_preroutes()                    # off -> ALL
    layers = {e["layer"] for e in viz._preroute_artists}
    assert layers, "no pre-route layers built"
    lid = sorted(layers)[0]
    viz._on_layer_toggle(lid)                 # uncheck one layer
    for e in viz._preroute_artists:
        expected = e["layer"] != lid
        assert e["artist"].get_visible() == expected, (
            f"layer {e['layer']} band visibility wrong with layer {lid} off")
    viz._on_layer_toggle(lid)                 # re-check: all visible again
    assert all(e["artist"].get_visible() for e in viz._preroute_artists)


def test_tracks_rails_signal_only_layer_colored(monkeypatch):
    """The [Tracks] rail stripes build from the same enumeration as the
    [Preroutes] bands (_track_band_rects over RoutingGridStack.preroutes)
    but keep only the SIGNAL stripes — the non-SIGNAL context is the
    [Preroutes] layer's job, so nothing is double-drawn when both are on.
    Stripes are coloured by their LAYER (the colour of the bit-wires that
    land on them) — the old near-white '#f9f9f9' was invisible."""
    from matplotlib.colors import to_hex
    from buda_viz import _LAYER_COLOR

    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_detailed()                    # rails need detailed mode
    viz._toggle_tracks()                      # lazy rail build here
    assert viz._rails_built and viz._grid_rail_artists
    # One collection per layer, all at the visible SIGNAL base alpha.
    assert {e['alpha'] for e in viz._grid_rail_artists} == {0.20}
    layers = [e['layer'] for e in viz._grid_rail_artists]
    assert len(layers) == len(set(layers)), "expected one collection per layer"
    # Every stripe carries its layer's colour (no pre-route palette entries
    # remain in the rails view).
    for e in viz._grid_rail_artists:
        expected = _LAYER_COLOR.get(e['layer'], '#888888').lower()
        for fc in e['artist'].get_facecolor():
            assert to_hex(fc).lower() == expected


def test_all_toggle_builds_and_shows_preroutes(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_all()                         # all off
    viz._toggle_all()                         # all on -> preroutes_mode ALL
    assert viz.ui_state.preroutes_mode == 'ALL'
    # fig_redraw builds the lazy artists when All turns the mode on without
    # going through _cycle_preroutes, and applies visibility post-build.
    assert viz._preroutes_built
    assert viz._preroute_artists
    assert all(e["artist"].get_visible() for e in viz._preroute_artists)
