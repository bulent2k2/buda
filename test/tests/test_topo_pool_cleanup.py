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

"""Opt-in candidate-pool cleanups: set_dedup_loci and set_drop_dangling.

Both default OFF (bit-identical) and run at the end of generation, before the
sidecar restore / BDB persistence, exactly like set_prune_dominated.

  * set_dedup_loci collapses candidates that are the same topological choice
    differing only in a nominal trunk locus inside a shared slide window
    (same connectivity + slide windows + block taps + net-pull), keeping the
    best-estimated representative.
  * set_drop_dangling drops candidates with a dangling segment (a single
    non-block connection) or an unbounded/unclamped slide window (the OOB /
    MST-relay geometry), never dropping USER or the pinned candidate.

The b44 vehicle (bus_060[52] over 3 blocks) is the reported repro: many
TRUNK_H@y* / TRUNK_V@x* loci within one pass-through slide window, plus
TRUNK_*_OOB(+MST) candidates with unclamped spines.
"""
import contextlib
import io

import pytest

import buda
import buda_cli

_B44 = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _gen(*extra, pin=None):
    s = buda_cli.BudaSession(); s.no_viz = True
    cmds = list(_B44) + list(extra) + ["run_bundler", "generate_topologies"]
    if pin is not None:
        cmds.append(f"select_topology 1 {pin}")
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _n(s):
    return len(s.bundles[0].input.candidates)


def _has_unbounded_or_dangling(s):
    fp = s.fp
    for c in s.bundles[0].input.candidates:
        ct = buda.ConnTopology(); ct.build(c, fp)
        for cs in ct.segs():
            if abs(cs.perp_lo) >= 1e8 or abs(cs.perp_hi) >= 1e8:
                return True
            if len(cs.conns) == 1 and not cs.conns[0].block_name:
                return True
    return False


# ── default OFF is bit-identical ──────────────────────────────────────────────

def test_default_off_keeps_full_pool():
    base = _n(_gen())
    assert _n(_gen("set_dedup_loci off")) == base
    assert _n(_gen("set_drop_dangling off")) == base


# ── set_dedup_loci ────────────────────────────────────────────────────────────

def test_dedup_loci_collapses_nominal_variants():
    base = _n(_gen())
    ded = _n(_gen("set_dedup_loci on"))
    assert ded < base                    # some nominal-locus variants collapsed
    # No two surviving candidates share the locus-canonical fingerprint.
    s = _gen("set_dedup_loci on")
    seen = set()
    for c in s.bundles[0].input.candidates:
        k = s._topo_loci_canon(c, s.fp)
        assert k not in seen, "a nominal-locus duplicate survived"
        seen.add(k)


def _run(s, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)


def test_dedup_loci_keeps_pinned_candidate():
    # A pin that PRE-EXISTS the dedup (pinned in a full-pool run, then dedup
    # turned on + re-generated) must survive: the cleanup never drops the
    # pinned candidate (it re-attaches by uid before the pass runs).
    s = buda_cli.BudaSession(); s.no_viz = True
    _run(s, *_B44, "run_bundler", "generate_topologies")
    # find a locus duplicate (the higher-index member of a canon-colliding pair)
    canon, dup_hi = {}, None
    for i, c in enumerate(s.bundles[0].input.candidates):
        k = s._topo_loci_canon(c, s.fp)
        if k in canon:
            dup_hi = i + 1; break
        canon[k] = i
    assert dup_hi is not None, "expected a locus duplicate in the b44 pool"
    uid = buda.topo_uid(s.bundles[0].input.candidates[dup_hi - 1])
    _run(s, f"select_topology 1 {dup_hi}", "set_dedup_loci on",
         "generate_topologies")
    kept = [buda.topo_uid(c) for c in s.bundles[0].input.candidates]
    assert uid in kept                   # the pinned duplicate was not dropped
    sel = s.bundles[0].plan.selected_topology_index
    assert buda.topo_uid(s.bundles[0].input.candidates[sel]) == uid


# ── set_drop_dangling ─────────────────────────────────────────────────────────

def test_drop_dangling_removes_unclamped_candidates():
    assert _has_unbounded_or_dangling(_gen())            # present by default
    s = _gen("set_drop_dangling on")
    assert not _has_unbounded_or_dangling(s)             # all gone
    assert _n(s) < _n(_gen())


def _n_unbounded(s):
    n = 0
    for c in s.bundles[0].input.candidates:
        ct = buda.ConnTopology(); ct.build(c, s.fp)
        if any(abs(cs.perp_lo) >= 1e8 or abs(cs.perp_hi) >= 1e8
               for cs in ct.segs()):
            n += 1
    return n


def _n_truly_dangling(s):
    n = 0
    for c in s.bundles[0].input.candidates:
        ct = buda.ConnTopology(); ct.build(c, s.fp)
        if any(len(cs.conns) == 1 and not cs.conns[0].block_name
               for cs in ct.segs()):
            n += 1
    return n


def test_on_is_the_drop_mode():
    assert _n(_gen("set_drop_dangling on")) == _n(_gen("set_drop_dangling drop"))


def test_clamp_mode_keeps_all_and_bounds_windows():
    base = _gen()
    assert _n_unbounded(base) > 0                 # unbounded present by default
    s = _gen("set_drop_dangling clamp")
    assert _n(s) == _n(base)                      # NOTHING dropped
    assert _n_unbounded(s) == 0                   # every window bounded


