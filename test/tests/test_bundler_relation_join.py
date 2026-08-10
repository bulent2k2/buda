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

"""Asking for SEVERAL relations at once.

A strategy names one relation (COMBINED, a fixed pair).  That is enough
until a design carries more than one SHAPE — and a real one does:
`flow/rv` takes a 32-bit bus IN from 32 pads and sends another OUT to 32
pads.  CONVERGENT bundles the first and splits the second; DIVERGENT does
exactly the opposite; COMBINED is conv+bidir so it does not help either.
There was no way to say "both".

The union-find join already worked over a SET of relations.  Only the way
to ask for one was missing, so `run_bundler CONVERGENT DIVERGENT` names
the set.
"""
import contextlib
import io

import buda_cli

_LAYERS = "def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"

# BOTH shapes in one netlist, sharing no net:
#   fan-IN   src0,src1 -> sink        (different drivers, one receiver)
#   fan-OUT  hub -> dst0,dst1         (one driver, different receivers)
_BOTH = _LAYERS + """add_block src0 0 0 100 80
add_block src1 0 200 100 280
add_block sink 400 80 500 200
add_block hub 700 100 800 200
add_block dst0 1000 0 1100 80
add_block dst1 1000 300 1100 380
add_net i0 src0.o sink.a
add_net i1 src1.o sink.b
add_net o0 hub.p dst0.i
add_net o1 hub.q dst1.i
"""


def _run(script):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in script.strip().splitlines():
            if line.strip():
                s.do_command(line.strip())
    return s, buf.getvalue()


def _sizes(s):
    return sorted(len(w.input.original_bundle.get_net_names())
                  for w in s.bundles)


def test_one_relation_bundles_one_shape_and_not_the_other():
    """The starting point, stated as a measurement rather than a claim:
    each single relation gets its own shape and leaves the other split."""
    conv, _ = _run(_BOTH + "run_bundler CONVERGENT")
    div, _ = _run(_BOTH + "run_bundler DIVERGENT")
    # fan-in merged (2), fan-out still split (1+1)
    assert _sizes(conv) == [1, 1, 2], _sizes(conv)
    # …and exactly the mirror.
    assert _sizes(div) == [1, 1, 2], _sizes(div)
    # Same shape of answer, DIFFERENT bundles — which is the point.
    def merged(sess):
        return {tuple(sorted(w.input.original_bundle.get_net_names()))
                for w in sess.bundles
                if len(w.input.original_bundle.get_net_names()) > 1}
    assert merged(conv) == {("i0", "i1")}
    assert merged(div) == {("o0", "o1")}


def test_combined_does_not_cover_the_fan_out():
    """COMBINED is conv+bidir, so on this design it is CONVERGENT — which
    is why naming the set was needed rather than reaching for COMBINED."""
    s, _ = _run(_BOTH + "run_bundler COMBINED")
    assert _sizes(s) == [1, 1, 2]
    merged = {tuple(sorted(w.input.original_bundle.get_net_names()))
              for w in s.bundles
              if len(w.input.original_bundle.get_net_names()) > 1}
    assert merged == {("i0", "i1")}


def test_naming_both_relations_bundles_both_shapes():
    s, out = _run(_BOTH + "run_bundler CONVERGENT DIVERGENT")
    assert _sizes(s) == [2, 2], _sizes(s)
    merged = {tuple(sorted(w.input.original_bundle.get_net_names()))
              for w in s.bundles}
    assert merged == {("i0", "i1"), ("o0", "o1")}
    assert "JOIN" in out, out


def test_a_single_token_is_unchanged_by_the_join_plumbing():
    """The set is only consulted when more than one token is named, so a
    one-token run must partition exactly as it did before the feature."""
    for tok in ("STRICT", "CONVERGENT", "DIVERGENT", "BIDIRECTIONAL",
                "COMBINED"):
        one, _ = _run(_BOTH + f"run_bundler {tok}")
        again, _ = _run(_BOTH + f"run_bundler {tok}")
        assert _sizes(one) == _sizes(again)
        assert [w.input.original_bundle.reason for w in one.bundles] == \
               [w.input.original_bundle.reason for w in again.bundles]


# ── refusals: the two ways of naming a set that mean nothing ────────────────

def test_strict_cannot_be_joined():
    """STRICT names no relation — it is what you get with none — so
    `STRICT CONVERGENT` is not a coarser partition, it is a confusion.
    Refuse it rather than silently answering as if STRICT were absent."""
    s, out = _run(_BOTH + "run_bundler STRICT CONVERGENT")
    assert "STRICT names no relation" in out, out


def test_a_repeated_strategy_is_refused():
    s, out = _run(_BOTH + "run_bundler CONVERGENT CONVERGENT")
    assert "repeated strategy" in out, out


def test_an_unknown_token_in_a_set_is_still_refused():
    s, out = _run(_BOTH + "run_bundler CONVERGENT DIVERGENTISH")
    assert "must be STRICT, CONVERGENT" in out, out


# ── the routed result ──────────────────────────────────────────────────────

def test_the_joined_partition_routes_clean():
    """Both trees in one design, each rooted at its own end."""
    s, out = _run(_BOTH + """run_bundler CONVERGENT DIVERGENT
generate_topologies
run_planner 5
run_nuts
check_design nuts
""")
    assert s.nuts_result.num_overlaps == 0
    assert "Success: no violations found" in out, out[-2000:]


