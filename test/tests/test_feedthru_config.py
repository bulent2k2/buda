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

"""
Feedthru phase 1: the per-(block, layer) FeedthruConfig resolver on Floorplan.

These cover only the configuration layer (set_feedthru* setters + get_feedthru
resolution). The generator split that consumes get_feedthru lands in a later phase.
Resolution is most-specific-first: (block,layer) > (block,*) > (*,layer) > global.
"""
import buda


def _fp():
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 10, 10)
    fp.add_block("B", 20, 0, 30, 10)
    return fp


def test_default_is_off():
    fp = _fp()
    assert fp.get_feedthru("A", 4) is False
    assert fp.get_feedthru("B", 5) is False


def test_global():
    fp = _fp()
    fp.set_feedthru(True)
    assert fp.get_feedthru("A", 4) is True
    assert fp.get_feedthru("B", 99) is True  # any block, any layer


def test_per_layer_for_all_blocks():
    fp = _fp()
    fp.set_feedthru_layer(4, True)
    assert fp.get_feedthru("A", 4) is True
    assert fp.get_feedthru("B", 4) is True
    assert fp.get_feedthru("A", 5) is False  # other layer untouched


def test_per_block_for_all_layers():
    fp = _fp()
    fp.set_feedthru_block("A", True)
    assert fp.get_feedthru("A", 4) is True
    assert fp.get_feedthru("A", 5) is True
    assert fp.get_feedthru("B", 4) is False  # other block untouched


def test_per_block_layer_pair():
    fp = _fp()
    fp.set_feedthru_block_layer("A", 4, True)
    assert fp.get_feedthru("A", 4) is True
    assert fp.get_feedthru("A", 5) is False  # only the one pair
    assert fp.get_feedthru("B", 4) is False


def test_block_beats_layer():
    # (block,*) is more specific than (*,layer): A on M4 follows the block rule.
    fp = _fp()
    fp.set_feedthru_layer(4, False)
    fp.set_feedthru_block("A", True)
    assert fp.get_feedthru("A", 4) is True   # per_block beats per_layer
    assert fp.get_feedthru("B", 4) is False  # B falls through to per_layer


def test_pair_carves_exception_out_of_layer():
    # set_feedthru * M4 on ; set_feedthru A M4 off  ->  A-on-M4 is off.
    fp = _fp()
    fp.set_feedthru_layer(4, True)
    fp.set_feedthru_block_layer("A", 4, False)
    assert fp.get_feedthru("A", 4) is False  # most-specific off wins
    assert fp.get_feedthru("B", 4) is True   # broader on still applies
    assert fp.get_feedthru("A", 5) is False  # untouched layer = global default (off)


def test_pair_beats_block_and_global():
    fp = _fp()
    fp.set_feedthru(True)               # global on
    fp.set_feedthru_block("A", True)    # A on everywhere
    fp.set_feedthru_block_layer("A", 5, False)  # except A on M5
    assert fp.get_feedthru("A", 4) is True
    assert fp.get_feedthru("A", 5) is False
    assert fp.get_feedthru("B", 5) is True  # still global on
