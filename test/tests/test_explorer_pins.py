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

"""Explorer pin honesty + GUI-pin durability (the hier field report).

The reported failure: a GUI-pinned cell-local bundle (custom layers too)
vanished — the sidecar held only the LAST bundle pinned.  Three causes,
each pinned here:

  1. `s` is a TOGGLE: a second press on a pinned candidate silently
     unpinned it and re-saved the sidecar minus the entry, with only a
     save count as console evidence.  Now every `s`/`x` says PINNED /
     UNPINNED loudly.
  2. The rerun button (↺) auto-pinned whatever candidate was displayed —
     paging through a bundle to compare and pressing ↺ created a pin
     nobody asked for.  Now it re-runs under the EXISTING pins and NOTEs.
  3. A sidecar-applied pin persisted only as `is_selected`: the checkpoint
     BDB never learned it was PINNED, and the forced per-segment layers
     had no BDB home at all — so a plan-resume WITHOUT the .json silently
     re-decided.  Now `_apply_selections` refreshes the pre-expansion rows
     (is_pinned, the template row included) and the planner persist writes
     is_pinned + meta `pinned_layers:<bid>` on every branch, and
     `load_pipeline` restores both.
"""
import contextlib
import io
import json
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

pytestmark = pytest.mark.mid

_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_bus dbus[8] A.p B.p",
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


def _explorer(s, side):
    return buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=side, layer_stack=s.layers,
        fp_resolver=s._make_topo_fp_resolver())


class _Key:
    def __init__(self, key):
        self.key = key


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    import matplotlib.pyplot as plt
    plt.close('all')


# ---------------------------------------------------------------------------
# 1. `s`/`x` honesty: every pin state change is said out loud
# ---------------------------------------------------------------------------

def test_pin_and_toggle_unpin_are_loud(tmp_path):
    side = str(tmp_path / "flow.json")
    s = _fresh(*_SETUP, *_BUNDLE)
    exp = _explorer(s, side)

    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._on_key(_Key('s'))                      # pin candidate 1
    out = buf.getvalue()
    assert "PINNED bundle 1 -> topo 1" in out
    assert len(json.load(open(side))['selections']) == 1

    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._on_key(_Key('s'))                      # TOGGLE: unpin
    out = buf.getvalue()
    assert "UNPINNED bundle 1" in out               # no longer silent
    assert "toggles" in out                         # the remedy is named
    assert json.load(open(side))['selections'] == []
    assert not s.bundles[0].input.topology_pinned


def test_x_unpin_is_loud(tmp_path):
    side = str(tmp_path / "flow.json")
    s = _fresh(*_SETUP, *_BUNDLE)
    exp = _explorer(s, side)
    exp._on_key(_Key('s'))
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._on_key(_Key('x'))
    assert "UNPINNED bundle 1" in buf.getvalue()
    # x on an already-unpinned bundle stays quiet (nothing changed).
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._on_key(_Key('x'))
    assert "UNPINNED" not in buf.getvalue()


# ---------------------------------------------------------------------------
# 2. ↺ re-runs under EXISTING pins — it must not pin the displayed candidate
# ---------------------------------------------------------------------------

def test_rerun_does_not_autopin(tmp_path):
    side = str(tmp_path / "flow.json")
    s = _fresh(*_SETUP, *_BUNDLE)
    calls = []
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=side, layer_stack=s.layers,
        fp_resolver=s._make_topo_fp_resolver(), rerun_fn=lambda: calls.append(1))

    exp.idx = 1                                     # page to an UNPINNED candidate
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._rerun_and_refresh()
    out = buf.getvalue()
    assert calls == [1]                             # the pipeline DID re-run
    assert "NOTE: displayed candidate 2 is not pinned" in out
    assert not os.path.exists(side)                 # and NO sidecar entry appeared
    assert not s.bundles[0].input.topology_pinned

    # With a pin in place, ↺ re-runs silently under it (no NOTE, no change).
    exp._on_key(_Key('s'))
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        exp._rerun_and_refresh()
    assert "NOTE" not in buf.getvalue()
    assert len(json.load(open(side))['selections']) == 1


# ---------------------------------------------------------------------------
# 3. Durability: a sidecar-applied pin + forced layers survive a plan-resume
#    with the .json GONE — restored from the checkpoint BDB alone
# ---------------------------------------------------------------------------

