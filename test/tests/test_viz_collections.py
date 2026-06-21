"""Regression tests for the buda_viz rendering-performance refactor.

On large designs the visualizer used to create tens of thousands of individual
matplotlib artists (one per bit-wire, one per heatmap cell, one per overflow
label), making the interactive window take minutes to load.  The refactor:

  * defaults the congestion heatmap OFF,
  * collapses the heatmap into a single PatchCollection + capped text labels,
  * builds the detailed bit-wire / rail artists lazily (first [Detailed] click),
  * groups bit-wires and NUTS interval bands into collections.

These tests pin those behaviors so they don't silently regress.  They build a
real BudaVisualizer over a tiny flow with the Agg backend (no window shown).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from pathlib import Path

import pytest

pytestmark = pytest.mark.mid

_ROOT = Path(__file__).parents[2]


def _build_viz(flow_name, monkeypatch):
    """Run a .buda flow through the CLI and return the BudaVisualizer it builds,
    with plt.show() neutralized so nothing blocks or opens a window."""
    import buda_cli
    import buda_viz

    monkeypatch.setattr(plt, "show", lambda *a, **k: None)

    captured = {}
    orig_show = buda_viz.BudaVisualizer.show

    def cap_show(self):
        # Disable IPC: its import (viz_ipc from tools/) and Unix-socket setup
        # are not on the test path and not what these tests exercise.
        self._ipc_session = None
        orig_show(self)
        captured["viz"] = self

    monkeypatch.setattr(buda_viz.BudaVisualizer, "show", cap_show)

    sess = buda_cli.BudaSession()
    sess.do_command(f"source {_ROOT / 'flow' / flow_name}")
    assert "viz" in captured, "visualize did not build a BudaVisualizer"
    return captured["viz"]


def test_heatmap_off_by_default():
    from ui_state import ViewState
    assert ViewState().heatmap is False


def test_heatmap_is_single_collection_and_hidden(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    # The heatmap collapses to one PatchCollection (+ at most _HEATMAP_LABEL_CAP
    # text labels), and starts hidden because heatmap defaults off.
    pcs = [a for a in viz._heatmap_artists if isinstance(a, PatchCollection)]
    assert len(pcs) == 1, "heatmap cells should be one PatchCollection"
    assert not any(a.get_visible() for a in viz._heatmap_artists), \
        "heatmap must be hidden by default"
    n_labels = sum(1 for a in viz._heatmap_artists if not isinstance(a, PatchCollection))
    assert n_labels <= viz._HEATMAP_LABEL_CAP


def test_detailed_artists_built_lazily(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    # Data is registered but no detailed artists exist until the view is opened.
    assert viz._has_detailed_data is True
    assert viz._detailed_built is False
    assert viz._detailed_bundle_artists == {}
    assert viz._grid_rail_artists == []

    viz._toggle_detailed()  # opens the detailed view → builds bit-wires only

    assert viz._detailed_built is True
    assert viz._detailed_bundle_artists, "bit-wire collections should now exist"
    # Bit-wires are LineCollections (one per bundle×layer), not per-bit Line2D.
    for entries in viz._detailed_bundle_artists.values():
        for e in entries:
            assert isinstance(e["artist"], LineCollection)

    # Background rail stripes (Tracks) are off by default and must NOT be built
    # just because the detailed view opened — they are deferred to [Tracks].
    assert viz.ui_state.tracks is False
    assert viz._rails_built is False
    assert viz._grid_rail_artists == []

    viz._toggle_tracks()  # first [Tracks] enable → builds the rail stripes
    assert viz._rails_built is True
    assert viz._grid_rail_artists, "rail collections should exist after Tracks on"
    for e in viz._grid_rail_artists:
        assert isinstance(e["artist"], PatchCollection)
        assert e["artist"].get_visible()


def test_all_toggle_builds_rails_in_detailed_mode(monkeypatch):
    # Tracks can be enabled via the All toggle (ViewState.toggle_all sets
    # tracks=True and only notifies fig_redraw, bypassing _toggle_tracks).  The
    # lazy rails must still get built on that path.
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_detailed()                 # detailed on; Tracks still off
    assert viz._rails_built is False and viz._grid_rail_artists == []

    viz._toggle_all()                      # All -> OFF (tracks stays off)
    assert viz._rails_built is False
    viz._toggle_all()                      # All -> ON (tracks True via toggle_all)

    assert viz.ui_state.tracks is True
    assert viz._rails_built is True, "All-on must build the lazy rails"
    assert any(e["artist"].get_visible() for e in viz._grid_rail_artists), \
        "rails should be visible after All turns Tracks on in detailed mode"


def test_nuts_bands_are_collections(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    # The NUTS interval bands (footprints + dashed bounds) are batched into
    # collections; the main segment lines stay as Line2D (for the highlight
    # outline).  So every is_band entry must be a collection.
    band_entries = [e for entries in viz._bundle_artists.values()
                    for e in entries if e["is_band"]]
    assert band_entries, "expected NUTS interval-band artists"
    for e in band_entries:
        assert isinstance(e["artist"], (LineCollection, PatchCollection))


def test_step_bundle_reveals_selected_when_all_bundles_off(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)

    viz._on_bundle_toggle_all()                 # turn All Bundles OFF
    assert all(e["artist"].get_alpha() == 0
               for es in viz._bundle_artists.values() for e in es), \
        "All Bundles off should hide every bundle"

    viz._on_key(types.SimpleNamespace(key="n"))  # step to a bundle
    sel = viz._highlighted
    assert sel is not None
    # The selected bus's segs are revealed despite All Bundles being off...
    assert any((e["artist"].get_alpha() or 0) > 0.5
               for e in viz._bundle_artists[sel] if not e["is_band"]), \
        "selected bundle should become visible after n/p"
    # ...and everything else stays hidden (spotlight, not accumulate).
    assert all(e["artist"].get_alpha() == 0
               for bid, es in viz._bundle_artists.items() if bid != sel
               for e in es)

    viz._on_key(types.SimpleNamespace(key="p"))  # move spotlight off `sel`
    assert all(e["artist"].get_alpha() == 0 for e in viz._bundle_artists[sel]), \
        "previously selected bundle returns to hidden when the spotlight moves"


def test_arrow_keys_pan_the_view(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)

    x0 = viz.ax.get_xlim()
    viz._on_key(types.SimpleNamespace(key="right"))
    x1 = viz.ax.get_xlim()
    assert x1[0] > x0[0] and x1[1] > x0[1], "right should pan the view right"
    viz._on_key(types.SimpleNamespace(key="left"))
    assert viz.ax.get_xlim()[0] < x1[0], "left should pan back"

    y0 = viz.ax.get_ylim()
    viz._on_key(types.SimpleNamespace(key="up"))
    y1 = viz.ax.get_ylim()
    assert y1[0] > y0[0], "up should pan the view up"
    viz._on_key(types.SimpleNamespace(key="down"))
    assert viz.ax.get_ylim()[0] < y1[0], "down should pan back"


def test_t_key_toggles_busterms_not_explorer(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._topo_explorer is None
    before = viz.ui_state.busterms
    viz._on_key(types.SimpleNamespace(key="t"))   # 't' = busterms only, NOT TopoExp
    assert viz.ui_state.busterms != before
    assert viz._topo_explorer is None, "'t' must not open the Topology Explorer"
    viz._on_key(types.SimpleNamespace(key="t"))
    assert viz.ui_state.busterms == before


def test_g_key_toggles_hanan_grid(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)
    before = viz.ui_state.hanan_grid
    viz._on_key(types.SimpleNamespace(key="g"))
    assert viz.ui_state.hanan_grid != before
    viz._on_key(types.SimpleNamespace(key="g"))
    assert viz.ui_state.hanan_grid == before


def test_rerun_in_detailed_mode_hides_abstract_tracks(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_detailed()                      # enter detailed mode
    assert viz.ui_state.detailed_mode is True

    # A layer/topology rerun rebuilds both artist sets.  In detailed mode the
    # abstract bus tracks must stay hidden so they don't overlay the bit-wires.
    viz._redraw_nuts_tracks((viz._nuts_result, viz._detailed_result))

    assert all(not e["artist"].get_visible()
               for entries in viz._bundle_artists.values() for e in entries), \
        "abstract NUTS artists must be hidden in detailed mode after a rerun"
    assert any(e["artist"].get_visible()
               for entries in viz._detailed_bundle_artists.values() for e in entries), \
        "detailed bit-wires should be visible after a rerun in detailed mode"


def test_highlight_survives_collection_refactor(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bid = next(iter(viz._bundle_artists))
    viz._set_highlight(bid)            # must not raise on collection artists
    # Selecting a bundle draws white outline lines over its Line2D segments.
    assert viz._highlighted == bid
    assert len(viz._highlight_overlays) > 0
    viz._set_highlight(None)
    assert viz._highlighted is None
