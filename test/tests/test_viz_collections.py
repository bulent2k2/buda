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


def test_collect_candidate_bundles_keeps_distinct_hier_instances():
    """Per-instance hier bundles that share a cell template (same cell_context/
    reason) but route independently must each appear in the explorer list — not
    be collapsed to one representative — so selecting bundle N opens bundle N and
    the count matches the main viz. Exact-id duplicates are still removed."""
    from types import SimpleNamespace as NS
    from buda_viz import collect_candidate_bundles

    def mk(bid, cell, reason, ncand=2):
        return NS(input=NS(candidates=list(range(ncand)),
                           original_bundle=NS(id=bid, cell_context=cell,
                                              reason=reason)))

    bundles = [
        mk(167, "dogleg2", "r"), mk(168, "dogleg2", "r"),   # 4 instances of one
        mk(169, "dogleg2", "r"), mk(170, "dogleg2", "r"),   # cell template
        mk(5, "", ""), mk(5, "", ""),                       # exact-id dup of 5
        mk(9, "", "", ncand=0),                             # no candidates → skip
    ]
    wrappers, cell_seen = collect_candidate_bundles(bundles)
    ids = [w.input.original_bundle.id for w in wrappers]
    # All 4 instances kept (id-dup + no-cand gone), and sorted by id so the
    # explorer's "bundle i/N" index matches the id-sorted main viz panel.
    assert ids == [5, 167, 168, 169, 170], ids
    assert ids == sorted(ids)
    assert cell_seen[("dogleg2", "r")][1] == 4    # annotation still counts instances


def test_topo_explorer_carries_over_layer_visibility(monkeypatch):
    """A layer toggled off in the main viz is hidden in the topology explorer
    too: the explorer holds a live reference to the main viz's _layer_visible
    map, and toggling a layer refreshes the open explorer."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bid = next(iter(viz._bundle_artists))
    viz._highlighted = bid
    viz._open_topo_explorer()
    exp = viz._topo_explorer
    assert exp is not None and exp._layer_visible is viz._layer_visible

    # Count segment lines AND the layer-colored helper artifacts — slide-span
    # bands (patches) and busterm markers (scatter collections + labels) — so
    # this also guards that _draw_slide_spans / _draw_busterm_markers honour
    # the hidden-layer set, not just the main segment loop.
    def _artist_count():
        return (len(exp.ax.get_lines()) + len(exp.ax.patches)
                + len(exp.ax.texts) + len(exp.ax.collections))

    n_all = _artist_count()
    for lid in viz._layer_visible:          # hide every layer
        viz._layer_visible[lid] = False
    viz._refresh_topo_explorer()
    n_off = _artist_count()
    assert n_off < n_all, (n_all, n_off)   # the bundle's segments AND their
    #                                        slide spans / busterm markers gone
    assert exp._hidden_seg, "every segment should be marked hidden"


def test_topo_explorer_v_raises_main_window(monkeypatch):
    """In the explorer, 'v' mirrors cmd/ctrl-1 (raise the main viz window); the
    main window's 'v' opens/raises the explorer, so 'v' cycles between them."""
    import buda_viz
    from types import SimpleNamespace
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._highlighted = next(iter(viz._bundle_artists))
    viz._refresh_highlight()
    viz._open_topo_explorer()
    exp = viz._topo_explorer
    assert exp is not None and exp._main_fig is viz.fig

    raised = []
    import viz_window
    monkeypatch.setattr(viz_window, "raise_window", lambda f: raised.append(f))
    for key in ("v", "cmd+1", "ctrl+1"):
        raised.clear()
        exp._on_key(SimpleNamespace(key=key, xdata=None, ydata=None))
        assert raised and raised[-1] is viz.fig, key


def test_explorer_v_syncs_selection_to_main(monkeypatch):
    """Paging bundles with [ / ] in the explorer and cycling back with 'v'
    adopts the explorer's bundle as the main-view selection, so the two
    windows stay in sync in both directions (main 'v' already jumps the
    explorer to the main selection)."""
    import buda_viz
    from types import SimpleNamespace
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz._set_highlight(next(iter(viz._bundle_artists)))
    viz._open_topo_explorer()
    exp = viz._topo_explorer
    import viz_window
    monkeypatch.setattr(viz_window, "raise_window", lambda f: None)

    exp._step_bundle(+1)                       # the '[' / ']' page
    target = exp.wrappers[exp.bidx].input.original_bundle.id
    assert target != viz._highlighted
    exp._on_key(SimpleNamespace(key="v", xdata=None, ydata=None))
    assert viz._highlighted == target

    # Cycling back on the SAME bundle must not toggle the selection off
    # (_set_highlight toggles on same-id — the hook guards against that).
    exp._on_key(SimpleNamespace(key="v", xdata=None, ydata=None))
    assert viz._highlighted == target