def test_sidecar_pin_and_layers_survive_without_json(tmp_path):
    db = str(tmp_path / "flow.ckpt.bdb")
    side = str(tmp_path / "flow.json")

    # Session 1: candidates generated, sidecar written (as the explorer
    # would), planner applies it — the pin + forced layers must reach the BDB.
    s1 = buda_cli.BudaSession()
    s1.no_viz = True
    s1.script_path = str(tmp_path / "flow.buda")
    _quiet(s1, *_SETUP, f"open_bdb {db}", *_BUNDLE)
    w1 = s1.bundles[0]
    t = w1.input.candidates[1]
    nseg = len(t.segments)
    forced = ([6, 7, 6] + [7, 6] * nseg)[:nseg]     # any valid per-seg layers
    json.dump({"selections": [{
        "bundle_hint": w1.input.original_bundle.get_net_names()[0],
        "bundle_id": w1.input.original_bundle.id,
        "topo_type": t.type, "topo_wl": t.estimated_wirelength,
        "topo_uid": buda.topo_uid(t), "topo_index_hint": 1,
        "note": "", "selected_at": "now", "seg_layers": forced,
    }]}, open(side, "w"))
    out = _quiet(s1, "run_planner 3")
    assert "Pinned bundle 1 to topology 2" in out
    del s1

    import sqlite3
    con = sqlite3.connect(db)
    pinned = con.execute(
        "SELECT cand_index FROM topology WHERE bundle_id='1' AND is_pinned=1"
    ).fetchall()
    meta = con.execute(
        "SELECT value FROM meta WHERE key='pinned_layers:1'").fetchone()
    con.close()
    assert pinned, "sidecar pin never became is_pinned in the checkpoint"
    assert meta and json.loads(meta[0]) == forced, \
        "forced layers have no durable home"

    # Session 2: the json is DELETED — the checkpoint alone must restore the
    # pin AND the forced layers, and a re-plan must keep both.
    os.remove(side)
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    s2.script_path = str(tmp_path / "flow.buda")
    _quiet(s2, *_SETUP, f"open_bdb {db}", "load_pipeline")
    w2 = s2.bundles[0]
    assert w2.input.topology_pinned, "pin lost without the sidecar json"
    assert list(w2.input.pinned_seg_layers) == forced, \
        "forced layers lost without the sidecar json"
    uid_before = buda.topo_uid(
        w2.input.candidates[w2.plan.selected_topology_index])
    _quiet(s2, "run_planner 3")
    uid_after = buda.topo_uid(
        w2.input.candidates[w2.plan.selected_topology_index])
    assert uid_before == uid_after, "replan re-decided the pinned bundle"
    assert list(w2.plan.seg_layers) == forced, "replan dropped the forced layers"


def test_unpinned_bundle_keeps_no_layer_meta(tmp_path):
    # The meta writer must CLEAR on unpin — a stale `pinned_layers:` entry
    # would force layers onto whatever a later planner picks.
    db = str(tmp_path / "flow.ckpt.bdb")
    s = _fresh(*_SETUP, f"open_bdb {db}", *_BUNDLE,
               "select_topology 1 2", "run_planner 3")
    w = s.bundles[0]
    w.input.pinned_seg_layers = [6] * len(
        w.input.candidates[w.plan.selected_topology_index].segments)
    _quiet(s, "run_planner 3")          # persists pin + layers
    _quiet(s, "unpin_topology 1")       # durable unpin
    del s

    import sqlite3
    con = sqlite3.connect(db)
    pinned = con.execute(
        "SELECT cand_index FROM topology WHERE is_pinned=1").fetchall()
    meta = con.execute(
        "SELECT value FROM meta WHERE key='pinned_layers:1'").fetchone()
    con.close()
    assert pinned == [], "unpin left a pinned row behind"
    assert not (meta and meta[0]), "unpin left stale forced-layer meta"


# ---------------------------------------------------------------------------
# 4. Hier: a sidecar pin on a cell-local TEMPLATE lands on the template row,
#    so a pre-expansion plan-resume restores it (the reported bug's exact shape)
# ---------------------------------------------------------------------------

