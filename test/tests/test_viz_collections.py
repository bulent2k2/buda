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
from matplotlib.collections import LineCollection, PatchCollection, PathCollection
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
    # Bit-wires are LineCollections (one per bundle×layer) and per-bit vias are
    # PathCollections (one per bundle×upper-layer) — never per-bit/per-via
    # individual artists.
    for entries in viz._detailed_bundle_artists.values():
        for e in entries:
            assert isinstance(e["artist"], (LineCollection, PathCollection))

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


def test_s_key_toggles_solo(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)
    before = viz.ui_state.solo
    viz._on_key(types.SimpleNamespace(key="s"))
    assert viz.ui_state.solo != before, "'s' should toggle Solo mode"
    viz._on_key(types.SimpleNamespace(key="s"))
    assert viz.ui_state.solo == before


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


def test_a_key_toggles_highlight(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bid = next(iter(viz._bundle_artists))
    
    # 1. Start in abstract mode with no highlight
    assert viz._highlighted is None
    
    # 2. Highlight a bundle
    viz._set_highlight(bid)
    assert viz._highlighted == bid
    assert viz._last_highlighted == bid
    
    # 3. Press 'a' -> should clear highlight (resets view in abstract mode)
    viz._on_key(types.SimpleNamespace(key="a"))
    assert viz._highlighted is None
    assert viz._last_highlighted == bid
    
    # 4. Press 'a' again -> should restore highlight
    viz._on_key(types.SimpleNamespace(key="a"))
    assert viz._highlighted == bid
    
    # 5. Switch to detailed mode
    viz._toggle_detailed()
    assert viz.ui_state.detailed_mode is True
    
    # 6. Highlight is currently active. Press 'a' -> should clear highlight in detailed mode
    viz._on_key(types.SimpleNamespace(key="a"))
    assert viz._highlighted is None
    assert viz._last_highlighted == bid
    
    # 7. Press 'a' again -> should restore highlight in detailed mode
    viz._on_key(types.SimpleNamespace(key="a"))
    assert viz._highlighted == bid


def test_detailed_vias_are_grouped_collections(monkeypatch):
    # Per-bit vias (NetVia) collapse into one PathCollection per
    # (bundle, upper layer) — the perf pin: never one artist per via.
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._detailed_via_artists == []          # lazy, like the bit-wires

    viz._toggle_detailed()
    net_vias = list(viz._detailed_result.net_vias)
    assert net_vias, "flow should produce per-bit vias (layer transitions exist)"
    assert viz._detailed_via_artists
    assert all(isinstance(a, PathCollection) for a in viz._detailed_via_artists)
    n_points = sum(len(a.get_offsets()) for a in viz._detailed_via_artists)
    assert n_points == len(net_vias)                # every via drawn exactly once
    groups = {(v.bundle_id, max(v.from_layer, v.to_layer)) for v in net_vias}
    assert len(viz._detailed_via_artists) == len(groups)


def test_detailed_vias_gated_by_vias_conns_and_detailed(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_detailed()                          # detailed on, vias_conns on
    assert viz.ui_state.vias_conns is True
    assert all(a.get_visible() for a in viz._detailed_via_artists)

    viz._toggle_vias_conns()                        # Vias/Conns off -> hidden
    assert not any(a.get_visible() for a in viz._detailed_via_artists)
    viz._toggle_vias_conns()                        # back on -> visible
    assert all(a.get_visible() for a in viz._detailed_via_artists)

    viz._toggle_detailed()                          # leave detailed mode -> hidden
    assert not any(a.get_visible() for a in viz._detailed_via_artists)


def test_entering_detailed_respects_vias_conns_off(monkeypatch):
    # The detailed toggle's bulk reveal of _detailed_bundle_artists must not
    # leak the via scatters while Vias/Conns is off.
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_vias_conns()                        # off BEFORE detailed opens
    viz._toggle_detailed()
    assert viz._detailed_via_artists                # built...
    assert not any(a.get_visible() for a in viz._detailed_via_artists)  # ...hidden


def test_rerun_rebuilds_detailed_vias(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._toggle_detailed()
    n_before = sum(len(a.get_offsets()) for a in viz._detailed_via_artists)

    viz._redraw_nuts_tracks((viz._nuts_result, viz._detailed_result))
    n_after = sum(len(a.get_offsets()) for a in viz._detailed_via_artists)
    assert n_after == n_before >= 1                 # rebuilt, not duplicated
    assert all(a.get_visible() for a in viz._detailed_via_artists)

    viz._toggle_vias_conns()                        # gate still works post-rerun
    assert not any(a.get_visible() for a in viz._detailed_via_artists)


def _axes_aspect(ax):
    x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
    return (x1 - x0) / (y1 - y0)


def _box_aspect(ax):
    pos = ax.get_position(original=True)
    fw, fh = ax.figure.get_size_inches()
    return (fw * pos.width) / (fh * pos.height)


def test_home_view_tracks_window_resize(monkeypatch):
    """The home view must stay maximal (data aspect == on-screen axes-box aspect)
    as the window reaches its final size — the macOS symptom was a one-shot
    first-draw refit that fired before the async maximize, so the view only
    became maximal after a manual 'h'.  A resize_event now re-applies the fill."""
    from matplotlib.backend_bases import ResizeEvent
    viz = _build_viz("dnuts1.buda", monkeypatch)

    def resize(w, h):
        viz.fig.set_size_inches(w, h)
        viz.fig.canvas.callbacks.process(
            "resize_event", ResizeEvent("resize_event", viz.fig.canvas))

    # A window that maximizes AFTER show() to a wide frame: the fit must follow.
    resize(24, 9)
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6, \
        "home view did not refit to the maximized window (still non-maximal)"
    # A later resize keeps it maximal too.
    resize(12, 18)
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6


def test_home_fit_tracking_does_not_clobber_a_pan(monkeypatch):
    """Once the user pans away from home, a resize must not yank the view back."""
    from matplotlib.backend_bases import ResizeEvent
    viz = _build_viz("dnuts1.buda", monkeypatch)
    x0, x1 = viz.ax.get_xlim(); dx = (x1 - x0) * 0.3
    viz.ax.set_xlim(x0 + dx, x1 + dx)
    panned = viz.ax.get_xlim()
    viz.fig.set_size_inches(10, 10)
    viz.fig.canvas.callbacks.process(
        "resize_event", ResizeEvent("resize_event", viz.fig.canvas))
    assert abs(viz.ax.get_xlim()[0] - panned[0]) < 1e-6, \
        "resize clobbered a user pan (home-fit tracking not guarded)"


def test_home_fit_tracking_resumes_after_pressing_home(monkeypatch):
    """After pan → (resize ignored) → 'h', the view is home again, so a later
    resize must resume keeping it maximal.  Regression for the guard comparing
    against the stale pre-pan home tuple (Codex #147): _zoom_home must refresh
    _home_xlim/_home_ylim."""
    from matplotlib.backend_bases import ResizeEvent
    viz = _build_viz("dnuts1.buda", monkeypatch)

    def resize(w, h):
        viz.fig.set_size_inches(w, h)
        viz.fig.canvas.callbacks.process(
            "resize_event", ResizeEvent("resize_event", viz.fig.canvas))

    # Pan away, resize while off-home (correctly ignored), then press Home.
    x0, x1 = viz.ax.get_xlim(); dx = (x1 - x0) * 0.3
    viz.ax.set_xlim(x0 + dx, x1 + dx)
    resize(9, 16)
    viz._zoom_home()                      # 'h' — recompute maximal fill for 9x16
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6

    # A subsequent resize must still be tracked (was the bug: treated as a pan).
    resize(22, 8)
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6, \
        "resize after 'h' was not tracked (stale home tuple)"
