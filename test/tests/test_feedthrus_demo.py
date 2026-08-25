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

"""Feedthru in the HIER flow + demo/feedthrus.buda, pinned.

The feedthru flags live on session.fp, and no hier bundle generates
against session.fp — every hier frame is a DERIVED floorplan (depth
projection / cross-level / cell-local).  A `set_feedthru` in a hier flow
was therefore silently dropped for every hier bundle: the trunk passed
straight over the declared block and the dump reported it only as a
pass-through.  The fix records declarations on the session and
`_apply_fp_session_settings` replays them onto every derived frame;
block-scoped rules match by the name the frame knows (full component
paths in depth/cross-level frames, the child's LOCAL name in a
cell-local template frame — one rule governing every instance of the
cell).

The fast tests hold the replay in-process; the mid test pins the demo
vehicle end to end so its printed narrative cannot rot.
"""
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import buda_cli
from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]
_DEMO = _ROOT / "demo" / "feedthrus.buda"


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def _hier_session():
    """The demo's shape, minimal: three top-level blocks with a tall mid
    the trunk must cross, plus one tile cell with the same shape inside."""
    s = _session()
    for cmd in [
        "open_bdb :memory:",
        "set_die 1000 720",
        "add_cell io_cell 100 100",
        "add_cell relay_cell 100 300",
        "add_cell tile_cell 460 140",
        "add_cell leaf_cell 100 100",
        "add_cell lmid_cell 100 140",
        "add_inst_to_cell tile_cell a leaf_cell 20 20",
        "add_inst_to_cell tile_cell m lmid_cell 180 0",
        "add_inst_to_cell tile_cell b leaf_cell 340 20",
        "add_inst t0 tile_cell - 40 40",
        "add_inst t1 tile_cell - 540 40",
        "add_inst src io_cell - 60 480",
        "add_inst mid relay_cell - 400 400",
        "add_inst dst io_cell - 800 480",
        "bdb_net_mode on",
        "add_bus lft_t0[4] t0/a.out t0/m.in,t0/b.in",
        "add_bus lft_t1[4] t1/a.out t1/m.in,t1/b.in",
        "add_bus tft[4] src.out mid.in,dst.in",
        "def_layer 3 M3 H 20",
        "def_layer 4 M4 V 20",
        "def_layer 5 M5 H TOP 10",
        "def_layer 6 M6 V TOP 10",
        "derive_busterms",
        "add_blocks_from_bdb 1",
        "run_hier_bundler",
    ]:
        _run(s, cmd)
    return s


def _feedthru_sets(s):
    """{bundle_id: union of feedthru_blocks over the pool} after a fresh
    generation."""
    _run(s, "generate_hier_topologies")
    out = {}
    for w in s.bundles:
        ft = set()
        for t in w.input.candidates:
            ft.update(t.feedthru_blocks)
        out[w.input.original_bundle.id] = ft
    return out


def _bundle_ids(s):
    """(top tft bundle id, [cell-local lft bundle ids])."""
    top, local = None, []
    for w in s.bundles:
        b = w.input.original_bundle
        first = b.net_names[0]
        if first.startswith("tft"):
            top = b.id
        else:
            local.append(b.id)
    return top, local


def test_hier_frames_drop_feedthru_without_the_replay():
    # The control: no declaration → no candidate anywhere carries a
    # feedthru block (the trunk passes over mid silently).
    s = _hier_session()
    fts = _feedthru_sets(s)
    assert all(ft == set() for ft in fts.values()), fts


def test_block_layer_rule_reaches_the_depth_frame():
    # (mid, M5): the top-level cross-block bundle generates in a DERIVED
    # depth-projection frame, not session.fp — the replay is what makes
    # the rule bite there.
    s = _hier_session()
    _run(s, "set_feedthru mid M5")
    fts = _feedthru_sets(s)
    top, local = _bundle_ids(s)
    assert fts[top] == {"mid"}, fts
    for bid in local:
        assert fts[bid] == set(), fts


def test_local_name_reaches_every_cell_local_frame():
    # (m, *): the tile buses generate in tile_cell's cell-local frame,
    # whose blocks carry LOCAL names — one declaration governs BOTH
    # tiles' bundles (template semantics), and the handler accepts the
    # local name because the BDB knows it (session.fp does not).
    s = _hier_session()
    out = _run(s, "set_feedthru m *")
    assert "unknown block" not in out, out
    fts = _feedthru_sets(s)
    top, local = _bundle_ids(s)
    assert fts[top] == set(), fts
    assert len(local) == 2
    for bid in local:
        assert fts[bid] == {"m"}, fts


def test_precedence_ladder_on_the_derived_frame():
    # The resolver order the demo walks, observed through the derived
    # frame: (*,layer) on → (block,*) off beats it → (block,layer) on
    # beats that.
    s = _hier_session()
    top, _ = _bundle_ids(s)

    _run(s, "set_feedthru * M5")
    assert "mid" in _feedthru_sets(s)[top]

    _run(s, "set_feedthru mid * off")
    assert "mid" not in _feedthru_sets(s)[top]

    _run(s, "set_feedthru mid M5")
    assert "mid" in _feedthru_sets(s)[top]


@pytest.mark.mid
def test_feedthrus_demo_runs_clean_and_shows_the_narrative(tmp_path):
    """The vehicle's own promise, held end to end: each stage's dump shows
    the state its comment claims, and the pipeline routes the relayed
    selections clean."""
    flow = tmp_path / "feedthrus.buda"
    shutil.copy(_DEMO, flow)
    env = buda_env(_ROOT, "build", "src")
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"),
         "--no-viz", str(flow)],
        capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-400:])
    assert r.stdout.count("Success: no violations found") == 2, r.stdout[-800:]
    assert "0 bits unplaced" in r.stdout, r.stdout[-800:]

    log = (tmp_path / "log" / "feedthrus_flow.log").read_text()
    # Stages 1 and 3: mid is a silent pass-through (baseline, and the
    # (block,*) off override beating the (*,layer) wildcard).
    assert log.count("passthru: mid") == 2, log[-2000:]
    # Stages 2 and 4: mid relays ((*,M5), then (mid,M5) beating (mid,*) off).
    assert log.count("feedthru=['mid']") == 2, log[-2000:]
    # Stage 5: the local-name rule relays in BOTH tiles' frames.
    assert log.count("feedthru=['m']") == 2, log[-2000:]
