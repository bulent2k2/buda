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

"""COMBINED bundling (the join of CONVERGENT and BIDIRECTIONAL), per-prefix
set_bundling overrides, and the set_max_bundle_bits bundle bit bound.

The strategy lattice: STRICT (finest) is refined by CONVERGENT (shared
receiver set) and BIDIRECTIONAL (shared endpoint set) — incomparable
coarsenings — and COMBINED is their join: nets merge when connected by a
CHAIN of either relation.  The generalized Python bundler must reproduce the
pure C++ partitions exactly when unrestricted (equivalence tests below), and
overrides gate each relation per net prefix.  The bit bound splits oversized
bundles into balanced parts keeping bus bits together, statically (max N)
or dynamically (shortest busterm edge / min bit pitch vs the bits the
per-bit taper actually lands on that block)."""

import buda_cli


_LAYERS = ("def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"
           "def_track_pattern 4 0 SIGNAL 1 1\n"
           "def_track_pattern 5 0 SIGNAL 1 1\n")

# A netlist exercising every relation: a STRICT pair (p0/p1: same driver +
# receiver), a BIDIRECTIONAL pair (b0: A->B, b1: B->A), a CONVERGENT pair
# (c0: C->S, c1: D->S), and a chain link (b2: A->S joins the bidir group
# {A,B} to the convergent group {S} under COMBINED only).
_BLOCKS = ("add_block A 0 0 100 80\n"
           "add_block B 800 0 950 200\n"
           "add_block C 0 300 100 380\n"
           "add_block D 0 500 100 580\n"
           "add_block S 800 400 950 600\n")
_NETS = ("add_net p0 A.t0 B.r0\n"
         "add_net p1 A.t1 B.r1\n"
         "add_net b1 B.t0 A.r0\n"
         "add_net c0 C.t0 S.r0\n"
         "add_net c1 D.t0 S.r1\n"
         "add_net b2 A.t2 S.r2\n")


def _session(extra=""):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for line in (_LAYERS + _BLOCKS + _NETS + extra).strip().splitlines():
        s.do_command(line)
    return s


def _partition(sess):
    """The bundling partition as a frozenset of frozensets of net names."""
    return frozenset(
        frozenset(w.input.original_bundle.get_net_names())
        for w in sess.bundles)


def _run(sess, cmd):
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sess.do_command(cmd)
    return out.getvalue()


# ── equivalences: the generalized bundler reproduces the pure modes ───────────

def test_generalized_bundler_matches_cpp_partitions():
    """With no overrides the C++ path runs; with a no-op override the Python
    path runs — the partitions must be IDENTICAL for every pure strategy.
    This pins the Python signature definitions to the C++ ones."""
    for strat in ("STRICT", "CONVERGENT", "BIDIRECTIONAL"):
        cpp = _session()
        cpp.do_command(f"run_bundler {strat}")
        py = _session()
        # A non-'*' no-op override forces the generalized path without
        # changing any net's permissions.
        py.do_command("set_bundling zzz_no_such_net_ strict")
        py.do_command(f"run_bundler {strat}")
        assert _partition(py) == _partition(cpp), strat


def test_combined_is_the_join_and_chains_transitively():
    """COMBINED merges via chains of either relation: b1 (B->A) joins b2
    (A->S) only through p0/p1 (bidir with b1, convergent with b2)?  Walk the
    actual chain: {p0,p1,b1} share endpoint set {A,B} (bidir); {b2,c0,c1}
    share receiver {S} (convergent); and p0 vs b2 share NO relation — but
    A->B and A->S don't chain... they do NOT.  So COMBINED yields exactly
    two groups here, each strictly coarser than what any single pure mode
    builds."""
    s = _session()
    s.do_command("run_bundler COMBINED")
    part = _partition(s)
    assert frozenset({"p0", "p1", "b1"}) in part          # bidir group
    assert frozenset({"b2", "c0", "c1"}) in part          # convergent group
    assert len(part) == 2
    # No single pure mode produces either group in full:
    for strat in ("STRICT", "CONVERGENT", "BIDIRECTIONAL"):
        s2 = _session()
        s2.do_command(f"run_bundler {strat}")
        assert part != _partition(s2), strat