def test_open_bundles_rank_first(monkeypatch):
    """Bundles with DNUTS-dropped (open) bits are ordered at the top of the
    bundle panel — most dropped first — so the user investigates every open
    from the top instead of hunting through the list."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bids = sorted(viz._bundle_artists.keys())
    assert viz._bid_list == bids               # clean design: plain id order

    worst, worse = bids[-1], bids[-2]
    monkeypatch.setattr(viz, "_bundle_unplaced",
                        lambda: {worst: 7, worse: 3})
    viz._sort_bid_list()
    assert viz._bid_list[:2] == [worst, worse]
    assert viz._bid_list[2:] == [b for b in bids if b not in (worst, worse)]


def test_explorer_steps_bundles_in_panel_order(monkeypatch):
    """[ / ] in the explorer page bundles in the parent BudaViz's live
    bundle-panel order (opens-first), not numeric, so the two windows step
    together. A full ']' cycle visits ids in exactly that order."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bids = sorted(viz._bundle_artists.keys())
    worst, worse = bids[-1], bids[-2]                 # force a non-numeric order
    monkeypatch.setattr(viz, "_bundle_unplaced", lambda: {worst: 7, worse: 3})
    viz._sort_bid_list()
    assert viz._bid_list[:2] == [worst, worse]

    viz._set_highlight(bids[0])
    viz._open_topo_explorer()
    exp = viz._topo_explorer

    idx_by_id = {w.input.original_bundle.id: i for i, w in enumerate(exp.wrappers)}
    assert exp._bundle_step_order() == [idx_by_id[b] for b in viz._bid_list]

    order_ids = list(viz._bid_list)
    seen = []
    for _ in range(len(exp.wrappers)):
        seen.append(exp.wrappers[exp.bidx].input.original_bundle.id)
        exp._step_bundle(+1)
    start = order_ids.index(seen[0])                  # rotation of the panel order
    assert seen == [order_ids[(start + k) % len(order_ids)]
                    for k in range(len(order_ids))]

    # The order tracks the panel LIVE: re-sort clears the opens -> numeric again.
    monkeypatch.setattr(viz, "_bundle_unplaced", lambda: {})
    viz._sort_bid_list()
    assert exp._bundle_step_order() == [idx_by_id[b] for b in sorted(idx_by_id)]


def test_explorer_orphan_steps_numeric(monkeypatch):
    """An explorer with no parent (bundle_order_fn=None) pages in numeric/id
    order — wrappers are id-sorted, so the step order is 0,1,2,…"""
    import buda_viz
    from buda_viz import collect_candidate_bundles
    viz = _build_viz("dnuts1.buda", monkeypatch)
    wrappers, _ = collect_candidate_bundles(viz.bundles)
    exp = buda_viz.TopologyExplorer(viz.fp, wrappers, layer_stack=viz.layer_stack)
    try:
        assert exp._bundle_order_fn is None
        assert exp._bundle_step_order() == list(range(len(wrappers)))
    finally:
        plt.close(exp.fig)


