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

"""Full-pipeline exercise of the bundler's CONVERGENT strategy (via the CLI).

CONVERGENT bundles nets by their *shared receiver instance* only, ignoring the
driver — so several nets from DIFFERENT drivers that fan in to one sink become a
single bundle.  These tests drive the whole flat pipeline through the CLI
(`run_bundler {STRICT|CONVERGENT}` → topology → planner → NUTS) for such a
fan-in and contrast the two strategies.

Historically CONVERGENT was UNSOUND: the topology generator represented a
bundle by a single src→dst pair, so a multi-driver bundle routed from ONE
arbitrary driver and the rest were silently unrouted, and check_design never
noticed (it checked internal self-consistency only).  These are the inverted
ACCEPTANCE tests for the fan-in tree fix (docs/internal/convergent_bundling.md):
`_bundle_endpoints` derives the endpoints from ALL of a bundle's nets and roots
a multi-driver bundle at the shared sink with every driver as a leaf, so the
multicast/MST machinery physically attaches every driver block; the new
net-driver fidelity check (`NET_DRIVER_OPEN`) flags any topology whose
connected-block contract drops an endpoint block.
"""

import buda_cli

# Four source blocks at well-separated rows, each driving one net into a single
# common sink on the right.  Different drivers + shared receiver = the case where
# CONVERGENT differs from STRICT.
_TRACKS = "def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"
_SETUP = """add_block src0 0 0 100 80
add_block src1 0 200 100 280
add_block src2 0 400 100 480
add_block src3 0 600 100 680
add_block sink 800 250 950 450
add_net a0 src0.tx sink.r0
add_net a1 src1.tx sink.r1
add_net a2 src2.tx sink.r2
add_net a3 src3.tx sink.r3
"""

# Approximate row (y) of each source's horizontal run; ~200 units apart.
_SOURCE_ROWS = (40, 240, 440, 640)
_ROW_TOL = 80


def _build_session():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for line in (_TRACKS + _SETUP).strip().splitlines():
        sess.do_command(line)
    return sess


def _run_pipeline(strategy):
    """Bundle via the CLI (`run_bundler <strategy>`), then run the rest of the
    pipeline through the same session.  Returns (sess, raw_bundles)."""
    sess = _build_session()
    sess.do_command(f"run_bundler {strategy}")
    raw = [w.input.original_bundle for w in sess.bundles]
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        sess.do_command(cmd)
    return sess, raw


def _h_rows(sess):
    """Distinct horizontal-run rows (rounded) placed by NUTS — one per source row
    actually reached by routing."""
    return sorted({round(s.track_position)
                   for s in sess.nuts_result.segments if s.horiz})


def _covered(rows):
    return {t for t in _SOURCE_ROWS
            if any(abs(r - t) < _ROW_TOL for r in rows)}


def _blocks_covered_by_nuts(sess, blocks):
    """Blocks whose bbox is crossed/touched by some placed NUTS segment —
    the physical 'this driver is attached' predicate (a fan-in tree may tap
    a driver with a stub at its row OR pass a trunk through its bbox)."""
    covered = set()
    for name in blocks:
        r = sess.fp.get_block_bounds(name)
        for s in sess.nuts_result.segments:
            if not s.placed:
                continue
            if s.horiz:
                hit = (r.y1 <= s.track_position <= r.y2
                       and s.span_lo <= r.x2 and s.span_hi >= r.x1)
            else:
                hit = (r.x1 <= s.track_position <= r.x2
                       and s.span_lo <= r.y2 and s.span_hi >= r.y1)
            if hit:
                covered.add(name)
                break
    return covered


_SOURCES = ("src0", "src1", "src2", "src3")


def test_strict_fanin_routes_every_driver():
    # STRICT keeps the four different-driver nets in four separate bundles, each
    # routed from its own driver → every source row is reached.
    sess, raw = _run_pipeline("STRICT")
    assert len(raw) == 4
    assert sess.nuts_result.num_overlaps == 0
    rows = _h_rows(sess)
    assert len(rows) == 4
    assert _covered(rows) == set(_SOURCE_ROWS)   # all four drivers routed


