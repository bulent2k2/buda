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

"""A stale sidecar overriding a durable checkpoint pin on a REBUILD announces itself.

USER_GUIDE section 3.3: when a flow both has a checkpoint BDB and a `.json`
beside it, a rebuild applies the sidecar first and `_apply_bdb_pins` then skips
the already-pinned wrapper, so the checkpoint's durable pin is silently passed
over.  That was the one interaction in the section that produced NO output.
`_apply_selections` now warns when the sidecar pins an UNPINNED wrapper on a
rebuild and the open BDB holds a durable pin pointing at a DIFFERENT candidate.

The notice is scoped so it fires for exactly that case and nothing else:
  - silent when the sidecar AGREES with the checkpoint (same candidate);
  - silent with no checkpoint (the sidecar is the only persistence);
  - silent for a SCRIPT `select_topology` overriding a checkpoint pin, which is
    documented and intended (a pin made in this session wins);
  - silent on a RESUME (`load_pipeline`), where the restored pin is already on
    the wrapper so the sidecar does not adopt.
"""
import contextlib
import io
import json

import pytest

# Full-pipeline BDB round-trip: mid tier, like test_bdb_resume.
pytestmark = pytest.mark.mid

import buda_cli

_MARK = "OVERRIDES a durable checkpoint pin"

SETUP = ("source flow/tracks/tracks.buda", "corner_margin dx 5 dy 5",
         "add_block cpu 50 50 250 250", "add_block mem0 550 50 750 250")
NETS = ("add_bus d1[4] cpu.o mem0.i",)


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


def _build_checkpoint(db, pin_cmd):
    """Route the design once with pin_cmd, persisting into db (no sidecar)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *SETUP, f"open_bdb {db}", *NETS,
           "run_bundler strict", "generate_topologies",
           pin_cmd, "run_planner 2", "run_nuts")
    return s


def _rebuild(tmp_path, db, sidecar, *extra):
    """Fresh session over the same db WITH a sidecar; return captured stdout."""
    script = tmp_path / "r.buda"
    script.write_text("")
    if sidecar is not None:
        (tmp_path / "r.json").write_text(json.dumps(sidecar))
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(script)           # _sidecar_path() -> r.json
    out = _quiet(s, *SETUP, f"open_bdb {db}", *NETS,
                 "run_bundler strict", "generate_topologies", *extra)
    return s, out


def _sidecar(hint_idx):
    # type/wl deliberately won't match -> resolved by topo_index_hint.
    return {"selections": [{
        "bundle_hint": "d1_0", "bundle_id": "1",
        "topo_type": "_unused_", "topo_wl": -1, "topo_index_hint": hint_idx,
        "note": "", "selected_at": "x",
    }]}


def _enough_candidates(s):
    return bool(s.bundles) and len(s.bundles[0].input.candidates) >= 4


def test_divergent_sidecar_on_rebuild_warns(tmp_path):
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 4")   # checkpoint pins cand 4
    if not _enough_candidates(s1):
        pytest.skip("topology generation differs from expected fixture")
    s2, out = _rebuild(tmp_path, db, _sidecar(0))         # sidecar names cand 1
    assert _MARK in out, out
    # And the sidecar actually won (candidate 1 = index 0).
    assert s2.bundles[0].plan.selected_topology_index == 0


def test_agreeing_sidecar_on_rebuild_is_silent(tmp_path):
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 4")
    if not _enough_candidates(s1):
        pytest.skip("topology generation differs from expected fixture")
    _s2, out = _rebuild(tmp_path, db, _sidecar(3))        # cand 4 == index 3
    assert _MARK not in out, out


def test_no_checkpoint_is_silent(tmp_path):
    # A flow that opens no BDB: the sidecar is the only persistence, nothing
    # is being overridden.
    script = tmp_path / "r.buda"
    script.write_text("")
    (tmp_path / "r.json").write_text(json.dumps(_sidecar(0)))
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(script)
    out = _quiet(s, *SETUP, *NETS,
                 "run_bundler strict", "generate_topologies")
    if not _enough_candidates(s):
        pytest.skip("topology generation differs from expected fixture")
    assert _MARK not in out, out


def test_script_pin_overriding_checkpoint_is_silent(tmp_path):
    # A script select_topology to a different candidate is the documented,
    # intended override — not the stale-sidecar hazard — so it stays quiet.
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 4")
    if not _enough_candidates(s1):
        pytest.skip("topology generation differs from expected fixture")
    _s2, out = _rebuild(tmp_path, db, None, "select_topology d1 1")
    assert _MARK not in out, out


def test_divergent_sidecar_over_group_pin_warns(tmp_path):
    # A durable GROUP (super-candidate) pin is invisible to the single-pin
    # rows, so it too was silently overridden (Codex #861).  Candidate 4 in
    # this fixture carries a 2-member family; pin the family, then override
    # with a sidecar single pin OUTSIDE it.
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 group:4")
    if not _enough_candidates(s1) or not s1.bdb.meta_get("pinned_group:1", ""):
        pytest.skip("grouped generation differs from expected fixture")
    s2, out = _rebuild(tmp_path, db, _sidecar(0))          # cand 1, not in family
    assert "OVERRIDES a durable checkpoint GROUP pin" in out, out
    assert s2.bundles[0].plan.selected_topology_index == 0


def test_sidecar_within_group_family_is_silent(tmp_path):
    # Choosing a candidate that IS a family member is not an override.
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 group:4")
    if not _enough_candidates(s1) or not s1.bdb.meta_get("pinned_group:1", ""):
        pytest.skip("grouped generation differs from expected fixture")
    _s2, out = _rebuild(tmp_path, db, _sidecar(3))         # cand 4 == a member
    assert _MARK not in out, out


def test_design_changed_drop_reports_missing_candidate_not_override(tmp_path):
    # When the durable pin's candidate is no longer generated (the design
    # changed), deleting the sidecar cannot restore it — so the notice must
    # be the missing-candidate diagnostic, not the "delete the sidecar"
    # remedy (Codex #861).  The rebuild MOVES mem0, so the pinned candidate's
    # uid drops out of the regenerated pool.
    db = tmp_path / "ck.bdb"
    s1 = _build_checkpoint(db, "select_topology d1 4")
    if not _enough_candidates(s1):
        pytest.skip("topology generation differs from expected fixture")
    script = tmp_path / "r.buda"
    script.write_text("")
    (tmp_path / "r.json").write_text(json.dumps(_sidecar(0)))
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    s2.script_path = str(script)
    moved = ("source flow/tracks/tracks.buda", "corner_margin dx 5 dy 5",
             "add_block cpu 50 50 250 250", "add_block mem0 550 350 750 550")
    out = _quiet(s2, *moved, f"open_bdb {db}", *NETS,
                 "run_bundler strict", "generate_topologies")
    assert "matches no regenerated candidate" in out, out
    assert "dropped (design changed)" in out, out
    assert _MARK not in out, out           # not the "keep the checkpoint's choice" remedy