def test_scroll_buttons_page_by_visible_minus_one(monkeypatch):
    """The ▲/▼ scroll buttons page by (visible rows − 1), sized to the CURRENT
    dynamic panel height — not a fixed step — so a tall list (Overlap panel
    folded away when there are no overlaps) pages a full screen at a time."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    monkeypatch.setattr(viz, "_redraw_bundle_list", lambda: None)   # arith only
    viz._bid_list = list(range(100))

    monkeypatch.setattr(viz, "_bundle_list_n_visible", lambda: 10)
    viz._bundle_scroll = 0
    viz._scroll_bundles_page(+1); assert viz._bundle_scroll == 9    # 10 - 1
    viz._scroll_bundles_page(+1); assert viz._bundle_scroll == 18
    viz._scroll_bundles_page(-1); assert viz._bundle_scroll == 9

    # Taller panel (no Overlap panel) -> a click pages further, sized live.
    monkeypatch.setattr(viz, "_bundle_list_n_visible", lambda: 30)
    viz._bundle_scroll = 0
    viz._scroll_bundles_page(+1); assert viz._bundle_scroll == 29   # 30 - 1


def test_expected_bit_wires_taper_aware(monkeypatch):
    """#268 taper: a fan-in segment with a non-empty Topology::seg_bits entry
    emits NetSegments only for its member bits.  The opens-first ranking and
    the [n/N] badges must expect exactly that (like check_dnuts after #273) —
    the naive nets × segments would report phantom opens on clean CONVERGENT
    bundles and rank HEALTHY bundles at the top of the panel."""
    from types import SimpleNamespace
    viz = _build_viz("dnuts1.buda", monkeypatch)

    # Real (untapered) wrapper: expectation is the plain nets × segments.
    w = next(w for w in viz.bundles if w.input.candidates)
    topo  = w.input.candidates[w.plan.selected_topology_index]
    nbits = len(w.input.original_bundle.get_net_names())
    assert not topo.seg_bits                      # dnuts1 has no fan-in taper
    assert viz._expected_bit_wires(w) == nbits * len(topo.segments)
    assert not viz._bundle_unplaced()             # fully placed → no opens

    # Tapered fan-in shape: segment 0 carries 3 member bits, the rest full.
    def fake(seg_bits, nsegs=4, nbits=8, sel=0):
        return SimpleNamespace(
            plan=SimpleNamespace(selected_topology_index=sel),
            input=SimpleNamespace(
                candidates=[SimpleNamespace(seg_bits=seg_bits,
                                            segments=[None] * nsegs)],
                original_bundle=SimpleNamespace(
                    get_net_names=lambda: ["n"] * nbits, id=999)))
    assert viz._expected_bit_wires(fake({0: [0, 1, 2]})) == 3 + 8 * 3
    # An EMPTY seg_bits entry means untapered (seg_bit_count's rule).
    assert viz._expected_bit_wires(fake({0: []})) == 8 * 4
    assert viz._expected_bit_wires(fake({}, sel=-1)) is None   # no topo


def test_overlap_panel_space_folds_into_bundle_list(monkeypatch):
    """With no overlaps the Overlap list and its scroll arrows are hidden and
    the bundle list absorbs their vertical space (more visible rows); the
    split is restored when overlaps (re)appear, e.g. after a re-route."""
    from types import SimpleNamespace
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert not viz._overlap_entries            # dnuts1 routes clean
    assert viz._ov_layout_mode == 'no_ov'
    assert not viz._ax_overlaps.get_visible()
    assert not viz._ax_oscroll_up.get_visible()
    tall_h = viz._ax_bundles.get_position().height
    split_h = viz._right_rects['with_ov']['bundle_list'][3]
    assert tall_h > split_h + 1e-6
    rows_tall = viz._bundle_list_n_visible()

    # Overlaps appear (as after a re-route) → split restored.
    viz._overlap_entries = [SimpleNamespace(layer=5, bid_a=1, bid_b=2)]
    assert viz._apply_overlap_layout() is True
    assert viz._ov_layout_mode == 'with_ov'
    assert viz._ax_overlaps.get_visible()
    pos = viz._ax_bundles.get_position()
    assert abs(pos.height - split_h) < 1e-9
    assert viz._bundle_list_n_visible() <= rows_tall

    # Gone again → folded again, byte-identical geometry.
    viz._overlap_entries = []
    assert viz._apply_overlap_layout() is True
    assert not viz._ax_overlaps.get_visible()
    assert abs(viz._ax_bundles.get_position().height - tall_h) < 1e-9


def test_recompute_home_bbox_tracks_current_artists(monkeypatch):
    """After a re-run pins a different-extent topology, `h` fits the CURRENT
    design (blocks + live route) — including SHRINKING back when re-routing from
    a large extent to a smaller one. It is computed from the live registered
    artists, NOT autoscale_view(): Matplotlib's dataLim never shrinks when
    artists are removed, so autoscaling would keep the stale union (Codex #242).
    Cache-only: the camera is not moved. _redraw_nuts_tracks calls it per re-route."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    ax = viz.ax
    viz._recompute_home_bbox()
    base = viz._home_data_bbox
    view = (ax.get_xlim(), ax.get_ylim())

    # A route segment reaching far beyond the design, registered like a real one.
    bid = next(iter(viz._bundle_artists))
    ln, = ax.plot([1e4, 1.1e4], [1.2e4, 1.2e4])
    viz._register(bid, ln, alpha=1.0)
    viz._recompute_home_bbox()
    assert viz._home_data_bbox[1] >= 1e4 and viz._home_data_bbox[3] >= 1.2e4  # grew

    # Re-routing back to the smaller design must SHRINK the home again — the bug
    # Codex flagged: autoscale_view() would keep the stale (larger) union.
    ln.remove()
    viz._bundle_artists[bid] = [e for e in viz._bundle_artists[bid]
                                if e['artist'] is not ln]
    viz._recompute_home_bbox()
    assert viz._home_data_bbox == base, (viz._home_data_bbox, base)   # shrank back
    assert ax.get_xlim() == view[0] and ax.get_ylim() == view[1]      # camera untouched


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


def _build_viz_from_text(monkeypatch, tmp_path, text, name="t.buda"):
    """Build a BudaVisualizer from an inline .buda script (for panel-button
    tests that need a design with/without keepouts, a routing grid, etc.)."""
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
    flow = tmp_path / name
    flow.write_text(text)
    buda_cli.BudaSession().do_command(f"source {flow}")
    assert "viz" in captured, "visualize did not build a BudaVisualizer"
    return captured["viz"]


def test_heatmap_off_by_default():
    from ui_state import ViewState
    assert ViewState().heatmap is False


_P705_FLOW = """
def_layer 4 M4 H TOP 50
def_layer 5 M5 V TOP 50
def_track_pattern 4 0 SIGNAL 1 1 SIGNAL 1 1 SIGNAL 1 1
def_track_pattern 5 0 SIGNAL 1 1 SIGNAL 1 1 SIGNAL 1 1
add_block A 0 0 100 100
add_block B 300 0 400 100
add_block C 0 300 100 400
add_bus d[4] A.tx B.rx
add_bus e[4] C.tx B.rx
run_bundler strict
generate_topologies
run_planner 5
run_nuts
visualize
"""


def test_heatmap_refreshes_on_rerun(monkeypatch, tmp_path):
    """An in-GUI re-run rebuilds the congestion heatmap from the RE-PLANNED
    cut/band state instead of leaving the stale original overlay on screen
    (audit P7-05).  With the provider stubbed to report no congested cells,
    the re-run must CLEAR the stale artists — pre-fix _redraw_nuts_tracks
    never touched them."""
    viz = _build_viz_from_text(monkeypatch, tmp_path, _P705_FLOW)
    # The heatmap is wired to a live cuts provider and drawn at open.
    assert viz._cuts_provider is not None
    assert len(viz._heatmap_artists) >= 1

    calls = {"n": 0}

    def _stub():
        calls["n"] += 1
        return None                # a re-plan that left no congested cells

    viz._cuts_provider = _stub
    result = viz._rerun_fn()       # real re-run → fresh NUTSResult
    viz._redraw_nuts_tracks(result)

    assert calls["n"] >= 1, "the re-run did not consult the cuts provider"
    assert viz._heatmap_artists == [], "stale heatmap survived the re-run"


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


def test_keepouts_tracks_buttons_always_visible_but_dimmed(monkeypatch):
    """Keepouts (when the design has none) and Tracks (until Detailed is on)
    stay visible but dimmed/inactive, and clicking a dimmed button is a no-op.
    Both activate when their condition is met."""
    viz = _build_viz("dnuts1.buda", monkeypatch)   # no keepouts, Detailed off

    # Both buttons are shown but dimmed (inactive).
    assert viz._btn_keepouts.ax.get_visible() and viz._btn_keepouts._buda_enabled is False
    assert viz._btn_tracks.ax.get_visible() and viz._btn_tracks._buda_enabled is False

    # Clicking a dimmed button does nothing.
    kbefore = viz.ui_state.keepouts
    viz._toggle_keepouts()
    assert viz.ui_state.keepouts == kbefore, "dimmed Keepouts must be a no-op"
    tbefore = viz.ui_state.tracks
    viz._toggle_tracks()
    assert viz.ui_state.tracks == tbefore, "dimmed Tracks must be a no-op"

    # Turning Detailed on activates Tracks (dnuts1 has track patterns → rails).
    viz._toggle_detailed()
    assert viz._btn_tracks.ax.get_visible() and viz._btn_tracks._buda_enabled is True


def test_keepouts_button_active_when_design_has_keepouts(monkeypatch, tmp_path):
    """A design with a floorplan keepout zone shows the Keepouts button active."""
    viz = _build_viz_from_text(
        monkeypatch, tmp_path,
        "def_layer 4 M4 H TOP 0.0\n"
        "def_layer 5 M5 V TOP 0.0\n"
        "add_block a 0 0 100 100\n"
        "add_block b 400 0 500 100\n"
        "add_keepout 150 0 250 100 M4\n"
        "add_bus x[4] a.tx b.rx\n"
        "run_bundler\ngenerate_topologies\nrun_planner\nrun_nuts\nvisualize\n")
    assert viz.fp.get_keepout_zones(), "precondition: design has a keepout"
    assert viz._btn_keepouts.ax.get_visible()
    assert viz._btn_keepouts._buda_enabled is True


def test_preroutes_button_dimmed_without_routing_grid(monkeypatch, tmp_path):
    """Preroutes stays visible but dimmed (and click-inert) when the design has
    no routing grid (no def_track_pattern)."""
    viz = _build_viz_from_text(
        monkeypatch, tmp_path,
        "def_layer 4 M4 H TOP 0.0\n"
        "def_layer 5 M5 V TOP 0.0\n"
        "add_block a 0 0 100 100\n"
        "add_block b 400 0 500 100\n"
        "add_bus x[4] a.tx b.rx\n"
        "run_bundler\ngenerate_topologies\nrun_planner\nrun_nuts\nvisualize\n")
    assert not viz._has_preroute_data
    assert viz._btn_preroutes.ax.get_visible() and viz._btn_preroutes._buda_enabled is False
    before = viz.ui_state.preroutes_mode
    viz._cycle_preroutes()
    assert viz.ui_state.preroutes_mode == before, "dimmed Preroutes must be a no-op"


def test_preroutes_button_active_with_routing_grid(monkeypatch):
    """dnuts1 defines track patterns → the Preroutes button is active."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._has_preroute_data
    assert viz._btn_preroutes.ax.get_visible() and viz._btn_preroutes._buda_enabled is True


def test_s_key_toggles_solo(monkeypatch):
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)
    before = viz.ui_state.solo
    viz._on_key(types.SimpleNamespace(key="s"))
    assert viz.ui_state.solo != before, "'s' should toggle Solo mode"
    viz._on_key(types.SimpleNamespace(key="s"))
    assert viz.ui_state.solo == before


def test_design_stats_header_counts(monkeypatch):
    """The bundle-panel header reports design-wide bundles/buses/nets. dnuts1 has
    5 add_bus lines (n11[32] n12[32] bv1[16] bv2[16] r12[32]) → 5 buses / 128
    nets, bundled into 5 bundles."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    n_bundles, n_buses, n_nets = viz._design_counts()
    assert n_bundles == len(viz.bundles)
    assert n_buses == 5, f"expected 5 buses, got {n_buses}"
    assert n_nets == 128, f"expected 128 nets, got {n_nets}"
    # nets == sum of each bundle's net names (every wire counted once).
    assert n_nets == sum(len(w.input.original_bundle.get_net_names())
                         for w in viz.bundles)
    # The always-on header is one compact 'buses · nets' line; the bundle
    # count lives on the All Bundles toggle label instead.
    assert viz._ax_design_stats is not None
    texts = [t.get_text() for t in viz._ax_design_stats.texts]
    assert texts == [f"{n_buses} buses · {n_nets} nets"], texts
    assert f"{n_bundles} Bundles" in viz._btn_all_bundles.label.get_text(), \
        viz._btn_all_bundles.label.get_text()


def test_layer_stats_header_counts(monkeypatch):
    """The layer-panel header reports the design-wide NUTS segment/bit totals —
    the parallel of the bundle panel's 'buses · nets' line — and those totals
    equal the sum of the per-layer breakdown the rows show."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._nuts_result is not None          # dnuts1 runs NUTS

    nseg, nbits = viz._nuts_seg_counts()
    placed = [ts for ts in viz._nuts_result.segments if ts.placed]
    assert nseg == len(placed), f"expected {len(placed)} placed segs, got {nseg}"
    assert nbits == sum(viz._bundle_bits(ts.bundle_id) for ts in placed)

    # Totals must equal the per-layer breakdown drawn in the layer rows.
    per_layer_segs = {}
    per_layer_bits = {}
    for ts in placed:
        per_layer_segs[ts.layer] = per_layer_segs.get(ts.layer, 0) + 1
        per_layer_bits[ts.layer] = (per_layer_bits.get(ts.layer, 0)
                                    + viz._bundle_bits(ts.bundle_id))
    assert nseg == sum(per_layer_segs.values())
    assert nbits == sum(per_layer_bits.values())

    # The always-on header above the Layer panel shows the compact total line.
    assert viz._ax_layer_stats is not None
    texts = [t.get_text() for t in viz._ax_layer_stats.texts]
    assert texts == [f"{nseg} segments · {nbits} wires"], texts


def test_nuts_linewidth_tracks_zoom(monkeypatch):
    """Abstract NUTS segment lines are zoom-true: the drawn point-width follows
    the segment's PHYSICAL width at the current zoom (capped at the static
    viz_lw, floored for visibility).  Regression for the home-view artifact
    where a fixed ~14pt line, centered on a track hugging a block face,
    rendered several times wider than the physical band and appeared to
    straddle its busterm when zoomed out."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    entries = [e for es in viz._bundle_artists.values() for e in es
               if e.get('phys_w')]
    assert entries, "no physical-width NUTS lines registered"
    m_entries = [e for es in viz._bundle_artists.values() for e in es
                 if e.get('marker_phys')]
    assert m_entries, "no physical-size via/conn markers registered"

    def scales():
        o = viz.ax.transData.transform((0.0, 0.0))
        px = abs(viz.ax.transData.transform((1.0, 0.0))[0] - o[0])
        py = abs(viz.ax.transData.transform((0.0, 1.0))[1] - o[1])
        return px * 72.0 / viz.fig.dpi, py * 72.0 / viz.fig.dpi

    def pts_per_unit(e):
        px, py = scales()
        return py if e['horiz'] else px

    def expected(e):
        return max(viz._LW_MIN_PTS, min(e['lw_cap'], e['phys_w'] * pts_per_unit(e)))

    def expected_ms(e):
        return max(e['ms_floor'], min(e['ms_cap'], e['marker_phys'] * min(*scales())))

    def check_all():
        for e in entries:
            lw = e['artist'].get_linewidth()
            assert abs(lw - expected(e)) < 0.2, \
                (lw, expected(e), e['phys_w'], e['horiz'])
            assert abs(lw - e['lw']) < 1e-9   # registry mirrors the artist
            # Never wider than the physical footprint unless at the floor.
            lw_units = lw / pts_per_unit(e)
            assert (lw_units <= e['phys_w'] * 1.01
                    or lw <= viz._LW_MIN_PTS + 1e-6), (lw_units, e['phys_w'])
        # Via/conn markers track the connected segments' widths the same way.
        for e in m_entries:
            ms = e['artist'].get_markersize()
            assert abs(ms - expected_ms(e)) < 0.2, \
                (ms, expected_ms(e), e['marker_phys'])
            assert abs(ms - e['ms']) < 1e-9

    viz.fig.canvas.draw()          # settle the initial sync
    check_all()

    # Zoom OUT 10× (the home-view regime): widths must re-fit immediately —
    # the xlim/ylim callbacks fire synchronously on set_xlim/set_ylim.
    x0, x1 = viz.ax.get_xlim(); y0, y1 = viz.ax.get_ylim()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sx, sy = (x1 - x0) * 5, (y1 - y0) * 5
    viz.ax.set_xlim(cx - sx, cx + sx); viz.ax.set_ylim(cy - sy, cy + sy)
    check_all()

    # Zoom IN 100× from there: widths cap at the static viz_lw.
    viz.ax.set_xlim(cx - sx / 100, cx + sx / 100)
    viz.ax.set_ylim(cy - sy / 100, cy + sy / 100)
    check_all()
    assert any(abs(e['artist'].get_linewidth() - e['lw_cap']) < 1e-6
               for e in entries), "expected at least one line at the static cap"


