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

"""Super-candidate group pin PERSISTENCE — BDB resume + explorer sidecar.

A group pin (`select_topology <b> group:<N>` / the explorer's 'S') restricts the
planner to a nominal-locus family so it refines WHICH member wins.  For the pin
to be useful across a checkpoint it has to round-trip:

  * BDB    — persisted as meta `pinned_group:<bid>` = JSON list of the family
             members' `topo_uid`s (uid-keyed so a re-ordered candidate pool still
             resolves), restored by `_restore_wrapper` on `load_pipeline`.
  * Sidecar — the explorer writes the family's `group_uids` into the JSON entry
             (`_group_select_current`), restored by `_apply_selections` on the
             next `generate_topologies` in a session with the same script path.

Both paths are uid-keyed and both re-run the planner within the pinned family.
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # headless; no window
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda  # noqa: E402
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

# The same 3-block fan-out fixture as test_supercandidate.py: several Hanan-line
# trunk loci -> a candidate pool with genuine nominal-locus families.
_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_bus dbus[52] A.p B.p,C.p",
]
_BUNDLE = ["run_bundler", "generate_topologies"]


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


def _fresh(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *cmds)
    return s


def _family(s, w):
    fp = s._make_topo_fp_resolver()(w)
    return next(g for g in s._loci_groups(w, fp) if len(g) > 1)


# ---------------------------------------------------------------------------
# BDB resume round-trip
# ---------------------------------------------------------------------------

@pytest.mark.mid
def test_group_pin_survives_bdb_resume(tmp_path):
    # Pin a family before run_planner, checkpoint to the BDB, then reopen in a
    # FRESH session: load_pipeline must restore pinned_group and the resumed
    # planner must refine WITHIN the family (never leave it).
    db = str(tmp_path / "grp.bdb")
    s1 = _fresh(*_SETUP, f"open_bdb {db}", *_BUNDLE)
    w1 = s1.bundles[0]
    fam = _family(s1, w1)
    _quiet(s1, f"select_topology 1 group:{fam[0] + 1}")
    assert list(w1.input.pinned_group) == fam
    # topo_uids of the pinned family — the identity that must survive the reload.
    fam_uids = {buda.topo_uid(w1.input.candidates[i]) for i in fam}
    del s1

    s2 = _fresh(*_SETUP, f"open_bdb {db}", "load_pipeline")
    w2 = s2.bundles[0]
    assert w2.input.pinned_group, "group pin restored from BDB meta"
    restored_uids = {buda.topo_uid(w2.input.candidates[i])
                     for i in w2.input.pinned_group}
    assert restored_uids == fam_uids                     # same family, uid-keyed

    _quiet(s2, "run_planner 3")
    sel = w2.plan.selected_topology_index
    sel_uid = buda.topo_uid(w2.input.candidates[sel])
    assert sel_uid in fam_uids                            # planner stayed in family


@pytest.mark.mid
def test_no_group_pin_persists_nothing(tmp_path):
    # An UNPINNED bundle must not leave a stale group pin behind: the meta value
    # is written empty, so a resumed session restores no pinned_group.
    db = str(tmp_path / "nogrp.bdb")
    s1 = _fresh(*_SETUP, f"open_bdb {db}", *_BUNDLE)
    assert list(s1.bundles[0].input.pinned_group) == []
    del s1

    s2 = _fresh(*_SETUP, f"open_bdb {db}", "load_pipeline")
    assert list(s2.bundles[0].input.pinned_group) == []


@pytest.mark.mid
def test_group_pin_cleared_then_repersisted(tmp_path):
    # Pin a family, checkpoint, then UNPIN and re-persist: the stale meta must be
    # overwritten (empty), so a later resume restores nothing.
    db = str(tmp_path / "clear.bdb")
    s1 = _fresh(*_SETUP, f"open_bdb {db}", *_BUNDLE)
    fam = _family(s1, s1.bundles[0])
    _quiet(s1, f"select_topology 1 group:{fam[0] + 1}")
    assert list(s1.bundles[0].input.pinned_group) == fam
    _quiet(s1, "unpin_topology *")                       # clears + re-persists
    assert list(s1.bundles[0].input.pinned_group) == []
    del s1

    s2 = _fresh(*_SETUP, f"open_bdb {db}", "load_pipeline")
    assert list(s2.bundles[0].input.pinned_group) == []


# ---------------------------------------------------------------------------
# Explorer sidecar round-trip
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _close_figs():
    yield
    import matplotlib.pyplot as plt
    plt.close('all')


def test_group_pin_survives_sidecar_roundtrip(tmp_path):
    # The explorer's 'S' writes the family's group_uids to the sidecar; a fresh
    # session whose script path resolves to that same sidecar restores the pin
    # on generate_topologies (via _apply_selections).
    side = str(tmp_path / "flow.json")

    s1 = _fresh(*_SETUP, *_BUNDLE)
    w1 = s1.bundles[0]
    fam = _family(s1, w1)
    fam_uids = {buda.topo_uid(w1.input.candidates[i]) for i in fam}
    exp = buda_viz.TopologyExplorer(
        s1.fp, s1.bundles, sidecar_path=side, layer_stack=s1.layers,
        fp_resolver=s1._make_topo_fp_resolver(), groups_fn=s1._loci_groups)
    exp.bidx = 0
    exp.idx = fam[1]                                      # a NON-representative member
    exp._group_pin_current()
    assert os.path.exists(side)

    # Fresh session; its sidecar path resolves to `side` (flow.json next to
    # flow.buda).  generate_topologies triggers _apply_selections.
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    s2.script_path = str(tmp_path / "flow.buda")
    assert s2._sidecar_path() == side
    _quiet(s2, *_SETUP, *_BUNDLE)
    w2 = s2.bundles[0]
    assert w2.input.pinned_group, "group pin restored from sidecar"
    restored_uids = {buda.topo_uid(w2.input.candidates[i])
                     for i in w2.input.pinned_group}
    assert restored_uids == fam_uids
    assert w2.plan.selected_topology_index in w2.input.pinned_group


def test_sidecar_group_pin_yields_to_script_pin(tmp_path):
    # A sidecar group pin is a BASELINE load: an explicit script select_topology
    # for the same bundle must win (mirrors the single-pin precedence).
    side = str(tmp_path / "flow.json")

    s1 = _fresh(*_SETUP, *_BUNDLE)
    fam = _family(s1, s1.bundles[0])
    exp = buda_viz.TopologyExplorer(
        s1.fp, s1.bundles, sidecar_path=side, layer_stack=s1.layers,
        fp_resolver=s1._make_topo_fp_resolver(), groups_fn=s1._loci_groups)
    exp.bidx = 0
    exp.idx = fam[0]
    exp._group_pin_current()

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    s2.script_path = str(tmp_path / "flow.buda")
    _quiet(s2, *_SETUP, *_BUNDLE, "select_topology 1 1")
    w2 = s2.bundles[0]
    assert w2.input.topology_pinned is True              # script single-pin won
    assert list(w2.input.pinned_group) == []             # not group-pinned
    assert w2.plan.selected_topology_index == 0
