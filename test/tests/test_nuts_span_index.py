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

"""The repack's span index (#972 option C1) answers exactly what the layer
scan it replaced answered.

`repack_members` used to find a member's span-touching non-members by testing
every segment on the layer (`occupancy_from`'s closed-span predicate); with
enough members it asks an interval index instead.  Both must return the same
set, so the test compares the index against that predicate itself over
randomized spans, including the cases a hand-written tree gets wrong:
touching ends (closed: touch = occupied), nested and identical spans,
reversed span ends, unplaced segments, and a NaN end.
"""
import math
import random

import buda


def _brute(spans, a, b):
    # occupancy_from's test, spelled out: placed, and sp_lo(o) <= b and a <= sp_hi(o)
    # with sp_lo/sp_hi = min/max of the two ends (NaN never compares true).
    out = []
    for i, (x, y, placed) in enumerate(spans):
        if not placed:
            continue
        lo, hi = min(x, y), max(x, y)
        if math.isnan(x) or math.isnan(y):
            continue
        if lo <= b and a <= hi:
            out.append(i)
    return out


def test_index_matches_the_scan_on_random_spans():
    rng = random.Random(972)
    for trial in range(300):
        n = rng.randint(0, 60)
        # A coarse grid makes touching / identical ends common.
        spans = []
        for _ in range(n):
            x, y = rng.randint(0, 12) * 10.0, rng.randint(0, 12) * 10.0
            r = rng.random()
            if r < 0.05:
                x = float("nan")
            spans.append((x, y, rng.random() > 0.15))
        a = rng.randint(0, 12) * 10.0
        b = a + rng.randint(0, 6) * 10.0
        assert list(buda.span_index_hits(spans, a, b)) == _brute(spans, a, b), \
            (trial, spans, a, b)


def test_touching_end_counts_as_a_hit():
    """Closed spans: a segment ending exactly where the query starts occupies
    (end-to-end collinear bits are a DRC), a gap of one unit does not."""
    spans = [(0.0, 10.0, True), (11.0, 20.0, True)]
    assert list(buda.span_index_hits(spans, 10.0, 15.0)) == [0, 1]
    assert list(buda.span_index_hits(spans, 10.5, 10.9)) == []


def test_empty_and_unplaced_only():
    assert list(buda.span_index_hits([], 0.0, 1.0)) == []
    assert list(buda.span_index_hits([(0.0, 5.0, False)], 0.0, 1.0)) == []
