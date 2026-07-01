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


def test_flow_test_emits_command_stream(tmp_path):
    # A fixtureless BudaSession-driven test → the exact do_command sequence.
    r, txt = _run(tmp_path, "test_bidirectional_pairs_a_net_with_its_reverse")
    assert r.returncode == 0, r.stderr
    assert "add_block A 0 0 100 80" in txt      # self-contained setup
    assert "run_bundler BIDIRECTIONAL" in txt


def test_tmp_path_fixture_flow_is_captured(tmp_path):
    # A tmp_path-fixture, BDB-building, hier-flow test now works (previously the
    # fixture check rejected it) and emits a runnable open_bdb + hier flow.
    r, txt = _run(tmp_path, "test_hier_bidirectional_mixes_directions_in_one_bundle")
    assert r.returncode == 0, r.stderr
    assert "open_bdb" in txt
    assert "run_hier_bundler depth 1 bidirectional" in txt


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
