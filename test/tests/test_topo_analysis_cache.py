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

"""Phase B — the content-fingerprint analysis cache
(docs/internal/topo_conn_unification.md §5).

ConnTopology::build is now a wrapper over analyze(), which caches the six-pass
result on the Topology and validates by CONTENT: a fingerprint over every
Topology field the passes read, plus the Floorplan's (uid, rev).  The design
promise is that a stale cache is *structurally impossible* — no mutator, in
C++ or Python, needs to know the cache exists — so these tests attack it
through every mutation vector:

  * repeat build            -> hit, and byte-identical fields (hit == recompute)
  * segments edited         -> recompute
  * connected_block_names edited -> recompute (the field a rev-discipline
    design would have missed — it is assigned AFTER filter_pinched already
    built the analysis in generate_2pin, and was the subject of the PR #196
    review fix)
  * same-content copy       -> hit (fingerprint equality, not object identity)
  * floorplan mutated       -> recompute (fp rev)
  * different floorplan     -> recompute (fp uid), and no cross-poisoning when
    alternating floorplans for one topology
"""
import buda


def _fp():
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    return fp


def _cands(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    return g.generate_candidates("A", ["B"])


def _counters():
    return buda.analysis_cache_counters()


def _snap(ct):
    return [(cs.horiz, cs.layer_id, cs.along_lo, cs.along_hi, cs.perp_pos,
             cs.perp_lo, cs.perp_hi, cs.net_pull,
             cs.along_flex_lo, cs.along_flex_hi,
             cs.along_cover_lo, cs.along_cover_hi, cs.along_pull,
             [(c.kind, c.block_name, c.face_coord, c.seg_idx, c.at_pos,
               c.is_endpoint) for c in cs.conns])
            for cs in ct.segs()]


def test_repeat_build_hits_and_equals_recompute():
    fp = _fp()
    t = _cands(fp)[0]
    ct1 = buda.ConnTopology()
    ct1.build(t, fp)
    first = _snap(ct1)
    buda.analysis_cache_reset_counters()
    ct2 = buda.ConnTopology()
    ct2.build(t, fp)
    computes, hits = _counters()
    assert (computes, hits) == (0, 1), \
        f"second build of an unchanged topology must be a pure hit, got {computes=} {hits=}"
    assert _snap(ct2) == first, "cache hit returned different analysis than recompute"


def test_segment_mutation_invalidates():
    fp = _fp()
    t = _cands(fp)[0]
    buda.ConnTopology().build(t, fp)
    segs = t.segments
    segs[0].start = buda.Point(segs[0].start.x + 1, segs[0].start.y)
    t.segments = segs                     # whole-vector assign (pybind copies)
    buda.analysis_cache_reset_counters()
    buda.ConnTopology().build(t, fp)
    computes, hits = _counters()
    assert computes == 1 and hits == 0, \
        f"edited geometry must recompute, got {computes=} {hits=}"


def test_connected_block_names_mutation_invalidates():
    """The Codex-review case: this field feeds tighten_passthrough and is
    assigned after analyses may already exist; content validation must catch
    a change no setter-bump protocol was told about."""
    fp = _fp()
    t = _cands(fp)[0]
    buda.ConnTopology().build(t, fp)
    t.connected_block_names = list(t.connected_block_names) + ["B"] \
        if "B" not in t.connected_block_names else ["A"]
    buda.analysis_cache_reset_counters()
    buda.ConnTopology().build(t, fp)
    computes, hits = _counters()
    assert computes == 1 and hits == 0, \
        f"connected_block_names change must recompute, got {computes=} {hits=}"


def test_same_content_copy_hits():
    """Validation is by content, not object identity: an offset-0 deep copy
    fingerprints identically and its copied cache pointer serves the hit."""
    fp = _fp()
    t = _cands(fp)[0]
    buda.ConnTopology().build(t, fp)
    t2 = buda.offset_topology(t, 0, 0)
    buda.analysis_cache_reset_counters()
    buda.ConnTopology().build(t2, fp)
    computes, hits = _counters()
    assert hits == 1 and computes == 0, \
        f"identical-content copy should hit, got {computes=} {hits=}"


def test_floorplan_mutation_invalidates():
    fp = _fp()
    t = _cands(fp)[0]
    buda.ConnTopology().build(t, fp)
    fp.add_block("C", 600, 600, 700, 700)     # bumps fp rev
    buda.analysis_cache_reset_counters()
    buda.ConnTopology().build(t, fp)
    computes, hits = _counters()
    assert computes == 1 and hits == 0, \
        f"floorplan mutation must recompute, got {computes=} {hits=}"


def test_different_floorplan_invalidates_and_analysis_differs():
    """Same topology analyzed against another floorplan (the hier
    check_connectivity pattern) must not serve the first floorplan's slides."""
    fp = _fp()
    t = _cands(fp)[0]
    ct1 = buda.ConnTopology()
    ct1.build(t, fp)
    s1 = _snap(ct1)

    fp2 = buda.Floorplan()                    # B's face extent shifted up
    fp2.add_block("A", 0, 0, 100, 100)
    fp2.add_block("B", 300, 40, 400, 200)
    buda.analysis_cache_reset_counters()
    ct2 = buda.ConnTopology()
    ct2.build(t, fp2)
    computes, hits = _counters()
    assert computes == 1 and hits == 0, \
        f"different floorplan must recompute, got {computes=} {hits=}"
    assert _snap(ct2) != s1, "analysis against a different floorplan cannot be identical here"

    # ...and alternating back must not serve fp2's analysis for fp (the
    # single-slot cache recomputes rather than cross-poisoning).
    ct3 = buda.ConnTopology()
    ct3.build(t, fp)
    assert _snap(ct3) == s1


def test_built_ct_survives_topology_mutation():
    """Snapshot semantics preserved: a ct built before a mutation keeps
    serving the state it was built from (the old owned-vector behavior)."""
    fp = _fp()
    t = _cands(fp)[0]
    ct = buda.ConnTopology()
    ct.build(t, fp)
    before = _snap(ct)
    segs = t.segments
    segs[0].start = buda.Point(segs[0].start.x + 7, segs[0].start.y)
    t.segments = segs
    assert _snap(ct) == before


def test_flow_level_cross_stage_reuse():
    """Phase C: one analysis, everywhere.  Across a full flow (generation ->
    planner -> NUTS -> DetailedNUTS -> persist paths), each candidate's
    analysis is computed at most TWICE — once by filter_pinched and once by
    filter_uncovered after generate_2pin/npin's deliberate post-filter
    connected_block_names assignment (an ordering that cannot move without
    changing routing bytes) — and every downstream stage is a cache hit:
    the planner's per-candidate builds, NUTS's build_nuts_maps, the CLI's
    Python-side DetailedNUTS prep, and viz/persist all reuse the same
    TopoAnalysis.  Empirically this flow shows ~2*ncand computes and >= ncand
    hits; the bounds below are the structural claims, with slack for dogleg
    re-annotation extras."""
    import contextlib, io, os
    import buda_cli
    buda.analysis_cache_reset_counters()
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    os.chdir(os.path.join(root, "flow"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for line in open("four_blocks_3_bundles.buda"):
                c = line.strip()
                if not c or c.startswith("#"):
                    continue
                if c.split()[0] in ("visualize", "visualize_topologies",
                                    "exit", "report_wirelength", "report_wl"):
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)
    ncand = sum(len(w.input.candidates) for w in s.bundles)
    computes, hits = _counters()
    assert ncand > 0
    assert computes <= 2 * ncand + 4, \
        f"more computes than the two generation-stage builds allow: " \
        f"{computes=} for {ncand=} candidates (a downstream stage stopped hitting)"
    assert hits >= ncand, \
        f"downstream stages are not reusing the cache: {hits=} for {ncand=}"