def test_bundle_rows_drop_bits_suffix_when_detailed(monkeypatch):
    """In detailed mode each row shows a right-aligned [unplaced/total] column.
    The redundant '[bits]' suffix is dropped from the row label so it can't
    crowd/overlap that column, and the full bus name still shows."""
    import re as _re
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._detailed_result is not None      # dnuts1 runs detailed NUTS
    viz._redraw_bundle_list()

    texts  = [t.get_text() for t in viz._ax_bundles.texts]
    labels = [t for t in texts if t.startswith(('☑', '☐'))]
    stats  = [t for t in texts if _re.fullmatch(r'\[\d+/\d+\]', t)]
    assert labels and stats, (labels, stats)

    # No row label keeps a trailing "[bits]" suffix in detailed mode...
    for lbl in labels:
        assert not _re.search(r'\[\d+\]$', lbl), f"label kept a [bits] suffix: {lbl!r}"
    # ...and the bus name is not over-truncated (dnuts1 names are short).
    assert any('n11_0' in lbl for lbl in labels), labels


def test_selected_title_shows_segment_count_no_solo_hint(monkeypatch):
    """Cycling bundles with `n` shows the selected topology's segment count in
    the title (to gauge complexity), and no '[Solo ON]' hint even in Solo mode."""
    import re as _re
    import types
    viz = _build_viz("dnuts1.buda", monkeypatch)

    viz._on_key(types.SimpleNamespace(key="n"))     # step to a bundle
    bid = viz._highlighted
    assert bid is not None
    title = viz.ax.get_title()

    # Segment count is present and matches the selected topology's segment count.
    wrapper = next(w for w in viz.bundles
                   if w.input.original_bundle.id == bid)
    nsegs = len(wrapper.input.candidates[
        wrapper.plan.selected_topology_index].segments)
    m = _re.search(r"(\d+)\s+segs", title)
    assert m, f"title should report a segment count: {title!r}"
    assert int(m.group(1)) == nsegs, \
        f"title segs {m.group(1)} != selected topology segs {nsegs}: {title!r}"
    assert "Solo" not in title, f"'[Solo ON]' hint should be gone: {title!r}"

    # Turning Solo ON must not reintroduce the hint.
    viz._on_key(types.SimpleNamespace(key="s"))     # solo on
    viz._on_key(types.SimpleNamespace(key="n"))     # re-render title
    assert "Solo" not in viz.ax.get_title(), \
        f"Solo mode must not add a title hint: {viz.ax.get_title()!r}"


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


