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

    viz._toggle_detailed()  # opens the detailed view → triggers the lazy build

    assert viz._detailed_built is True
    assert viz._detailed_bundle_artists, "bit-wire collections should now exist"
    # Bit-wires are LineCollections (one per bundle×layer), not per-bit Line2D.
    for entries in viz._detailed_bundle_artists.values():
        for e in entries:
            assert isinstance(e["artist"], LineCollection)
    # Rail stripes are PatchCollections (one per layer×kind).
    for e in viz._grid_rail_artists:
        assert isinstance(e["artist"], PatchCollection)


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


def test_highlight_survives_collection_refactor(monkeypatch):
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bid = next(iter(viz._bundle_artists))
    viz._set_highlight(bid)            # must not raise on collection artists
    # Selecting a bundle draws white outline lines over its Line2D segments.
    assert viz._highlighted == bid
    assert len(viz._highlight_overlays) > 0
    viz._set_highlight(None)
    assert viz._highlighted is None
