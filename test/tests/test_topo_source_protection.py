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

"""Phases E2b/E4 — candidate provenance and user-candidate protection
(schema v15: topology.source + bundle.gen_knobs).

Bulk regeneration can never delete a hand-committed candidate:

  * in-session — USER candidates survive `generate_topologies` in the pool
    (re-appended uid-deduped, pin re-attachable);
  * cross-session — `clear_topologies(keep_user)` spares source='user' rows a
    session that never loaded them would otherwise wipe; orphans renumber to
    the tail and `load_pipeline` restores them;
  * knob memo — `generate_more_topologies` records its knobs per bundle, and a
    later bulk `generate_topologies` re-applies them additively, so an
    accreted pool does not silently revert.
"""
import contextlib
import io

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _out(session, *cmds):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


_SETUP = (
    "source flow/rnr/mix_tracks.buda",
    "add_block A 0 0 200 400",
    "add_block B 600 100 800 500",
)


def _session(bdb_path=None):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ([f"open_bdb {bdb_path}"] if bdb_path else []) + list(_SETUP) + [
        "add_bus d[8] A.p B.q", "run_bundler", "generate_topologies"]
    _quiet(s, *cmds)
    return s


def _commit_user(s, bid):
    _quiet(s, f"edit_topology {bid} new",
           "edit_add_trunk V 450", "edit_add_stub A 0", "edit_add_stub B 0",
           "edit_commit pin")


def test_user_candidate_survives_bulk_regeneration_in_session():
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    _commit_user(s, bid)
    user_uid = buda.topo_uid(w.input.candidates[-1])

    out = _out(s, "generate_topologies")
    assert "Kept 1 user candidate(s)" in out, out
    assert "Pin re-attached by topo_uid" in out, out
    uids = [buda.topo_uid(c) for c in w.input.candidates]
    assert user_uid in uids
    sel = w.plan.selected_topology_index
    assert w.input.topology_pinned and buda.topo_uid(
        w.input.candidates[sel]) == user_uid


def test_user_source_persisted(tmp_path):
    s = _session(str(tmp_path / "p.bdb"))
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    _commit_user(s, bid)
    rows = s.bdb.topologies(str(bid))
    by_src = {}
    for r in rows:
        by_src.setdefault(r.source, 0)
        by_src[r.source] += 1
    assert by_src.get("user") == 1, by_src
    assert by_src.get("generated", 0) == len(rows) - 1


def test_cross_session_repersist_keeps_user_row(tmp_path):
    """Session 2 re-persists WITHOUT load_pipeline: the keep_user wipe must
    spare the user row (renumbered to the tail), and session 3's
    load_pipeline restores the candidate."""
    path = str(tmp_path / "x.bdb")
    s1 = _session(path)
    bid = s1.bundles[0].input.original_bundle.id
    _commit_user(s1, bid)
    user_uid = buda.topo_uid(s1.bundles[0].input.candidates[-1])
    del s1

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    out = _out(s2, f"open_bdb {path}", *_SETUP,
               "add_bus d[8] A.p B.q", "run_bundler", "generate_topologies")
    assert "kept user candidate" in out, out
    rows = s2.bdb.topologies(str(bid))
    user_rows = [r for r in rows if r.source == "user"]
    assert len(user_rows) == 1
    assert user_rows[0].topo_uid == user_uid
    # Renumbered past the fresh block: index unique and >= generated count.
    gen_n = sum(1 for r in rows if r.source != "user")
    assert user_rows[0].cand_index >= gen_n
    del s2

    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    _quiet(s3, f"open_bdb {path}", *_SETUP, "load_pipeline")
    uids = [buda.topo_uid(c) for c in s3.bundles[0].input.candidates]
    assert user_uid in uids, "load_pipeline did not restore the kept user candidate"


def test_gen_knob_memo_survives_bulk_regeneration(tmp_path):
    """generate_more's knobs persist per bundle; a later bulk
    generate_topologies re-applies them additively."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [f"open_bdb {tmp_path / 'k.bdb'}",
            "def_layer 4 M4 H TOP 20", "def_layer 5 M5 V TOP 20"]
    for i in range(6):
        x, y = (i % 3) * 400, (i // 3) * 600
        cmds.append(f"add_block b{i} {x} {y} {x + 200} {y + 300}")
    cmds += ["add_bus d[8] b0.p b1.q,b2.r,b3.s,b4.t,b5.u",
             "run_bundler", "generate_topologies"]
    _quiet(s, *cmds)
    w = s.bundles[0]
    base_n = len(w.input.candidates)

    _quiet(s, "generate_more_topologies d multi_trunk")
    accreted = [buda.topo_uid(c) for c in w.input.candidates]
    assert len(accreted) > base_n
    assert s.bdb.bundle_gen_knobs(str(w.input.original_bundle.id)) == "multi_trunk"

    out = _out(s, "generate_topologies")            # plain bulk regen
    assert "Re-applied knob memo 'multi_trunk'" in out, out
    assert sorted(buda.topo_uid(c) for c in w.input.candidates) == sorted(accreted), \
        "the accreted pool silently reverted"
