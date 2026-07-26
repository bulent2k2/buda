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
"""`tools/qor_nopin.py` pin-command detection.

The pin-free sweep must neutralize a `select_topology[ies]` in EVERY form the
CLI itself would accept (`buda_cli.do_command` strips, splits on arbitrary
whitespace, and lowercases the verb) — else an uppercase / tab / multi-space /
trailing-comment pin leaks through and defeats the whole point of the sweep on
exactly the pinned flows.  `_is_pin_cmd` is a pure function (no compiled `buda`
needed), so this collects and runs on any checkout.
"""
import qor_nopin


def test_neutralizes_standard_pin_forms():
    assert qor_nopin._is_pin_cmd("select_topology 1 5")
    assert qor_nopin._is_pin_cmd("select_topologies 1,5-9 3")


def test_neutralizes_cli_normalized_forms():
    # Case-insensitive verb (CLI lowercases parts[0]).
    assert qor_nopin._is_pin_cmd("SELECT_TOPOLOGY 1 5")
    assert qor_nopin._is_pin_cmd("Select_Topologies 1 3")
    # Arbitrary whitespace: leading, tab-delimited, multi-space (CLI .split()).
    assert qor_nopin._is_pin_cmd("\tselect_topology\t1\t5")
    assert qor_nopin._is_pin_cmd("   select_topology   1   5")
    # Trailing content / comment does not hide the verb.
    assert qor_nopin._is_pin_cmd("select_topology 1 5  # pin b1")


def test_does_not_neutralize_unrelated_or_prefixed_commands():
    assert not qor_nopin._is_pin_cmd("select_topologyx 1 5")   # not an exact verb
    assert not qor_nopin._is_pin_cmd("unpin_topology *")
    assert not qor_nopin._is_pin_cmd("run_planner 3")
    assert not qor_nopin._is_pin_cmd("generate_topologies")
    assert not qor_nopin._is_pin_cmd("")
    assert not qor_nopin._is_pin_cmd("   ")
    assert not qor_nopin._is_pin_cmd("# select_topology 1 5")  # fully commented line
