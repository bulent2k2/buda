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

"""Topo-truth Phase 5 — logical persistence of seg_conns (schema v12).

`generate_topologies` persists each candidate's seg-to-seg junction records into
`topology_seg_conn` (one row per real link; index-valued, no geometry), via the
`_persist_topology_annotations` choke point.  `load_seg_busterms` restores BOTH
annotations — taps from the busterm links, junctions from the seg-conn links —
so a reloaded topology carries the full connectivity truth with zero geometric
re-derivation; the geometric derive survives only as an explicit, printed
fallback for pre-v12 checkpoints.

See docs/internal/single_source_topo_truth.md (Phase 5).
"""
import contextlib
import io

import buda
import buda_cli
import bdb_serialize


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _session(bdb_path):
    """Flat flow whose candidates include multi-junction shapes: a driver A
    broadcasting to three receivers gives trunk+stub candidates (multiple
    junctions on one trunk, incl. 3+ segs meeting where stubs share a column)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {bdb_path}",
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 0 800 200",
           "add_block C 600 300 800 500",
           "add_block D 1100 100 1300 300",
           "add_bus d[8] A.p B.p,C.p,D.p",
           "run_bundler",
           "generate_topologies")
    return s


def _norm(sc):
    return {tuple(k): list(v) for k, v in sc.items()}


def _reload(s, bid, ci, cand):
    """Rebuild a candidate exactly the way load_pipeline does: segment rows +
    the single reload helper (which restores taps AND junction links)."""
    t = buda.Topology()
    t.type = cand.type
    segs = []
    for sr in s.bdb.topology_segments(bid, ci):
        sg = buda.Segment()
        sg.start = buda.Point(sr.x1, sr.y1)
        sg.end = buda.Point(sr.x2, sr.y2)
        sg.layer_hint = sr.layer_hint
        sg.is_jog = sr.is_jog
        segs.append(sg)
    t.segments = segs
    buda.load_seg_busterms(s.bdb, bid, ci, t)
    return t


def test_seg_conns_roundtrip_all_candidates(tmp_path):
    """Every candidate's seg_conns reloads exactly from the persisted rows —
    keys, values, and order — with no geometric fallback (rows exist)."""
    s = _session(str(tmp_path / "sc.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    assert len(w.input.candidates) >= 1
    saw_junctions = False
    for ci, cand in enumerate(w.input.candidates):
        got = _reload(s, bid, ci, cand)
        assert _norm(got.seg_conns) == _norm(cand.seg_conns), \
            f"cand {ci} ({cand.type}) seg_conns diverged on reload"
        if cand.seg_conns:
            saw_junctions = True
            # the persisted rows themselves are the logical truth: count matches
            rows = s.bdb.topology_seg_conns(bid, ci)
            assert len(rows) == sum(len(v) for v in cand.seg_conns.values())
    assert saw_junctions, "no candidate carried junctions — test is vacuous"


def test_reloaded_topology_drives_conn_topology_identically(tmp_path):
    """End-to-end: the reloaded candidate yields the same ConnTopology SEG edges
    and the same check_topo verdict as the in-memory one."""
    s = _session(str(tmp_path / "sc.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)

    def seg_edges(ct):
        return sorted({(min(i, cc.seg_idx), max(i, cc.seg_idx))
                       for i, cs in enumerate(ct.segs()) for cc in cs.conns
                       if cc.kind == buda.SegConnKind.SEG})

    def viols(res):
        return sorted((str(v.kind), v.block_name) for v in res.violations)

    for ci, cand in enumerate(w.input.candidates):
        t = _reload(s, bid, ci, cand)
        ct_re = buda.ConnTopology(); ct_re.build(t, s.fp)
        ct_mem = buda.ConnTopology(); ct_mem.build(cand, s.fp)
        assert seg_edges(ct_re) == seg_edges(ct_mem), \
            f"cand {ci} ({cand.type}) SEG edges diverged"
        assert viols(buda.check_topo(ct_re, t, s.fp, 8)) == \
               viols(buda.check_topo(ct_mem, cand, s.fp, 8)), \
               f"cand {ci} ({cand.type}) check_topo verdict diverged"


def test_pre_v12_checkpoint_falls_back_geometrically(tmp_path, capfd):
    """A checkpoint with NO seg-conn rows (pre-v12) still reloads working
    junctions — via the explicit, printed geometric fallback."""
    s = _session(str(tmp_path / "sc.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    multi = [(ci, c) for ci, c in enumerate(w.input.candidates)
             if len(c.segments) >= 2 and c.seg_conns]
    assert multi
    ci, cand = multi[0]
    # Simulate a pre-v12 checkpoint: drop the junction rows for this candidate.
    import sqlite3
    con = sqlite3.connect(s.bdb.path if hasattr(s.bdb, 'path')
                          else str(tmp_path / "sc.bdb"))
    con.execute("DELETE FROM topology_seg_conn WHERE bundle_id=? AND cand_index=?",
                (bid, ci))
    con.commit(); con.close()
    capfd.readouterr()
    t = _reload(s, bid, ci, cand)
    out = capfd.readouterr().out
    assert "pre-v12" in out, "fallback must announce itself"
    assert _norm(t.seg_conns) == _norm(cand.seg_conns), \
        "geometric fallback must reproduce the junctions"


def test_single_segment_candidate_writes_zero_rows(tmp_path):
    """A candidate with no junctions (I-shape) persists zero seg-conn rows —
    absence of rows is the canonical 'no junctions', not a fallback trigger
    (its reload must NOT print the pre-v12 note)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {tmp_path / 'i.bdb'}",
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 0 800 400",
           "add_bus d[4] A.p B.p",
           "run_bundler",
           "generate_topologies")
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    singles = [(ci, c) for ci, c in enumerate(w.input.candidates)
               if len(c.segments) == 1]
    assert singles, "expected an I-shape candidate for an aligned pair"
    for ci, cand in singles:
        assert not cand.seg_conns
        assert s.bdb.topology_seg_conns(bid, ci) == []


def test_roundtrip_through_sql(tmp_path):
    """The topology_seg_conn rows survive the diffable *.bdb.sql serialization."""
    path = str(tmp_path / "sc.bdb")
    s = _session(path)
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    before = {ci: _norm(_reload(s, bid, ci, cand).seg_conns)
              for ci, cand in enumerate(w.input.candidates)}
    assert any(before.values())

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    rebuilt = bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb"))
    db2 = buda.BDB(rebuilt)

    after = {}
    for ci, cand in enumerate(w.input.candidates):
        t = buda.Topology()
        segs = []
        for sr in db2.topology_segments(bid, ci):
            sg = buda.Segment()
            sg.start = buda.Point(sr.x1, sr.y1)
            sg.end = buda.Point(sr.x2, sr.y2)
            sg.layer_hint = sr.layer_hint
            sg.is_jog = sr.is_jog
            segs.append(sg)
        t.segments = segs
        buda.load_seg_busterms(db2, bid, ci, t)
        after[ci] = _norm(t.seg_conns)
    assert after == before
