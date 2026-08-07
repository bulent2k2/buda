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

"""Parallel hier candidate generation (chip_flow_parallelism.md P5).

`generate_hier_topologies` fans the per-bundle C++ generation calls across
a thread pool (`buda.generate_candidates_batch`, GIL released) when the
pool is real (>1 thread) and the design is big enough.  Per-bundle
generation is pure, so the batch must be DECISION-identical (same pools,
same uids, same order) and PRINT-identical (prep warnings and "[TopoGen]"
notes replayed in visit order) to the sequential loop.
"""

import contextlib
import io

import buda
import buda_cli
import buda_cmds.topologies_cmds as topo_cmds


def _gen_session(bdb_input, monkeypatch, threads):
    monkeypatch.setenv("BUDA_TOPO_THREADS", str(threads))
    monkeypatch.setattr(topo_cmds, "_BATCH_MIN_BUNDLES", 1)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for c in ["source flow/rnr/mix_tracks.buda",
                  f"open_bdb {bdb_input('hier_mixed')}",
                  "derive_busterms 1",
                  "run_hier_bundler depth 1",
                  "generate_hier_topologies"]:
            s.do_command(c)
    return s, out.getvalue()


def _pools(s):
    return {w.input.original_bundle.id:
            [buda.topo_uid(t) for t in w.input.candidates]
            for w in s.bundles}


def test_batch_generation_matches_sequential(bdb_input, monkeypatch):
    s_par, out_par = _gen_session(bdb_input, monkeypatch, 2)
    s_seq, out_seq = _gen_session(bdb_input, monkeypatch, 1)
    assert len(s_par.bundles) >= 2
    # Same pools, same candidate identities, same order — per bundle.
    assert _pools(s_par) == _pools(s_seq)
    # Same printed lines (the HierTopo report lines and any notes).
    def content(out):
        return [ln for ln in out.splitlines()
                if not ln.lstrip().startswith("[runtime]")]
    assert content(out_par) == content(out_seq)


def test_batch_engine_returns_pools_and_notes_in_order(bdb_input,
                                                       monkeypatch):
    # Direct engine check: N tasks come back in input order with a notes
    # string per task (empty when the generator printed nothing).
    s, _out = _gen_session(bdb_input, monkeypatch, 1)
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 200)
    fp.add_block("B", 500, 600, 700, 900)
    fp.add_block("C", 1200, 0, 1400, 300)
    gens, srcs, dsts = [], [], []
    for src, dst in (("A", "B"), ("B", "C"), ("A", "C")):
        gens.append(s._make_topo_gen(fp))
        srcs.append(src)
        dsts.append([dst])
    res = buda.generate_candidates_batch(gens, srcs, dsts, 2)
    assert len(res) == 3
    for pool, notes in res:
        assert pool and isinstance(notes, str)
    # Task order preserved: each pool routes its own pair.
    assert "A" in res[0][0][0].connected_block_names
    assert "C" in res[1][0][0].connected_block_names
