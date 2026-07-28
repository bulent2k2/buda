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

"""Compact block labels: hierarchical instance paths overlap badly on dense
floorplans, so the on-screen label shows the LEAF component (`chip/a/b/c` -> `c`)
while flat names are unchanged.  The block's identity for click/highlight stays
the full name — this is display-only."""
import matplotlib
matplotlib.use("Agg")

from viz_common import _short_block_label


def test_flat_name_unchanged():
    assert _short_block_label("blk_20") == "blk_20"
    assert _short_block_label("u_cpu") == "u_cpu"


def test_hierarchical_name_shows_leaf():
    assert _short_block_label("chip/i_dnuts1_0/u0") == "u0"
    assert _short_block_label("left/u4/bt/lo") == "lo"
    assert _short_block_label("chip/i_dnuts1_3/top2") == "top2"


def test_overlong_leaf_is_tail_ellipsized():
    long_leaf = "a/" + "x" * 40
    out = _short_block_label(long_leaf, maxlen=16)
    assert len(out) == 16
    assert out.startswith("…")
    assert out.endswith("x")           # the tail (most-identifying) is kept


def test_leaf_within_maxlen_is_verbatim():
    assert _short_block_label("chip/reasonable_leaf", maxlen=16) == "reasonable_leaf"