def test_clamp_drop_clamps_survivors_no_truly_dangling_left():
    # The generation-time seed-trunk removability gate now removes the redundant
    # trunk hybrids that were b44's truly-dangling class (single non-block conn),
    # so nothing truly-dangling reaches clamp_drop's DROP half — it degenerates to
    # clamp here (bounds every window, drops nothing).  clamp_drop's drop
    # predicate is still exercised directly by
    # test_truly_dangling_predicate_flags_a_hand_built_stub.
    base = _gen()
    assert _n_truly_dangling(base) == 0           # gate removed them at generation
    assert _n_unbounded(base) > 0                  # legit OOB-detour windows remain
    s = _gen("set_drop_dangling clamp_drop")
    assert _n(s) == _n(base)                      # nothing to drop
    assert _n_unbounded(s) == 0                   # every survivor window clamped


def test_truly_dangling_predicate_flags_a_hand_built_stub():
    # The corpus no longer produces a truly-dangling candidate (the generation
    # gate removes the redundant-trunk class), so exercise clamp_drop's drop
    # predicate directly on a hand-built stub whose far end reaches open space.
    import buda
    s = buda_cli.BudaSession(); s.no_viz = True
    _run(s, "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
         "add_block A 0 0 100 100", "add_block B 400 0 500 100",
         "add_bus busA[2] A.p B.p", "run_bundler", "generate_topologies")
    fp = s.fp
    clean = s.bundles[0].input.candidates[0]
    assert s._topo_truly_dangling_reason(clean, fp) is None    # a clean one is not flagged
    dang = buda.Topology(); dang.type = "USER"
    dang.connected_block_names = clean.connected_block_names
    seg0 = clean.segments[0]
    dang.segments = list(clean.segments) + [
        buda.Segment(buda.Point(seg0.start.x, seg0.start.y),
                     buda.Point(seg0.start.x, seg0.start.y - 999), 5)]
    buda.annotate_topology(dang, fp)
    assert s._topo_truly_dangling_reason(dang, fp) is not None   # the stub IS flagged


@pytest.mark.mid
def test_clamp_modes_route_clean():
    for mode in ("clamp", "clamp_drop"):
        s = buda_cli.BudaSession(); s.no_viz = True
        _run(s, *_B44, f"set_drop_dangling {mode}", "run_bundler",
             "generate_topologies", "run_planner", "run_nuts",
             "run_detailed_nuts")
        assert s.nuts_result.num_overlaps == 0, mode
        assert s.detailed_result.num_unplaced == 0, mode


def test_drop_dangling_keeps_a_pinned_dangling_candidate():
    # A pin that pre-exists the drop must survive even though it dangles.
    s = buda_cli.BudaSession(); s.no_viz = True
    _run(s, *_B44, "run_bundler", "generate_topologies")
    dang = None
    for i, c in enumerate(s.bundles[0].input.candidates):
        ct = buda.ConnTopology(); ct.build(c, s.fp)
        if any(abs(cs.perp_lo) >= 1e8 or abs(cs.perp_hi) >= 1e8
               for cs in ct.segs()):
            dang = i + 1; break
    assert dang is not None
    uid = buda.topo_uid(s.bundles[0].input.candidates[dang - 1])
    _run(s, f"select_topology 1 {dang}", "set_drop_dangling on",
         "generate_topologies")
    assert uid in [buda.topo_uid(c) for c in s.bundles[0].input.candidates]


def test_dedup_excludes_bridged_candidates():
    """TEG-over bridge wires live OUTSIDE ConnTopology.segs(), so a bridged
    candidate must never participate in the locus dedup — else two candidates
    with the same skeleton but different bridge wires would collide and the
    bridged one could be dropped (Codex #389).  Mirrors the WL-prune exclusion."""
    s = buda_cli.BudaSession(); s.no_viz = True
    _run(s,
         "source flow/tracks/tracks4top.buda",
         "add_block src 200 10830 700 11330",
         "add_block dst 4000 10830 4500 11330",
         "add_block relay rect 1500 10500 2000 11000 "
         "rect 2500 11200 3000 11700 teg_mode over",
         "add_bus b[8] src.p dst.p,relay.p",
         "run_bundler", "generate_topologies")
    cands = s.bundles[0].input.candidates
    bridged = [c for c in cands if c.bridge_segments]
    assert bridged, "expected TEG-over bridge candidates in this scenario"
    for c in bridged:
        assert s._topo_loci_canon(c, s.fp) is None   # never a dedup key


def test_both_knobs_compose():
    s = _gen("set_dedup_loci on", "set_drop_dangling on")
    n = _n(s)
    assert 0 < n < _n(_gen())
    assert not _has_unbounded_or_dangling(s)


# ── end-to-end: a kept representative still routes clean ───────────────────────

@pytest.mark.mid
def test_dedup_representative_routes_like_the_collapsed_member():
    """A collapsed nominal-locus group's members route within realization-noise;
    the surviving representative routes clean."""
    def route(*extra, pin=1):
        s = buda_cli.BudaSession(); s.no_viz = True
        cmds = list(_B44) + list(extra) + [
            "run_bundler", "generate_topologies", f"select_topology 1 {pin}",
            "run_planner", "run_nuts", "run_detailed_nuts"]
        with contextlib.redirect_stdout(io.StringIO()):
            for c in cmds:
                s.do_command(c)
        return (sum(abs(n.span_hi - n.span_lo) for n in s.detailed_result.net_segments),
                s.detailed_result.num_unplaced)
    wl, unplaced = route("set_dedup_loci on", "set_drop_dangling on", pin=1)
    assert unplaced == 0 and wl > 0