def test_convergent_fanin_routes_every_driver():
    # ACCEPTANCE (inverted from the historical collapses-to-one-driver test):
    # CONVERGENT groups all four nets (shared receiver `sink`) into ONE bundle,
    # and the fan-in tree physically attaches EVERY driver block — the topology
    # is rooted at the sink with all four sources as leaves.
    sess, raw = _run_pipeline("CONVERGENT")
    assert len(raw) == 1
    assert raw[0].reason == "REC:sink"
    assert sorted(raw[0].get_net_names()) == ["a0", "a1", "a2", "a3"]

    # Every driver block (and the sink) is covered by placed routing.
    covered = _blocks_covered_by_nuts(sess, _SOURCES + ("sink",))
    assert covered == set(_SOURCES + ("sink",)), (
        f"fan-in bundle must attach every driver; missing "
        f"{set(_SOURCES + ('sink',)) - covered}")
    # The topology's required-block contract carries all five blocks.
    w = sess.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    assert set(topo.connected_block_names) == set(_SOURCES + ("sink",))


def test_convergent_topo_check_passes_and_fidelity_flags_dropped_driver(capsys):
    # ACCEPTANCE (inverted from the historical does-not-flag test): the routed
    # fan-in bundle passes check_design; a topology that models the bundle from
    # a single driver (the historical unsound shape) is now FLAGGED by the
    # net-driver fidelity check (NET_DRIVER_OPEN).
    sess, _ = _run_pipeline("CONVERGENT")
    capsys.readouterr()                       # clear pipeline output
    sess.do_command("check_design topo")
    out = capsys.readouterr().out.lower()
    assert "no opens" in out or "success" in out

    # Regress the bundle to the historical single-driver modelling and
    # re-check: three drivers are now missing from the contract.
    import buda
    w = sess.bundles[0]
    topo_gen = sess._make_topo_gen(sess.fp, False, False, False)
    w.input.candidates = topo_gen.generate_candidates("src0", ["sink"])
    w.plan.selected_topology_index = 0
    sess.do_command("check_design topo")
    out = capsys.readouterr().out
    assert "NET_DRIVER_OPEN" in out or "net endpoint block" in out
    for missing in ("src1", "src2", "src3"):
        assert missing in out