def test_abstract_vias_hidden_in_detailed_mode(monkeypatch):
    # The abstract (bus-level) via/conn markers belong to the NUTS view; the
    # per-bit vias replace them in Detailed mode. A stale fig_redraw while
    # detailed was on used to re-reveal them (gated on vias_conns alone),
    # leaving abstract markers on top of the detailed view.
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._vias_conns_artists                     # this design has some
    assert viz.ui_state.vias_conns is True
    n = lambda: sum(1 for a in viz._vias_conns_artists if a.get_visible())

    assert n() > 0                                     # visible in abstract mode
    viz._toggle_detailed()                             # enter detailed
    assert n() == 0, "abstract vias must hide in detailed mode"

    # Any ui_state change (layer/blocks/highlight/…) fires fig_redraw — it must
    # not re-reveal the abstract markers while detailed is on.
    viz.ui_state.notify()
    assert n() == 0, "fig_redraw re-revealed abstract vias in detailed mode"

    # A full leave/re-enter cycle (each step firing a redraw) stays clean — one
    # round-trip proves the toggle path re-gates correctly; more just multiplies
    # the (expensive) viz redraws without adding coverage.
    viz._toggle_detailed(); viz.ui_state.notify()      # leave
    viz._toggle_detailed(); viz.ui_state.notify()      # re-enter
    assert n() == 0

    viz._toggle_detailed()                             # back to abstract
    assert n() > 0, "abstract vias must return when leaving detailed"