def test_hier_template_pin_survives_plan_resume(tmp_path, monkeypatch):
    repo = Path(__file__).parents[2]
    import shutil
    shutil.copy(repo / "demo" / "resume_hier_input.bdb.sql", tmp_path)
    ckpt = str(tmp_path / "rh.ckpt.bdb")
    monkeypatch.setenv("BUDA_BDB_MATERIALIZE_TO", ckpt)

    setup = [
        f"source {repo / 'flow' / 'tracks' / 'tracks.buda'}",
        f"open_bdb {tmp_path / 'resume_hier_input.bdb.sql'}",
        "corner_margin dx 5 dy 5", "set_min_stub_length 2",
    ]
    build = ["derive_busterms 1", "add_blocks_from_bdb 0",
             "add_blocks_from_bdb 1 skip", "run_hier_bundler",
             "generate_hier_topologies"]

    s1 = buda_cli.BudaSession()
    s1.no_viz = True
    s1.script_path = str(tmp_path / "rh.buda")
    _quiet(s1, *setup, *build)
    # Sidecar-pin the cell-local TEMPLATE (the b_lohi bundle) — the shape
    # that was lost: pinned pre-expansion, persisted only post-expansion.
    wt = next(w for w in s1.bundles if w.input.original_bundle.cell_context)
    tt = wt.input.candidates[1]
    tid = wt.input.original_bundle.id
    json.dump({"selections": [{
        "bundle_hint": wt.input.original_bundle.get_net_names()[0],
        "bundle_id": tid, "topo_type": tt.type,
        "topo_wl": tt.estimated_wirelength, "topo_uid": buda.topo_uid(tt),
        "topo_index_hint": 1, "note": "", "selected_at": "now",
    }]}, open(str(tmp_path / "rh.json"), "w"))
    out = _quiet(s1, "run_planner hier 5")
    assert "applies to every instance" in out       # the fan-out is SAID
    tt_uid = buda.topo_uid(tt)
    del s1

    import sqlite3
    con = sqlite3.connect(ckpt)
    row = con.execute(
        "SELECT cand_index FROM topology WHERE bundle_id=? AND is_pinned=1",
        (str(tid),)).fetchone()
    con.close()
    assert row is not None, "template row never learned it was pinned"

    # Resume at the planner with NO sidecar: the template must come back
    # pinned and the resumed hier plan must fan the pin to every instance.
    os.remove(str(tmp_path / "rh.json"))
    monkeypatch.delenv("BUDA_BDB_MATERIALIZE_TO", raising=False)
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    s2.script_path = str(tmp_path / "rh.buda")
    _quiet(s2, f"source {repo / 'flow' / 'tracks' / 'tracks.buda'}",
           f"open_bdb {ckpt}", "corner_margin dx 5 dy 5",
           "set_min_stub_length 2", "add_blocks_from_bdb 0",
           "add_blocks_from_bdb 1 skip", "load_pipeline")
    wt2 = next(w for w in s2.bundles if w.input.original_bundle.cell_context)
    assert wt2.input.topology_pinned, "template pin lost on plan-resume"
    assert buda.topo_uid(
        wt2.input.candidates[wt2.plan.selected_topology_index]) == tt_uid

    pinned_idx = wt2.plan.selected_topology_index
    _quiet(s2, "run_planner hier 5")
    # Every expanded instance of the template class selects the pinned shape
    # (instance-frame candidate copies have their own content uids, so the
    # comparison is by index + type, not uid).
    fanned = s2._hier_expansion_map.get(tid, [])
    assert len(fanned) >= 2, "template did not expand to its instances"
    for w in fanned:
        assert w.plan.selected_topology_index == pinned_idx
        assert (w.input.candidates[w.plan.selected_topology_index].type
                == tt.type)


# ---------------------------------------------------------------------------
# 5. The persist-refresh is caller-scoped (Codex #815 P2 + the preview seam)
# ---------------------------------------------------------------------------

def test_generation_with_a_sidecar_persists_the_pool_once(tmp_path):
    # generate_topologies runs its own full _persist_topologies right after
    # _apply_selections — the internal refresh there would double the whole
    # candidate rewrite on every sidecar-carrying generation (Codex #815).
    db = str(tmp_path / "flow.ckpt.bdb")
    side = str(tmp_path / "flow.json")
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(tmp_path / "flow.buda")
    _quiet(s, *_SETUP, f"open_bdb {db}", "run_bundler", "generate_topologies")
    w = s.bundles[0]
    t = w.input.candidates[1]
    json.dump({"selections": [{
        "bundle_hint": w.input.original_bundle.get_net_names()[0],
        "bundle_id": w.input.original_bundle.id,
        "topo_type": t.type, "topo_wl": t.estimated_wirelength,
        "topo_uid": buda.topo_uid(t), "topo_index_hint": 1,
        "note": "", "selected_at": "now",
    }]}, open(side, "w"))

    calls = []
    orig = s._persist_topologies
    # Instance-level wrap on purpose: assigning onto the CLASS — even to
    # "restore" — leaves a BudaSession attribute that shadows the mixin
    # (test_core_does_not_shadow_mixins would rightly fail after this test).
    s._persist_topologies = lambda: (calls.append(1), orig())[1]
    _quiet(s, "generate_topologies")         # sidecar applies a NEW pin here
    del s._persist_topologies
    assert calls == [1], f"pool persisted {len(calls)}x during generation"
    # ...and the single persist still made the pin durable.
    assert s.bundles[0].input.topology_pinned
    import sqlite3
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM topology WHERE is_pinned=1").fetchone()[0]
    con.close()
    assert n == 1