def test_convergent_mixed_shared_and_distinct_drivers():
    # Mixed case (wishlist gap): two nets share driver `ma`, one comes from
    # `mb` — CONVERGENT groups all three by the shared sink, and the fan-in
    # tree attaches BOTH driver blocks.  Blocks scattered (not one column) so
    # the tree needs real branches, not a single pass-through trunk.
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for line in ("def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"
                 "def_track_pattern 4 0 SIGNAL 1 1\n"
                 "def_track_pattern 5 0 SIGNAL 1 1\n"
                 "add_block ma 0 0 100 80\n"
                 "add_block mb 300 600 400 680\n"
                 "add_block sink 800 250 950 450\n"
                 "add_net w0 ma.tx0 sink.r0\n"
                 "add_net w1 ma.tx1 sink.r1\n"
                 "add_net w2 mb.tx sink.r2\n").strip().splitlines():
        sess.do_command(line)
    sess.do_command("run_bundler CONVERGENT")
    raw = [w.input.original_bundle for w in sess.bundles]
    assert len(raw) == 1 and sorted(raw[0].get_net_names()) == ["w0", "w1", "w2"]
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        sess.do_command(cmd)
    covered = _blocks_covered_by_nuts(sess, ("ma", "mb", "sink"))
    assert covered == {"ma", "mb", "sink"}, f"missing {set(['ma','mb','sink']) - covered}"
    w = sess.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    assert set(topo.connected_block_names) == {"ma", "mb", "sink"}

    # Tapered per-bit fidelity (Codex #268 P1): each net's bits ride ONLY the
    # segments on its own driver→sink path.  ma's stub carries bits {0,1}
    # (nets w0/w1), mb's stub carries {2} (w2), the shared trunk all three —
    # and the widths taper accordingly (pitch 2 → 6/4/2), so no net's wire
    # ever lands on the OTHER driver's block.
    sb = dict(topo.seg_bits)
    assert sb, "fan-in bundle must carry per-segment bit membership"
    stub_bits = sorted(tuple(v) for v in sb.values() if len(v) < 3)
    assert stub_bits == [(0, 1), (2,)], sb
    widths = sorted(ts.width for ts in sess.nuts_result.segments)
    assert widths == [2.0, 4.0, 6.0], widths

    sess.do_command("run_detailed_nuts")
    assert sess.detailed_result.num_unplaced == 0
    per_seg = {}
    for ns in sess.detailed_result.net_segments:
        per_seg.setdefault(ns.seg_idx, set()).add(ns.bit_index)
    assert sorted(map(tuple, map(sorted, per_seg.values()))) == \
        sorted(map(tuple, (v for v in sb.values()))), (per_seg, sb)
    # w2 (bit 2, driven by mb) has NO wire touching ma, and w0/w1 none on mb.
    ma = sess.fp.get_block_bounds("ma")
    mb = sess.fp.get_block_bounds("mb")

    def _touches(ns, r):
        if ns.layer in (4,):   # H layer
            return (r.y1 <= ns.track_position <= r.y2
                    and ns.span_lo <= r.x2 and ns.span_hi >= r.x1)
        return (r.x1 <= ns.track_position <= r.x2
                and ns.span_lo <= r.y2 and ns.span_hi >= r.y1)

    for ns in sess.detailed_result.net_segments:
        if ns.bit_index == 2:
            assert not _touches(ns, ma), "w2's wire landed on ma"
        else:
            assert not _touches(ns, mb), f"w{ns.bit_index}'s wire landed on mb"
    # Vias pair by GLOBAL bit index: trunk↔ma-stub vias for bits 0,1 and
    # trunk↔mb-stub for bit 2 only.
    via_bits = {}
    for v in sess.detailed_result.net_vias:
        via_bits.setdefault((v.from_seg, v.to_seg), set()).add(v.bit_index)
    for pair, bits in via_bits.items():
        assert bits <= {0, 1} or bits == {2}, (pair, bits)
    # check_design dnuts must be CLEAN on a tapered bundle: the UNPLACED
    # audit expects only the bits each segment CARRIES (seg_bits) — the
    # deliberately-absent non-member bits are not unplaced.
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sess.do_command("check_design dnuts")
    assert "Success" in out.getvalue(), out.getvalue()


def test_cli_run_bundler_honors_strategy_argument(capsys):
    # The CLI `run_bundler` command honours its argument: STRICT yields four
    # bundles (one per driver), CONVERGENT yields the single shared-receiver
    # bundle and prints the fan-in note (the historical unrouted-driver
    # WARNING is gone — multi-driver bundles now route as fan-in trees).
    strict = _build_session()
    strict.do_command("run_bundler STRICT")
    assert len(strict.bundles) == 4

    conv = _build_session()
    capsys.readouterr()
    conv.do_command("run_bundler CONVERGENT")
    out = capsys.readouterr().out.lower()
    assert len(conv.bundles) == 1
    assert "fan-in" in out and "convergent" in out
    assert "warning" not in out

    # A bare `run_bundler` still defaults to STRICT, and a bad argument errors.
    bare = _build_session()
    bare.do_command("run_bundler")
    assert len(bare.bundles) == 4
    bad = _build_session()
    capsys.readouterr()
    bad.do_command("run_bundler SOMETHING")
    assert "error" in capsys.readouterr().out.lower()