def test_terminals_hidden_in_detailed_mode(monkeypatch):
    # Terminal (busterm) markers belong to the abstract view and hide in
    # Detailed mode. A fig_redraw while detailed was on (e.g. from a Solo
    # toggle) used to re-reveal them, gated on the Terminals toggle alone.
    viz = _build_viz("dnuts1.buda", monkeypatch)
    assert viz._busterm_artists                        # this design has some
    assert viz.ui_state.busterms is True
    n = lambda: sum(1 for a in viz._busterm_artists if a.get_visible())

    assert n() > 0                                     # visible in abstract mode
    viz._highlighted = next(iter(viz._bundle_artists)) # select a bundle
    viz._refresh_highlight()
    viz._toggle_detailed()                             # enter detailed → hidden
    assert n() == 0, "terminals must hide in detailed mode"

    viz._toggle_solo()                                 # Solo on  → fires fig_redraw
    assert n() == 0, "Solo toggle re-revealed terminals in detailed mode"
    viz._toggle_solo()                                 # Solo off → fires fig_redraw
    assert n() == 0

    # Any other redraw while detailed is on must not reveal them either.
    viz.ui_state.notify()
    assert n() == 0

    viz._toggle_detailed()                             # back to abstract
    assert n() > 0, "terminals must return when leaving detailed"


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


