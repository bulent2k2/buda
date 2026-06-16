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

"""A net/bus that references a block absent from the floorplan is a fatal input
error: generate_topologies must quit (sys.exit) rather than silently route from
the chip origin.

Regression (flow/quickstart.buda): the `dft` bus drove from `IOPAD.io` while the
block was defined as `IO_PAD`.  Floorplan::get_block_bounds returns a degenerate
{0,0,0,0} for an unknown name, so the bundle routed from (0,0) far from IO_PAD
with no error.  generate_topologies now validates every endpoint via
Floorplan::has_block and exits with a clear "did you mean" message.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda      # noqa: E402
import buda_cli  # noqa: E402

import pytest    # noqa: E402


def _session_with_blocks():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command("def_layer 4 M4 H TOP 1.0")
    sess.do_command("def_layer 5 M5 V TOP 1.0")
    sess.do_command("add_block IO_PAD 500 0 900 50")
    sess.do_command("add_block CPU 100 100 300 300")
    return sess


def test_has_block_binding():
    # The new Floorplan.has_block distinguishes a real block from the silent
    # get_block_bounds fallback.
    fp = buda.Floorplan()
    fp.add_block("IO_PAD", 0, 0, 10, 10)
    assert fp.has_block("IO_PAD")
    assert not fp.has_block("IOPAD")


def test_undefined_driver_block_is_fatal():
    sess = _session_with_blocks()
    sess.do_command("add_bus dft[3] IOPAD.io CPU.io")   # typo: IOPAD vs IO_PAD
    sess.do_command("run_bundler strict")
    with pytest.raises(SystemExit) as ei:
        sess.do_command("generate_topologies")
    assert ei.value.code == 1


def test_undefined_receiver_block_is_fatal():
    sess = _session_with_blocks()
    sess.do_command("add_bus dft[3] IO_PAD.io CPUX.io")  # typo: CPUX vs CPU
    sess.do_command("run_bundler strict")
    with pytest.raises(SystemExit) as ei:
        sess.do_command("generate_topologies")
    assert ei.value.code == 1


def test_defined_blocks_pass():
    sess = _session_with_blocks()
    sess.do_command("add_bus dft[3] IO_PAD.io CPU.io")   # both blocks exist
    sess.do_command("run_bundler strict")
    sess.do_command("generate_topologies")               # must not raise
    w = [b for b in sess.bundles if 'dft_0' in list(b.input.original_bundle.get_net_names())][0]
    assert len(w.input.candidates) > 0