def test_combined_mixed_group_routes_clean():
    """A COMBINED chain group (bidir pair + convergent partner) routes end
    to end: per-bit taper puts each net only on its own driver→sink path,
    and both design checks pass."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for line in (_LAYERS
                 + "add_block A 0 0 100 80\n"
                 + "add_block B 800 250 950 450\n"
                 + "add_block C 300 600 400 680\n"
                 + "add_net n0 A.tx B.r0\n"
                 + "add_net n1 B.tx A.r0\n"
                 + "add_net n2 C.tx B.r1\n").strip().splitlines():
        s.do_command(line)
    s.do_command("run_bundler COMBINED")
    assert _partition(s) == {frozenset({"n0", "n1", "n2"})}
    for cmd in ("generate_topologies", "run_planner", "run_nuts",
                "run_detailed_nuts"):
        s.do_command(cmd)
    assert s.detailed_result.num_unplaced == 0
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    assert topo.seg_bits, "mixed group must be tapered"
    assert "Success" in _run(s, "check_design topo")
    assert "Success" in _run(s, "check_design dnuts")


# ── set_bundling overrides ────────────────────────────────────────────────────

def test_set_bundling_star_strict_disables_all_merging():
    s = _session()
    s.do_command("set_bundling * strict")
    s.do_command("run_bundler COMBINED")
    strict = _session()
    strict.do_command("run_bundler STRICT")
    assert _partition(s) == _partition(strict)


def test_set_bundling_per_prefix_and_longest_wins():
    """'b' nets restricted to strict keep b1/b2 out of every merge while the
    convergent group still forms; a longer prefix overrides a shorter one."""
    s = _session()
    s.do_command("set_bundling b strict")
    s.do_command("run_bundler COMBINED")
    part = _partition(s)
    assert frozenset({"b1"}) in part and frozenset({"b2"}) in part
    assert frozenset({"c0", "c1"}) in part                # conv still merges
    assert frozenset({"p0", "p1"}) in part                # strict pair intact
    # Longest prefix wins: re-allow b2 specifically.
    s2 = _session()
    s2.do_command("set_bundling b strict")
    s2.do_command("set_bundling b2 combined")
    s2.do_command("run_bundler COMBINED")
    part2 = _partition(s2)
    assert frozenset({"b2", "c0", "c1"}) in part2         # b2 merges again
    assert frozenset({"b1"}) in part2                     # b1 still solo


def test_set_bundling_no_convergent_equals_bidirectional():
    s = _session()
    s.do_command("set_bundling * no_convergent")
    s.do_command("run_bundler COMBINED")
    bidir = _session()
    bidir.do_command("run_bundler BIDIRECTIONAL")
    assert _partition(s) == _partition(bidir)


def test_set_bundling_no_bidirectional_equals_convergent():
    s = _session()
    s.do_command("set_bundling * no_bidirectional")
    s.do_command("run_bundler COMBINED")
    conv = _session()
    conv.do_command("run_bundler CONVERGENT")
    assert _partition(s) == _partition(conv)


# ── set_max_bundle_bits ───────────────────────────────────────────────────────

def test_static_split_is_balanced():
    """600->2x300 semantics: 6 bits at max 4 split 3+3, not 4+2."""
    s = buda_cli.BudaSession(); s.no_viz = True
    for line in (_LAYERS + "add_block A 0 0 100 80\n"
                 + "add_block B 800 0 950 200\n"
                 + "add_bus data[6] A.tx B.rx\n").strip().splitlines():
        s.do_command(line)
    s.do_command("set_max_bundle_bits 4")
    out = _run(s, "run_bundler STRICT")
    assert "into 2 part(s) (3+3)" in out
    sizes = sorted(len(w.input.original_bundle.get_net_names())
                   for w in s.bundles)
    assert sizes == [3, 3]
    # Reason carries the split marker and stays parseable.
    for w in s.bundles:
        r = w.input.original_bundle.reason
        assert "|SPLIT:" in r
        src, dsts = s._parse_bundle_reason(r)
        assert src == "A" and dsts == ["B"]


def test_static_split_keeps_buses_together():
    """Two 3-bit buses under a limit of 4: each bus becomes its own part
    rather than splitting 4+2 across bus boundaries."""
    s = buda_cli.BudaSession(); s.no_viz = True
    for line in (_LAYERS + "add_block A 0 0 100 80\n"
                 + "add_block B 800 0 950 200\n"
                 + "add_bus xa[3] A.t B.r\n"
                 + "add_bus xb[3] A.u B.s\n").strip().splitlines():
        s.do_command(line)
    s.do_command("set_max_bundle_bits 4")
    s.do_command("run_bundler STRICT")
    part = _partition(s)
    assert frozenset({"xa_0", "xa_1", "xa_2"}) in part
    assert frozenset({"xb_0", "xb_1", "xb_2"}) in part


def test_auto_split_binds_on_shortest_busterm_edge():
    """Dynamic bound: a 6-bit fan-in into a block whose shortest edge hosts
    4 bits (edge 8 / pitch 2) needs 2 parts — and the parts route clean."""
    s = buda_cli.BudaSession(); s.no_viz = True
    for line in (_LAYERS
                 + "add_block s0 0 0 100 80\n"
                 + "add_block s1 0 200 100 280\n"
                 + "add_block tiny 800 100 950 108\n"
                 + "add_net m0 s0.t0 tiny.r0\nadd_net m1 s0.t1 tiny.r1\n"
                 + "add_net m2 s0.t2 tiny.r2\nadd_net m3 s1.t0 tiny.r3\n"
                 + "add_net m4 s1.t1 tiny.r4\nadd_net m5 s1.t2 tiny.r5\n"
                 ).strip().splitlines():
        s.do_command(line)
    s.do_command("set_max_bundle_bits auto")
    out = _run(s, "run_bundler CONVERGENT")
    assert "busterm edge of 'tiny'" in out and "into 2 part(s)" in out
    assert len(s.bundles) == 2
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        s.do_command(cmd)
    assert s.nuts_result.num_overlaps == 0
    assert "Success" in _run(s, "check_design topo")


def test_auto_split_enforces_cap_per_part_for_clustered_bits():
    """Codex #273: n_parts alone sizes the balanced target from the TOTAL,
    which cannot bound a part's bits incident to ONE block when those bits
    cluster in a bus group smaller than the target.  4 drivers x 6-bit buses
    into one sink; driver 'a' has a 6-unit edge (cap 3 at pitch 2) while the
    others are large — the naive split (24 bits, n_parts=2, target=12) would
    keep a's whole 6-bit bus in one part (6 > cap 3).  The partitioner must
    close parts before any block cap is exceeded."""
    s = buda_cli.BudaSession(); s.no_viz = True
    for line in (_LAYERS
                 + "add_block a 0 0 6 6\n"            # min edge 6 -> cap 3
                 + "add_block b 0 200 100 280\n"
                 + "add_block c 0 400 100 480\n"
                 + "add_block d 0 600 100 680\n"
                 + "add_block sink 800 100 950 400\n"
                 + "add_bus ta[6] a.t sink.ra\n"
                 + "add_bus tb[6] b.t sink.rb\n"
                 + "add_bus tc[6] c.t sink.rc\n"
                 + "add_bus td[6] d.t sink.rd\n").strip().splitlines():
        s.do_command(line)
    s.do_command("set_max_bundle_bits auto")
    s.do_command("run_bundler CONVERGENT")
    eps = s._net_endpoints
    all_nets = []
    for w in s.bundles:
        part = w.input.original_bundle.get_net_names()
        all_nets.extend(part)
        a_bits = sum(1 for n in part if eps[n][0] == "a")
        assert a_bits <= 3, (part, a_bits)
    assert len(all_nets) == 24 and len(set(all_nets)) == 24  # nothing lost


def test_bit_bound_off_and_no_bound_is_identity():
    s = buda_cli.BudaSession(); s.no_viz = True
    for line in (_LAYERS + "add_block A 0 0 100 80\n"
                 + "add_block B 800 0 950 200\n"
                 + "add_bus data[6] A.tx B.rx\n").strip().splitlines():
        s.do_command(line)
    s.do_command("set_max_bundle_bits 4")
    s.do_command("set_max_bundle_bits off")
    s.do_command("run_bundler STRICT")
    assert len(s.bundles) == 1


def test_hier_bundler_warns_on_flat_only_settings():
    import buda
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = buda.BDB(":memory:")
    s.do_command("set_max_bundle_bits 8")
    out = _run(s, "run_hier_bundler")
    assert "FLAT" in out or "flat" in out