def test_ctrl_z_with_no_selection_keeps_maximal_view(monkeypatch):
    """Ctrl-z ('zoom to bundle') with no bundle selected must reset to the
    MAXIMAL full view (like 'h'), not ax.autoscale() which collapses to a
    non-maximal sliver under set_aspect('equal')."""
    import types
    from matplotlib.backend_bases import ResizeEvent
    viz = _build_viz("dnuts1.buda", monkeypatch)
    viz.fig.set_size_inches(20, 9)
    viz.fig.canvas.callbacks.process(
        "resize_event", ResizeEvent("resize_event", viz.fig.canvas))
    assert viz._highlighted is None, "precondition: no bundle selected"
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6  # maximal now

    viz._on_key(types.SimpleNamespace(key="ctrl+z"))
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6, \
        "Ctrl-z with no selection shrank the view below maximal"

    # And the resize-tracking guard is still in sync (a later resize tracks).
    viz.fig.set_size_inches(11, 17)
    viz.fig.canvas.callbacks.process(
        "resize_event", ResizeEvent("resize_event", viz.fig.canvas))
    assert abs(_axes_aspect(viz.ax) - _box_aspect(viz.ax)) < 1e-6


# ── right-click-drag zoom-to-box (adopted from the Floorplanner) ──────────────

def _fire(fig, ax, name, xd, yd):
    from matplotlib.backend_bases import MouseEvent, MouseButton
    x, y = ax.transData.transform((xd, yd))
    fig.canvas.callbacks.process(
        name, MouseEvent(name, fig.canvas, x, y, button=MouseButton.RIGHT))


def _frac(t, lo, hi):
    return lo + (hi - lo) * t


