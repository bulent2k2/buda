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

"""BIDIRECTIONAL bundling in the HIERARCHICAL pipeline (`run_hier_bundler`).

Mirrors the hbundles/02_two_procs shape (a src → proc(pa,pb,pc) → snk lane) but
mixes bidirectional and unidirectional nets:

  * unidirectional pipeline buses  : src→pa, pc→snk
  * a bidirectional pair inside proc: pa→pb (fwd) AND pb→pa (rev)
  * an EXTRA one-way pa→pb          : so one bundle mixes both directions

With `run_hier_bundler … bidirectional` the direction-agnostic signature (sorted
set of all endpoint names) puts the forward bus, its reverse, AND the extra
one-way net into a SINGLE bundle, while the two plain pipeline buses stay as
separate unidirectional bundles.  Routing is block-to-block and
direction-agnostic, so the whole pipeline runs clean.
"""

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test/runtime_analysis.md.
pytestmark = pytest.mark.mid

import buda
import buda_cli


def _build_mixed_bdb(path):
    """Build the mixed-net hierarchical BDB at `path` and close it."""
    db = buda.BDB(path)
    for cell, w, h in [("proc_cell", 420, 200), ("pipe_cell", 110, 80),
                       ("src_cell", 200, 200), ("snk_cell", 200, 200),
                       ("gen_cell", 80, 80), ("rcv_cell", 80, 80)]:
        db.add_cell(cell, w, h)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst_to_cell("proc_cell", "pc_i", "pipe_cell", 290, 60)
    db.add_inst_to_cell("src_cell", "gen_i", "gen_cell", 60, 60)
    db.add_inst_to_cell("snk_cell", "rcv_i", "rcv_cell", 60, 60)
    db.add_inst("src_a", "src_cell", "", 50, 50)
    db.add_inst("proc_a", "proc_cell", "", 350, 50)
    db.add_inst("snk_a", "snk_cell", "", 870, 50)
    for i in range(4):
        db.add_net_pins(f"s2p_{i}", "src_a/gen_i.out", ["proc_a/pa_i.in"])
        db.add_net_pins(f"p2s_{i}", "proc_a/pc_i.out", ["snk_a/rcv_i.in"])
        db.add_net_pins(f"ab_fwd_{i}", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
        db.add_net_pins(f"ab_rev_{i}", "proc_a/pb_i.out", ["proc_a/pa_i.in"])
    db.add_net_pins("ab_extra", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
    buda.BustermGen(db).derive(1)
    db.compute_all()
    # db is a local — closed on return so the session can reopen the file.


_FWD = {f"ab_fwd_{i}" for i in range(4)}
_REV = {f"ab_rev_{i}" for i in range(4)}
_S2P = {f"s2p_{i}" for i in range(4)}
_P2S = {f"p2s_{i}" for i in range(4)}


def _session(path, strategy):
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for cmd in (f"open_bdb {path}",
                "def_layer 4 M4 H TOP 44.44", "def_layer 5 M5 V TOP 50.00",
                "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                f"run_hier_bundler depth 1 {strategy}"):
        sess.do_command(cmd)
    return sess


def _netsets(sess):
    return [set(w.input.original_bundle.get_net_names()) for w in sess.bundles]


def test_hier_strict_keeps_forward_and_reverse_separate(tmp_path):
    # Baseline: STRICT groups by driver, so pa→pb and pb→pa are different bundles.
    _build_mixed_bdb(str(tmp_path / "mix.bdb"))
    sess = _session(str(tmp_path / "mix.bdb"), "strict")
    sets = _netsets(sess)
    assert (_FWD | {"ab_extra"}) in sets   # forwards + extra one-way together
    assert _REV in sets                    # reverses separate
    assert _S2P in sets and _P2S in sets


def test_hier_bidirectional_mixes_directions_in_one_bundle(tmp_path):
    # BIDIRECTIONAL merges the forward bus, its reverse, and the extra one-way net
    # into ONE bundle (a bundle with both bidirectional and unidirectional nets),
    # while the two plain pipeline buses stay as separate unidirectional bundles.
    _build_mixed_bdb(str(tmp_path / "mix.bdb"))
    sess = _session(str(tmp_path / "mix.bdb"), "bidirectional")
    sets = _netsets(sess)

    mixed = _FWD | _REV | {"ab_extra"}
    assert mixed in sets, sets                 # the mixed bundle
    assert _S2P in sets and _P2S in sets       # plain unidirectional bundles
    # The mixed bundle's reason is the direction-agnostic BIDIR set of pa_i, pb_i.
    mixed_bundle = next(w.input.original_bundle for w in sess.bundles
                        if set(w.input.original_bundle.get_net_names()) == mixed)
    assert mixed_bundle.reason.startswith("BIDIR:")
    assert "pa_i" in mixed_bundle.reason and "pb_i" in mixed_bundle.reason


def test_hier_bidirectional_pipeline_runs_end_to_end(tmp_path):
    # The whole hier pipeline routes with the mixed bidirectional bundle present.
    _build_mixed_bdb(str(tmp_path / "mix.bdb"))
    sess = _session(str(tmp_path / "mix.bdb"), "bidirectional")
    for cmd in ("generate_hier_topologies", "run_planner hier", "run_nuts"):
        sess.do_command(cmd)
    assert sess.nuts_result is not None
    assert len(sess.nuts_result.segments) > 0
    assert sess.nuts_result.num_overlaps == 0


def test_hier_run_bundler_rejects_bad_strategy(tmp_path, capsys):
    _build_mixed_bdb(str(tmp_path / "mix.bdb"))
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"open_bdb {tmp_path / 'mix.bdb'}")
    capsys.readouterr()
    # An unknown strategy is a hard error that STOPS the flow (sys.exit), per
    # the PR #467 unknown-option guard — not a print-and-continue.
    with pytest.raises(SystemExit):
        sess.do_command("run_hier_bundler depth 1 SOMETHING")
    assert "unknown option" in capsys.readouterr().out.lower()
