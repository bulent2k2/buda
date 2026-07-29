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

"""overlap_delta_vs exactness (the corner-pass runtime work).

The repair/repack accept guards replaced their per-move full
find_overlaps recounts with an overlap-count DELTA against the pre-move
snapshot: pairs whose BOTH endpoints are unchanged have identical
overlap status in both states and cancel, so

    count(after) OP count(before)  ⟺  delta OP 0

for any ordering OP.  That identity is what makes the optimization
decision-preserving (validated byte-identical end-to-end on the rnr
vehicles + bigHalf/big2/tc3a and the congested synthetics); this test
pins it directly on randomized segment populations via the
_find_overlap_count / _overlap_delta_vs test hooks.
"""

import random

import buda


def _seg(bundle_id, seg_idx, layer, pos, lo, hi, width=4.0, placed=True):
    ts = buda.TrackSegment()
    ts.bundle_id = bundle_id
    ts.seg_idx = seg_idx
    ts.layer = layer
    ts.horiz = layer % 2 == 0
    ts.span_lo = lo
    ts.span_hi = hi
    ts.interval_lo = 0.0
    ts.interval_hi = 1000.0
    ts.width = width
    ts.track_position = pos
    ts.placed = placed
    return ts


def _population(rng, n=120, layers=(4, 5, 6)):
    segs = []
    for i in range(n):
        lo = rng.uniform(0, 800)
        segs.append(_seg(
            bundle_id=i // 3,             # some same-bundle pairs (exempt)
            seg_idx=i % 3,
            layer=rng.choice(layers),
            pos=rng.uniform(10, 400),
            lo=lo,
            hi=lo + rng.uniform(5, 300),
            width=rng.choice([2.0, 4.0, 8.0]),
            placed=rng.random() > 0.1,    # some unplaced
        ))
    return segs


def _mutate(rng, segs, k):
    """Random geometry mutations on k segments (the repair/repack shapes:
    track moves, span stretches, place/unplace)."""
    out = [buda.TrackSegment() for _ in segs]
    for dst, src in zip(out, segs):
        for f in ("bundle_id", "seg_idx", "layer", "horiz", "span_lo",
                  "span_hi", "interval_lo", "interval_hi", "width",
                  "track_position", "placed"):
            setattr(dst, f, getattr(src, f))
    for i in rng.sample(range(len(out)), k):
        r = rng.random()
        if r < 0.5:
            out[i].track_position = rng.uniform(10, 400)
        elif r < 0.8:
            out[i].span_hi = out[i].span_hi + rng.uniform(-50, 50)
        else:
            out[i].placed = not out[i].placed
    return out


def test_delta_equals_full_recount_randomized():
    rng = random.Random(20260729)
    for trial in range(25):
        before = _population(rng)
        after = _mutate(rng, before, k=rng.randint(1, 15))
        want = (buda._find_overlap_count(after)
                - buda._find_overlap_count(before))
        got = buda._overlap_delta_vs(before, after)
        assert got == want, f"trial {trial}: delta {got} != recount {want}"


def test_delta_zero_when_unchanged():
    rng = random.Random(7)
    segs = _population(rng)
    assert buda._overlap_delta_vs(segs, segs) == 0
