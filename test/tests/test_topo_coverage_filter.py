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

"""Generation-time coverage gate (`TopologyGenerator.filter_uncovered`).

`generate_candidates` now runs a uniform coverage post-filter over every
generation path.  It drops two silent-open risks the planner cannot detect and
no downstream stage catches:

  * `BUSTERM_OPEN` — a candidate that leaves a `connected_block_names` block with
    no busterm tap and no pass-through segment (an uncovered block).  ALWAYS
    dropped.
  * `FEEDTHRU_RELAY` — the legacy multi-rect / rootless trunk+MST fallback whose
    incident wires do not physically touch (a silent feedthru relay).  Dropped
    too, but ONLY when a clean (neither open nor relay) candidate survives.

Never-strand guard: if EVERY candidate is uncovered — or a bundle's ONLY options
are relays — the list is kept unchanged (with a warning) so the planner's
escalation ladder still commits one, and check_design reports the issue.

This supersedes the "planner coverage gate" wishlist item: the planner stays
focused on capacity/congestion, and bad candidates never reach it.

NOTE: TopologyGenerator holds a C++ reference to its Floorplan, so every helper
returns (fp, tg) and callers must keep fp alive for tg's lifetime.
"""

import buda


def _mk_gen():
    fp = buda.Floorplan()
    fp.add_block("a", 0, 0, 100, 80)
    fp.add_block("b", 400, 0, 500, 80)
    fp.add_block("c", 200, 300, 300, 380)   # off-path third block
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    return fp, tg


def _broken(cand):
    """A copy of `cand` that claims to also connect block 'c', which none of its
    segments taps or crosses → BUSTERM_OPEN → the gate must drop it."""
    bad = buda.Topology()
    bad.type = cand.type + "_broken"
    bad.segments = list(cand.segments)
    bad.seg_busterms = dict(cand.seg_busterms)
    bad.connected_block_names = list(cand.connected_block_names) + ["c"]
    return bad


def test_filter_drops_uncovered_candidate(capfd):
    fp, tg = _mk_gen()
    cands = tg.generate_candidates("a", ["b"])
    assert cands, "expected candidates for a->b"

    mixed = list(cands) + [_broken(cands[0])]
    kept = tg.filter_uncovered(mixed)
    assert len(kept) == len(cands)                       # exactly the bad one dropped
    assert all(not t.type.endswith("_broken") for t in kept)
    err = capfd.readouterr().err
    assert "dropped 1 candidate" in err                  # flagged, not silent
    assert "block 'c'" in err


def test_filter_never_strands_a_bundle(capfd):
    fp, tg = _mk_gen()
    cands = tg.generate_candidates("a", ["b"])
    all_bad = [_broken(c) for c in cands]
    kept = tg.filter_uncovered(all_bad)
    assert len(kept) == len(all_bad)                     # kept unchanged
    assert "WARNING" in capfd.readouterr().err           # ...but loudly


def test_generate_candidates_output_is_covered():
    # The gate runs inside generate_candidates on every path; everything it
    # returns must pass check_topo's coverage (no BUSTERM_OPEN).
    fp, tg = _mk_gen()
    for dsts in (["b"], ["b", "c"]):                     # 2-pin and n-pin paths
        for cand in tg.generate_candidates("a", dsts):
            ct = buda.ConnTopology()
            ct.build(cand, fp)
            res = buda.check_topo(ct, cand, fp, -1)
            assert not any(v.kind == buda.ViolationKind.BUSTERM_OPEN
                           for v in res.violations), cand.type


def test_feedthru_candidates_survive_the_gate(capfd):
    # A declared feedthru splits the trunk spine at the relay block's faces; the
    # relay is a covered busterm, so the gate must keep these candidates.
    # Geometry mirrors test_feedthru_topology.py: 'mid' straddles every trunk y.
    fp = buda.Floorplan()
    fp.add_block("A",   0, 100, 100, 200)
    fp.add_block("mid", 300, 0, 400, 300)
    fp.add_block("B",   600, 100, 700, 200)
    fp.set_feedthru_block("mid", True)
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    cands = tg.generate_candidates("A", ["mid", "B"])
    assert cands, "feedthru flow lost all its candidates"
    assert any("mid" in list(c.feedthru_blocks) for c in cands), \
        "expected at least one candidate using the declared feedthru relay"
    assert "WARNING: all" not in capfd.readouterr().err


def _relay_kinds(cand, fp):
    ct = buda.ConnTopology()
    ct.build(cand, fp)
    return {v.kind for v in buda.check_topo(ct, cand, fp, -1).violations}


def test_undeclared_feedthru_relay_dropped_when_clean_alternative_exists(capfd):
    """A multi-rect (notched) destination yields a TRUNK+MST hybrid that cannot be
    cleanly completed — check_topo flags it FEEDTHRU_RELAY (a silent feedthru no
    downstream stage catches).  With clean trunk alternatives present, the gate
    must drop it so the planner can never select an unbuildable relay."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 400)
    fp.add_block_rects("B", [(300, 0, 400, 150), (300, 250, 400, 400)])  # notch at y in [150,250]
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    cands = tg.generate_candidates("A", ["B"])
    assert cands, "multi-rect flow lost all its candidates"
    # No surviving candidate carries an (undeclared) feedthru relay.
    survivors_with_relay = [c.type for c in cands
                            if buda.ViolationKind.FEEDTHRU_RELAY in _relay_kinds(c, fp)]
    assert not survivors_with_relay, f"relay candidate survived the gate: {survivors_with_relay}"
    # ...and clean trunk candidates remain (the bundle is not stranded).
    assert any(buda.ViolationKind.BUSTERM_OPEN not in _relay_kinds(c, fp) for c in cands)
    # The drop is logged.  A relay hybrid whose seed trunk is redundant is now
    # dropped earlier by the seed-trunk removability gate ("redundant trunk+MST")
    # rather than by filter_uncovered's FEEDTHRU_RELAY cull ("feedthru-relay");
    # either way it is not silent.
    err = capfd.readouterr().err
    assert "feedthru-relay" in err or "redundant trunk+MST" in err