def test_right_drag_zoom_in_frames_the_box(monkeypatch):
    """Right-drag left→right zooms INTO the drawn box: smaller view area,
    centred on the box, and the box expanded to fill the window (aspect ==
    axes-box aspect).  A live dashed rubber-band shows during the drag and is
    removed on release."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    ax, fig = viz.ax, viz.fig
    fig.canvas.draw()
    xl, yl = ax.get_xlim(), ax.get_ylim()
    a0 = (xl[1] - xl[0]) * (yl[1] - yl[0])
    n_static = len(ax.patches)              # block/keepout patches already there
    bx0, bx1 = _frac(0.35, *xl), _frac(0.65, *xl)
    by0, by1 = _frac(0.35, *yl), _frac(0.65, *yl)

    _fire(fig, ax, "button_press_event", bx0, by0)
    _fire(fig, ax, "motion_notify_event", bx1, by1)
    assert len(ax.patches) == n_static + 1, "rubber-band not shown during drag"
    _fire(fig, ax, "button_release_event", bx1, by1)

    assert len(ax.patches) == n_static, "rubber-band not cleared on release"
    nxl, nyl = ax.get_xlim(), ax.get_ylim()
    assert (nxl[1] - nxl[0]) * (nyl[1] - nyl[0]) < a0, "did not zoom in"
    assert abs((nxl[0] + nxl[1]) / 2 - (bx0 + bx1) / 2) < 1e-6
    assert abs((nyl[0] + nyl[1]) / 2 - (by0 + by1) / 2) < 1e-6
    assert abs(_axes_aspect(ax) - _box_aspect(ax)) < 1e-9, "box did not fill window"


def test_right_drag_zoom_out_expands_view(monkeypatch):
    """Right-drag right→left zooms OUT: the view area grows."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    ax, fig = viz.ax, viz.fig
    fig.canvas.draw()
    xl, yl = ax.get_xlim(), ax.get_ylim()
    a0 = (xl[1] - xl[0]) * (yl[1] - yl[0])
    # RL drag: press at the upper-right of a sub-box, release at the lower-left.
    _fire(fig, ax, "button_press_event", _frac(0.65, *xl), _frac(0.65, *yl))
    _fire(fig, ax, "motion_notify_event", _frac(0.35, *xl), _frac(0.35, *yl))
    _fire(fig, ax, "button_release_event", _frac(0.35, *xl), _frac(0.35, *yl))
    nxl, nyl = ax.get_xlim(), ax.get_ylim()
    assert (nxl[1] - nxl[0]) * (nyl[1] - nyl[0]) > a0, "did not zoom out"


def test_tiny_right_drag_is_ignored(monkeypatch):
    """A barely-moved right-drag (a stray right-click) must not change the view."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    ax, fig = viz.ax, viz.fig
    fig.canvas.draw()
    xl, yl = ax.get_xlim(), ax.get_ylim()
    before = (xl, yl)
    cx, cy = _frac(0.5, *xl), _frac(0.5, *yl)
    _fire(fig, ax, "button_press_event", cx, cy)
    _fire(fig, ax, "motion_notify_event", cx + (xl[1] - xl[0]) * 0.003, cy)
    _fire(fig, ax, "button_release_event", cx + (xl[1] - xl[0]) * 0.003, cy)
    assert (ax.get_xlim(), ax.get_ylim()) == before, "tiny drag changed the view"


def test_right_click_does_not_deselect(monkeypatch):
    """A right-click (zoom-box gesture) must not clear the bundle selection."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    bid = next(iter(viz._bundle_artists), None)
    if bid is None:
        import pytest
        pytest.skip("no registered bundle to select")
    viz._set_highlight(bid)
    viz.fig.canvas.draw()
    xl, yl = viz.ax.get_xlim(), viz.ax.get_ylim()
    _fire(viz.fig, viz.ax, "button_press_event", _frac(0.5, *xl), _frac(0.5, *yl))
    assert viz._highlighted == bid, "right-click deselected the bundle"


def test_box_zoom_yields_to_active_toolbar_mode(monkeypatch):
    """While a matplotlib toolbar tool (pan/zoom) is active, a right-drag must
    NOT start BUDA's box zoom — otherwise it runs on top of the toolbar gesture
    (Codex #159)."""
    viz = _build_viz("dnuts1.buda", monkeypatch)
    ax, fig = viz.ax, viz.fig
    fig.canvas.draw()

    class _FakeToolbar:
        mode = "zoom rect"        # non-empty ⇒ a toolbar tool is active
    monkeypatch.setattr(fig.canvas, "toolbar", _FakeToolbar(), raising=False)

    before = (ax.get_xlim(), ax.get_ylim())
    xl, yl = before
    _fire(fig, ax, "button_press_event", _frac(0.35, *xl), _frac(0.35, *yl))
    _fire(fig, ax, "motion_notify_event", _frac(0.65, *xl), _frac(0.65, *yl))
    _fire(fig, ax, "button_release_event", _frac(0.65, *xl), _frac(0.65, *yl))
    assert (ax.get_xlim(), ax.get_ylim()) == before, \
        "box zoom ran despite an active toolbar mode"
    assert not any(getattr(p, "get_linestyle", lambda: None)() == "--"
                   for p in ax.patches), "rubber-band drawn despite toolbar mode"


def test_ipc_tools_path_fallback_resolves_viz_ipc():
    """Audit P6-01: the sys.path fallback ahead of `from viz_ipc import …`
    computed dirname(buda_viz.py)/../../tools — one level too far up, a
    directory that does not exist — so a viz launched with --ipc outside
    the tools/ dir could never import viz_ipc.  It must point at the
    repo's tools/ (dirname/../tools, as buda_cli.py computes it)."""
    import os

    import buda_viz
    tools = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(buda_viz.__file__)), '..', 'tools'))
    assert os.path.isfile(os.path.join(tools, 'viz_ipc.py'))
    src = open(buda_viz.__file__.replace('.pyc', '.py')).read()
    assert "'..', '..', 'tools'" not in src, \
        "the ../../tools fallback path is back (audit P6-01)"