def test_explorer_rerun_is_still_a_preview(tmp_path):
    # The explorer's ↺ path calls _apply_selections too — it must NOT write
    # the pin to the checkpoint: a preview's contract is that the BDB
    # changes only on explicit commands (replan / run_planner / pin).
    db = str(tmp_path / "flow.ckpt.bdb")
    side = str(tmp_path / "flow.json")
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(tmp_path / "flow.buda")
    _quiet(s, *_SETUP, f"open_bdb {db}", "run_bundler", "generate_topologies")
    w = s.bundles[0]
    t = w.input.candidates[1]
    json.dump({"selections": [{
        "bundle_hint": w.input.original_bundle.get_net_names()[0],
        "bundle_id": w.input.original_bundle.id,
        "topo_type": t.type, "topo_wl": t.estimated_wirelength,
        "topo_uid": buda.topo_uid(t), "topo_index_hint": 1,
        "note": "", "selected_at": "now",
    }]}, open(side, "w"))

    _quiet(s, "run_planner 3")               # a first, committed plan
    _quiet(s, "unpin_topology *")            # durable unpin: BDB rows clear
    json.dump(json.load(open(side)), open(side, "w"))  # sidecar still there

    with contextlib.redirect_stdout(io.StringIO()):
        s._rerun_all()                       # the ↺ preview replan
    assert s.bundles[0].input.topology_pinned    # LIVE state adopted the pin
    import sqlite3
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM topology WHERE is_pinned=1").fetchone()[0]
    con.close()
    assert n == 0, "a preview re-run wrote a pin to the checkpoint"


# ---------------------------------------------------------------------------
# 6. retire_sidecar durability edges (Codex #826)
# ---------------------------------------------------------------------------

def _sql_fixture(tmp_path):
    # A serialized .sql input carrying nothing (the flow declares the design).
    s = _fresh(f"open_bdb {tmp_path / 'seed.bdb'}")
    _quiet(s, f"save_bdb {tmp_path / 'in.bdb.sql'}")
    del s
    return tmp_path / "in.bdb.sql"


def _pin_via_sidecar(s, side):
    w = s.bundles[0]
    t = w.input.candidates[1]
    json.dump({"selections": [{
        "bundle_hint": w.input.original_bundle.get_net_names()[0],
        "bundle_id": w.input.original_bundle.id,
        "topo_type": t.type, "topo_wl": t.estimated_wirelength,
        "topo_uid": buda.topo_uid(t), "topo_index_hint": 1,
        "note": "", "selected_at": "now",
    }]}, open(side, "w"))


def test_retire_flushes_writeback_first(tmp_path):
    # `open_bdb x.sql writeback`: the DURABLE copy is the .sql, written only
    # at save/exit — retiring at a mid-session commit must flush it first,
    # or an interruption before exit leaves neither store holding the pin.
    sql = _sql_fixture(tmp_path)
    side = str(tmp_path / "flow.json")
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(tmp_path / "flow.buda")
    _quiet(s, f"open_bdb {sql} writeback", *_SETUP, *_BUNDLE)
    _pin_via_sidecar(s, side)
    _quiet(s, "run_planner 3")
    out = _quiet(s, "retire_sidecar")
    assert "durable in the checkpoint" in out
    assert not os.path.exists(side)
    # The .sql source ALREADY holds the pin — before any save/exit.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript(sql.read_text())
    assert con.execute(
        "SELECT COUNT(*) FROM topology WHERE is_pinned=1").fetchone()[0] == 1
    con.close()


def test_retire_never_touches_a_throwaway_materialization(tmp_path):
    # Without `writeback` the materialization dies with the session: the
    # json is the only persistence, so retire must no-op.
    sql = _sql_fixture(tmp_path)
    side = str(tmp_path / "flow.json")
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = str(tmp_path / "flow.buda")
    _quiet(s, f"open_bdb {sql}", *_SETUP, *_BUNDLE)
    _pin_via_sidecar(s, side)
    _quiet(s, "run_planner 3")
    out = _quiet(s, "retire_sidecar")
    assert "durable" not in out
    assert os.path.exists(side), "retire deleted the only persistence"
