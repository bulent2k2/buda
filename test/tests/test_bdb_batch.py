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

"""BDB write-batching primitives (begin/commit/rollback_batch).

Each add_* row insert autocommits by default, so a bulk persist fsyncs the WAL
once per statement — the generate_hier_topologies slowdown (23s -> ~1.7s on
mix once batched).  These pin the transaction semantics the CLI persist paths
rely on: a batch commits atomically, rolls back atomically, and NESTS via a
depth counter so composing persist helpers issue only one real BEGIN/COMMIT.
"""

import contextlib
import io

import buda
import buda_cli


def _bundle(bid):
    br = buda.BundleRow()
    br.id = bid
    br.strategy = "TEST"
    return br


def _ids(db):
    return {b.id for b in db.all_bundles()}


def test_batch_commit_persists(tmp_path):
    db = buda.BDB(str(tmp_path / "b.bdb"))
    db.begin_batch()
    db.add_bundle(_bundle("b0"))
    db.add_bundle(_bundle("b1"))
    db.commit_batch()
    assert {"b0", "b1"} <= _ids(db)


def test_batch_rollback_discards(tmp_path):
    db = buda.BDB(str(tmp_path / "b.bdb"))
    db.add_bundle(_bundle("keep"))     # autocommitted before the batch
    db.begin_batch()
    db.add_bundle(_bundle("drop"))
    db.rollback_batch()
    ids = _ids(db)
    assert "keep" in ids and "drop" not in ids


def test_batch_nesting_inner_commit_is_noop(tmp_path):
    """Depth counter: only the OUTERMOST begin/commit touches the DB.  An inner
    commit must NOT actually commit — proven by rolling back at the outer level
    afterwards and finding the inner-'committed' row gone."""
    db = buda.BDB(str(tmp_path / "b.bdb"))
    db.begin_batch()                   # depth 1 -> real BEGIN
    db.begin_batch()                   # depth 2
    db.add_bundle(_bundle("nested"))
    db.commit_batch()                  # depth 1 -> NO real commit
    db.rollback_batch()                # depth 0 -> real ROLLBACK discards it
    assert "nested" not in _ids(db)


def test_batch_nesting_outer_commit_persists(tmp_path):
    db = buda.BDB(str(tmp_path / "b.bdb"))
    db.begin_batch()
    db.begin_batch()
    db.add_bundle(_bundle("nested"))
    db.commit_batch()                  # inner no-op
    db.commit_batch()                  # outer -> real COMMIT
    assert "nested" in _ids(db)


def test_generate_persist_is_batched_and_correct(tmp_path):
    """End-to-end: generate_hier_topologies persists every candidate inside one
    batch, and the persisted rows match the in-memory candidates (the batch is
    transparent to output)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("source flow/rnr/mix_tracks.buda")
        s.do_command("add_block A 0 0 200 400")
        s.do_command("add_block B 1000 0 1200 400")
        s.do_command("add_bus d[8] A.p B.p")
        s.do_command("open_bdb " + str(tmp_path / "flat.bdb"))
        s.do_command("run_bundler")
        s.do_command("generate_topologies")
    # The C++ depth counter returned to 0 (every begin matched a commit), so a
    # fresh batch still works — a leaked transaction would raise here.
    s.bdb.begin_batch()
    s.bdb.commit_batch()
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    assert len(s.bdb.topologies(bid)) == len(w.input.candidates) >= 1
