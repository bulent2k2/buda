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

"""The `buda --threads N` (-j) flag: one knob for every parallel stage.

Drives the planner's candidate scoring, NUTS's per-layer solvers and the
healers' trial sweep through their env knobs (BUDA_PLAN/NUTS/SWEEP_THREADS).
Min 1; max = the machine's logical-CPU count as this process may use it
(cgroup/affinity-aware — on hybrid CPUs exactly P-cores x SMT + E-cores);
default = half the max.  An explicit flag overrides the per-engine env vars;
the default only fills unset ones.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ENV_VARS = ("BUDA_PLAN_THREADS", "BUDA_NUTS_THREADS", "BUDA_SWEEP_THREADS")


def test_detect_max_threads_positive_and_affinity_aware():
    n = buda_cli.detect_max_threads()
    assert n >= 1
    # On Linux the count is the scheduling affinity (container-aware), which
    # can never exceed os.cpu_count().
    if hasattr(os, "sched_getaffinity"):
        assert n == len(os.sched_getaffinity(0))
        assert n <= (os.cpu_count() or n)


def test_resolve_default_is_half_the_max():
    assert buda_cli.resolve_threads(None, 8) == 4
    assert buda_cli.resolve_threads(None, 5) == 2
    assert buda_cli.resolve_threads(None, 1) == 1      # min 1, never 0


def test_resolve_clamps_to_machine_range(capsys):
    assert buda_cli.resolve_threads(4, 8) == 4         # in range: as given
    assert buda_cli.resolve_threads(1, 8) == 1
    assert buda_cli.resolve_threads(8, 8) == 8
    assert buda_cli.resolve_threads(99, 8) == 8        # clamped high, LOUD
    assert "clamped to 8" in capsys.readouterr().out
    assert buda_cli.resolve_threads(0, 8) == 1         # clamped low, LOUD
    assert buda_cli.resolve_threads(-3, 8) == 1
    assert "clamped to 1" in capsys.readouterr().out


def test_explicit_flag_overrides_engine_env(monkeypatch):
    for v in _ENV_VARS:
        monkeypatch.setenv(v, "7")
    buda_cli.apply_thread_env(3, explicit=True)
    assert all(os.environ[v] == "3" for v in _ENV_VARS)


def test_default_fills_only_unset_engine_env(monkeypatch):
    monkeypatch.setenv("BUDA_PLAN_THREADS", "7")       # user's own override
    monkeypatch.delenv("BUDA_NUTS_THREADS", raising=False)
    monkeypatch.delenv("BUDA_SWEEP_THREADS", raising=False)
    buda_cli.apply_thread_env(2, explicit=False)
    assert os.environ["BUDA_PLAN_THREADS"] == "7"      # kept
    assert os.environ["BUDA_NUTS_THREADS"] == "2"      # filled
    assert os.environ["BUDA_SWEEP_THREADS"] == "2"     # filled


def test_cli_end_to_end_reports_and_clamps(tmp_path):
    """`buda --threads 999 tiny.buda` clamps LOUDLY, reports the resolved
    count, and the flow still runs (subprocess: the real argparse path)."""
    script = tmp_path / "tiny.buda"
    script.write_text("def_layer 4 M4 H 50\nexit\n")
    root = Path(__file__).parents[2]
    env = {**os.environ, "PYTHONPATH": f"{root}/build:{root}/src"}
    for v in _ENV_VARS:
        env.pop(v, None)
    out = subprocess.run(
        [sys.executable, str(root / "src" / "buda_cli.py"),
         "--threads", "999", "--no-viz", str(script)],
        capture_output=True, text=True, env=env, timeout=120).stdout
    assert "clamped to" in out
    assert "[threads]" in out and "--threads" in out

    out = subprocess.run(
        [sys.executable, str(root / "src" / "buda_cli.py"),
         "--no-viz", str(script)],
        capture_output=True, text=True, env=env, timeout=120).stdout
    assert "[threads]" in out and "default: max/2" in out
