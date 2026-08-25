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

"""tools/unit2buda.py — topology capture + CLI/BDB-flow capture with fixtures."""

import importlib.util
import os
import subprocess
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TOOL = os.path.join(_ROOT, "tools", "unit2buda.py")

# Spawns a subprocess that imports buda + runs a test — heavier than a unit test.
pytestmark = pytest.mark.mid


def _run(tmp_path, *args):
    out = tmp_path / "out.buda"
    r = subprocess.run([sys.executable, _TOOL, *args, "-o", str(out)],
                       capture_output=True, text=True, cwd=_ROOT)
    return r, (out.read_text() if out.exists() else "")


def test_flat_flow_appends_full_pipeline(tmp_path):
    # A fixtureless BudaSession-driven flat test → self-contained setup + the
    # bundler, then the appended flat downstream pipeline.
    r, txt = _run(tmp_path, "test_bidirectional_pairs_a_net_with_its_reverse")
    assert r.returncode == 0, r.stderr
    lines = txt.splitlines()
    assert "add_block A 0 0 100 80" in txt      # self-contained setup
    assert "run_bundler BIDIRECTIONAL" in lines
    # Flat downstream appended, in order.
    for cmd in ("generate_topologies", "run_planner", "run_nuts",
                "run_detailed_nuts", "visualize"):
        assert cmd in lines, cmd
    assert lines.index("run_bundler BIDIRECTIONAL") < lines.index("generate_topologies")


def test_hier_flow_appends_full_pipeline(tmp_path):
    # A tmp_path-fixture, BDB-building, hier-flow test now works and appends the
    # hier downstream (generate_hier_topologies / run_planner hier / …).
    r, txt = _run(tmp_path, "test_hier_bidirectional_mixes_directions_in_one_bundle")
    assert r.returncode == 0, r.stderr
    lines = txt.splitlines()
    assert "open_bdb" in txt
    assert "run_hier_bundler depth 1 bidirectional" in lines
    for cmd in ("generate_hier_topologies", "run_planner hier", "run_nuts",
                "run_detailed_nuts", "visualize"):
        assert cmd in lines, cmd


def test_appended_pipeline_does_not_duplicate_ran_stages(tmp_path):
    # A test that ALREADY ran topo/planner/nuts → only the missing stages append,
    # and flow mode wins over the internal generate_candidates calls.
    r, txt = _run(tmp_path, "test_hier_bidirectional_pipeline_runs_end_to_end")
    assert r.returncode == 0, r.stderr
    lines = txt.splitlines()
    assert lines.count("generate_hier_topologies") == 1   # not re-appended
    assert lines.count("run_nuts") == 1
    assert "run_detailed_nuts" in lines and "visualize" in lines


def test_topology_mode_unchanged(tmp_path):
    # The original flat topology-generation capture still works.
    r, txt = _run(tmp_path, "test_spread_z_hvh_trunk_has_bounded_slide_range")
    assert r.returncode == 0, r.stderr
    assert "generate_topologies" in txt
    assert "visualize_topologies" in txt


def test_unsupported_fixture_reports_clearly(tmp_path):
    tf = tmp_path / "test_weird.py"
    tf.write_text("def test_w(nonexistent_fixture):\n    pass\n")
    r = subprocess.run([sys.executable, _TOOL, f"{tf}::test_w"],
                       capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode != 0
    assert "unsupported fixture" in r.stderr.lower()
    assert "nonexistent_fixture" in r.stderr


def _load_tool():
    spec = importlib.util.spec_from_file_location("u2b_tool", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parametrize_case_selection():
    u = _load_tool()

    @pytest.mark.parametrize("seed", [1, 2, 7])
    def t(seed):
        pass

    assert u._parametrize_values(t, 0) == {"seed": 1}
    assert u._parametrize_values(t, 2) == {"seed": 7}
    assert u._parametrize_values(t, 99) == {"seed": 7}   # clamps to last case

    @pytest.mark.parametrize("a,b", [(1, 2), (3, 4)])
    def t2(a, b):
        pass

    assert u._parametrize_values(t2, 1) == {"a": 3, "b": 4}


def test_global_teg_default_is_recorded_and_emitted(tmp_path):
    # The `set_teg_mode` global default (teg_multirect_status open 14; Codex
    # P2 on PR #839): a test that sets the API default and then declares a
    # KEYWORDLESS multi-rect block routes under OVER — the emitted repro must
    # carry `set_teg_mode over` BEFORE that block (declaration-time
    # resolution), else it runs under THRU and no longer reproduces the
    # topology.  An explicit per-block mode must still win untouched.
    tf = tmp_path / "test_teg_default.py"
    tf.write_text(
        "import buda\n"
        "def test_default_over():\n"
        "    fp = buda.Floorplan()\n"
        "    fp.add_block('A', 0, 150, 100, 250)\n"
        "    fp.set_default_teg_mode(buda.TegMode.OVER)\n"
        "    fp.add_block_rects('B', [(200, 0, 300, 100),\n"
        "                             (200, 300, 300, 400)])\n"
        "    fp.add_block_rects('C', [(400, 0, 500, 100),\n"
        "                             (400, 300, 500, 400)],\n"
        "                       teg_mode=buda.TegMode.THRU)\n"
        "    g = buda.TopologyGenerator(fp)\n"
        "    g.set_layer_ids(4, 5)\n"
        "    g.generate_candidates('A', ['B', 'C'])\n")
    r, txt = _run(tmp_path, f"{tf}::test_default_over")
    assert r.returncode == 0, r.stderr
    lines = txt.splitlines()
    assert "set_teg_mode over" in lines
    b_line = next(l for l in lines if l.startswith("add_block B"))
    c_line = next(l for l in lines if l.startswith("add_block C"))
    # Declaration-time order: the default precedes the block it governed.
    assert lines.index("set_teg_mode over") < lines.index(b_line)
    assert "teg_mode" not in b_line          # keywordless stays keywordless
    assert c_line.endswith("teg_mode thru")  # explicit per-block mode wins
