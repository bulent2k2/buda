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

"""`generate_topologies_for_bundle` accepts a bundle ID (not only a net-name
hint), and `run_bundler --dump` prints a one-line-per-bundle summary."""
import contextlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                  "add_block A 0 0 100 100", "add_block B 500 0 600 100",
                  "add_block C 0 500 100 600",
                  "add_bus bus_03[4] A.p B.p", "add_bus bus_07[2] A.p C.p"):
            s.do_command(c)
    return s


def _run(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def test_run_bundler_dump_prints_one_line_per_bundle():
    s = _session()
    out = _run(s, "run_bundler --dump")
    # One "b-<id>" line per bundle, each with a net count and net-name preview.
    assert out.count("nets=") == len(s.bundles) == 2
    assert "b-1" in out and "b-2" in out
    assert "bus_03_0" in out and "bus_07_0" in out


def test_run_bundler_dump_rejects_unknown_flag():
    s = _session()
    with pytest.raises(SystemExit):
        _run(s, "run_bundler --dumpx")


def test_run_bundler_without_dump_prints_no_bundle_lines():
    s = _session()
    out = _run(s, "run_bundler")
    assert "nets=" not in out          # no per-bundle dump unless --dump


def test_gen_for_bundle_by_id():
    s = _session()
    _run(s, "run_bundler")
    _run(s, "generate_topologies")
    bid = s.bundles[0].input.original_bundle.id
    # Clear then regen by numeric ID.
    s.bundles[0].input.candidates = []
    out = _run(s, f"generate_topologies_for_bundle {bid}")
    assert f"bundle {bid}" in out
    assert len(s.bundles[0].input.candidates) > 0


def test_gen_for_bundle_by_hint_still_works():
    s = _session()
    _run(s, "run_bundler")
    _run(s, "generate_topologies")
    # bus_07 -> the second bundle.
    b2 = next(w for w in s.bundles
              if w.input.original_bundle.get_net_names()[0].startswith("bus_07"))
    b2.input.candidates = []
    out = _run(s, "generate_topologies_for_bundle bus_07")
    assert len(b2.input.candidates) > 0
    assert f"bundle {b2.input.original_bundle.id}" in out


def test_gen_for_bundle_by_id_colon_form():
    s = _session()
    _run(s, "run_bundler")
    _run(s, "generate_topologies")
    bid = s.bundles[1].input.original_bundle.id
    s.bundles[1].input.candidates = []
    _run(s, f"generate_topologies_for_bundle id:{bid}")
    assert len(s.bundles[1].input.candidates) > 0


def test_gen_for_bundle_unknown_id_warns():
    s = _session()
    _run(s, "run_bundler")
    _run(s, "generate_topologies")
    out = _run(s, "generate_topologies_for_bundle 99999")
    assert "Could not find bundle" in out