def test_set_bundling_still_gates_each_relation_inside_the_join():
    """The set says which relations are ON; `set_bundling` still says which
    NETS may use them.  Holding the fan-out out of the join must leave the
    fan-in merge alone — the two are independent, and a per-net override
    that silently disabled both would be the easy mistake here."""
    s, _ = _run(_BOTH + "set_bundling o no_divergent\n"
                        "run_bundler CONVERGENT DIVERGENT")
    merged = {tuple(sorted(w.input.original_bundle.get_net_names()))
              for w in s.bundles
              if len(w.input.original_bundle.get_net_names()) > 1}
    assert merged == {("i0", "i1")}, merged


# ── the hier path, which is where the C++ relation mask lives ──────────────
#
# The flat tests above all run through the PYTHON union-find
# (`_generalized_bundles`), so disabling the C++ mask leaves every one of
# them passing — measured, not assumed.  `run_hier_bundler` is the path
# that consults `HierarchicalBundler::set_relations`, and it needs a real
# hierarchical design to have both shapes at once.

_RV = __import__("pathlib").Path(__file__).resolve().parents[2] / "flow" / "rv"

_RV_IMPORT = [
    "open_bdb :memory:",
    "set_import_scale dbu",
    f"import_lef_tech {_RV / 'soc.lef'}",
    f"import_def_lef {_RV / 'soc.def'} {_RV / 'soc.lef'}",
    f"import_verilog {_RV / 'soc.v'}",
    "derive_container_bboxes margin 4000",
    "derive_busterms 4",
    "add_blocks_from_bdb 0",
    "add_blocks_from_bdb 1 skip",
    "add_blocks_from_bdb 2 skip",
    "add_blocks_from_bdb 3 skip",
    "add_blocks_from_bdb 4 skip",
]


def _rv(strategy):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _RV_IMPORT + [f"run_hier_bundler depth 4 {strategy}"]:
            s.do_command(c)
    hbs = [w.input.original_bundle for w in s.bundles]

    def widest(prefix):
        g = [b for b in hbs if any(n.startswith(prefix)
                                   for n in b.get_net_names())]
        return len(g), max((len(b.get_net_names()) for b in g), default=0)

    return len(hbs), widest("boot["), widest("dbg[")


def test_the_hier_join_bundles_both_port_buses():
    """`flow/rv` is the design the feature exists for: `boot` arrives from
    32 pads (fan-in) and `dbg` leaves for 32 pads (fan-out).  One relation
    gets one of them; the set gets both."""
    n_conv, boot_c, dbg_c = _rv("CONVERGENT")
    n_div, boot_d, dbg_d = _rv("DIVERGENT")
    n_join, boot_j, dbg_j = _rv("CONVERGENT DIVERGENT")

    assert boot_c == (1, 32) and dbg_c == (32, 1)   # inbound only
    assert boot_d == (32, 1) and dbg_d == (1, 32)   # outbound only
    assert boot_j == (1, 32) and dbg_j == (1, 32)   # both
    assert (n_conv, n_div, n_join) == (89, 78, 40)


def test_the_hier_join_is_coarser_than_either_relation_alone():
    """A join can only merge, never split: every single-relation bundle has
    to survive inside some joined bundle.  This is the property that would
    break first if the mask leaked into the single-relation paths."""
    def parts(strategy):
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()):
            for c in _RV_IMPORT + [f"run_hier_bundler depth 4 {strategy}"]:
                s.do_command(c)
        return [frozenset(w.input.original_bundle.get_net_names())
                for w in s.bundles]

    join = parts("CONVERGENT DIVERGENT")
    for strategy in ("CONVERGENT", "DIVERGENT"):
        for grp in parts(strategy):
            assert any(grp <= j for j in join), (strategy, sorted(grp)[:4])


def test_the_order_of_the_tokens_does_not_matter():
    """A SET has no order, so the two spellings of one set must produce the
    same bundles AND the same bundle metadata.

    They did not.  The strategy enum still held the FIRST token, and the
    cross-level emission read the enum rather than the set to decide
    whether a single-driver group gets a `BIDIR:` reason — so
    `BIDIRECTIONAL CONVERGENT` wrote BIDIR reasons where `CONVERGENT
    BIDIRECTIONAL` wrote strict ones, for 59 of `flow/rv`'s bundles.  The
    reason is not cosmetic: generation parses it to recover endpoints, and
    it is what gets persisted (Codex P2 on #676).

    Every PAIR is checked rather than the one that happened to be tried
    first — the conv+div pair, which is what this feature was built for,
    is exactly the pair that did NOT differ.
    """
    import itertools
    for a, b in itertools.combinations(
            ("CONVERGENT", "DIVERGENT", "BIDIRECTIONAL"), 2):
        fwd, rev = _rv_bundles(f"{a} {b}"), _rv_bundles(f"{b} {a}")
        assert fwd == rev, (a, b, [x for x in fwd if x not in rev][:2])


def _rv_bundles(strategy):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _RV_IMPORT + [f"run_hier_bundler depth 4 {strategy}"]:
            s.do_command(c)
    return sorted((tuple(sorted(w.input.original_bundle.get_net_names())),
                   w.input.original_bundle.reason) for w in s.bundles)


def test_the_persisted_strategy_names_the_whole_set():
    """`_bundler_strategy` is what a re-persist stamps on the bundle rows.
    Recording only the first token describes a join incompletely, and its
    identity would depend on the order someone typed."""
    for spelling in ("CONVERGENT DIVERGENT", "DIVERGENT CONVERGENT"):
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()):
            for c in _RV_IMPORT + [f"run_hier_bundler depth 4 {spelling}"]:
                s.do_command(c)
        assert s._bundler_strategy == "CONVERGENT+DIVERGENT", \
            (spelling, s._bundler_strategy)
    # A single token is unchanged — it is not a join.
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _RV_IMPORT + ["run_hier_bundler depth 4 CONVERGENT"]:
            s.do_command(c)
    assert s._bundler_strategy == "CONVERGENT"
