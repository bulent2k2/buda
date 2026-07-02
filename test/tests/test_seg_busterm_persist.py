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

"""Phase 3 — logical persistence of seg_busterms (single source of topo-truth).

`generate_topologies` writes each candidate's authoritative seg_busterms
annotation into the BDB *logically*: a routing-time busterm row ('tb:<block>',
carrying the full Busterm — margin bbox, orig bbox, multi-rect, TEG) plus a
`topology_seg_busterm` link naming which segment endpoint taps it.  A reload
rebuilds the annotation from those rows ALONE — no geometric re-derivation, no
floorplan — so a BDB round-trip never reintroduces the fallback Phase 2 removed.

See docs/internal/single_source_topo_truth.md (Phase 3).
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
    """Flat flow: single-rect driver A → multi-rect (TEG-over) receiver M, so the
    seg_busterms carry a single-rect tap, a multi-rect+TEG tap, and (on the L/Z
    candidates) internal junctions — every case the persistence must cover."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {bdb_path}",
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block M rect 900 0 980 400 rect 1020 0 1100 400 teg_mode over",
           "add_bus d[8] A.p M.p",
           "run_bundler",
           "generate_topologies")
    return s


def _norm_bt(bt):
    """A Busterm reduced to a comparable tuple (the whole one-true-source content)."""
    if bt is None:
        return None
    return (bt.block_name, str(bt.teg_mode),
            (bt.bbox.x1, bt.bbox.y1, bt.bbox.x2, bt.bbox.y2),
            (bt.orig_bbox.x1, bt.orig_bbox.y1, bt.orig_bbox.x2, bt.orig_bbox.y2),
            tuple((r.x1, r.y1, r.x2, r.y2) for r in bt.rects))


def _norm_sb(sb):
    """Canonical seg_busterms: an all-junction (None, None) segment carries no
    connectivity and is *not* persisted (zero rows), so drop it before comparing —
    the reloaded form legitimately omits it."""
    out = {}
    for si, (a, b) in sb.items():
        na, nb = _norm_bt(a), _norm_bt(b)
        if na is None and nb is None:
            continue
        out[si] = (na, nb)
    return out


def _reload(bdb, bid, ci):
    t = buda.Topology()
    buda.load_seg_busterms(bdb, bid, ci, t)
    return t


def test_seg_busterms_roundtrip_all_candidates(tmp_path):
    """Every candidate's seg_busterms reloads bit-for-bit from the persisted rows."""
    s = _session(str(tmp_path / "sb.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    assert len(w.input.candidates) >= 1
    for ci, cand in enumerate(w.input.candidates):
        got = _norm_sb(_reload(s.bdb, bid, ci).seg_busterms)
        want = _norm_sb(cand.seg_busterms)
        assert want, f"cand {ci} ({cand.type}) has no taps — test is vacuous"
        assert got == want, f"cand {ci} ({cand.type}) seg_busterms diverged on reload"


def test_multi_rect_and_teg_preserved(tmp_path):
    """The multi-rect + TEG solution survives as the busterm's own truth — the
    reason the busterm (not block geometry) is the one-true-source it references."""
    s = _session(str(tmp_path / "sb.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    # Find the M tap across the reloaded candidates.
    m_taps = []
    for ci in range(len(w.input.candidates)):
        for _si, (a, b) in _reload(s.bdb, bid, ci).seg_busterms.items():
            m_taps += [bt for bt in (a, b) if bt is not None and bt.block_name == "M"]
    assert m_taps, "receiver M was never tapped"
    for bt in m_taps:
        assert bt.teg_mode == buda.TegMode.OVER, "TEG mode lost on reload"
        assert len(bt.rects) == 2, "multi-rect geometry lost on reload"
        assert (bt.rects[0].x1, bt.rects[1].x2) == (900, 1100)


def test_junction_endpoints_are_absent(tmp_path):
    """A junction endpoint (nullopt) writes no row and reloads as None — the
    default is 'no tap', never a guessed busterm."""
    s = _session(str(tmp_path / "sb.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    saw_junction = False
    for ci, cand in enumerate(w.input.candidates):
        reloaded = _reload(s.bdb, bid, ci).seg_busterms
        for si, (a, b) in cand.seg_busterms.items():
            for endpoint, bt in ((0, a), (1, b)):
                if bt is not None:
                    continue
                saw_junction = True
                got = reloaded.get(si)
                got_ep = None if got is None else (got[0] if endpoint == 0 else got[1])
                assert got_ep is None, "a junction endpoint came back as a tap"
    assert saw_junction, "no junction endpoint exercised — test is vacuous"


def test_reloaded_topology_drives_conn_topology(tmp_path):
    """End-to-end: rebuild a candidate's segments (from topology_segment) + its
    seg_busterms (from the new links), then run ConnTopology against the floorplan.
    The reloaded annotation must yield the SAME busterm taps as the in-memory
    candidate and verify clean — proving connectivity is restored logically, not
    re-derived from geometry (which is exactly what Phase 2 forbade)."""
    s = _session(str(tmp_path / "sb.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)

    def taps(ct):
        return sorted({cc.block_name for cs in ct.segs() for cc in cs.conns
                       if cc.kind == buda.SegConnKind.BUSTERM})

    def viols(res):
        return sorted((str(v.kind), v.block_name) for v in res.violations)

    for ci, cand in enumerate(w.input.candidates):
        # Reconstruct geometry from the persisted segment rows...
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
        # ...and connectivity from the persisted seg-busterm links (logical).
        buda.load_seg_busterms(s.bdb, bid, ci, t)

        ct_re = buda.ConnTopology(); ct_re.build(t, s.fp)
        ct_mem = buda.ConnTopology(); ct_mem.build(cand, s.fp)
        # The reloaded topology behaves IDENTICALLY to the in-memory one — same
        # taps and the same check_topo verdict (violations and all).  A candidate
        # that legitimately carries a violation must reproduce it exactly; the
        # reload adds nothing and drops nothing.
        assert taps(ct_re) == taps(ct_mem), f"cand {ci} ({cand.type}) taps diverged"
        assert viols(buda.check_topo(ct_re, t, s.fp, 8)) == \
               viols(buda.check_topo(ct_mem, cand, s.fp, 8)), \
               f"cand {ci} ({cand.type}) check_topo verdict diverged on reload"


def test_roundtrip_through_sql(tmp_path):
    """The new busterm columns + topology_seg_busterm table survive the diffable
    *.bdb.sql serialization: dump → rebuild → reopen → reload matches in-memory."""
    path = str(tmp_path / "sb.bdb")
    s = _session(path)
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    before = {ci: _norm_sb(cand.seg_busterms)
              for ci, cand in enumerate(w.input.candidates)}

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    rebuilt = bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb"))
    db2 = buda.BDB(rebuilt)

    after = {ci: _norm_sb(_reload(db2, bid, ci).seg_busterms) for ci in before}
    assert after == before
    # The hier-derived busterm query stays unpolluted by the 'tb:' routing rows.
    assert all(not b.id.startswith("tb:") for b in db2.all_busterms())
