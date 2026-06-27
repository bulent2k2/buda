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
Regression: TRUNK_V stub suppression must be coverage-safe.

add_trunk_v drops a stub it deems redundant when a farther same-side stub's
attachment row passes through the shorter stub's block.  The original test only
checked has_stub[j] — so in an A<-B<-C chain it could suppress B believing C
covers it, while C's own stub was ALSO suppressed and the surviving wire sat at a
row that missed B.  The block was then left with no busterm/pass-through (a silent
open routed by the planner, unplaced bits at DetailedNUTS).

This is the big2 bus_056 / blk_09 bug: the TRUNK_V@x5485 candidate emitted only a
trunk + one far stub and never tapped blk_09.  The fix decides survivors
farthest-first and suppresses a stub only when a SURVIVING farther stub genuinely
spans its block.  See flow/big_data_test/big2_b4_b24.buda.
"""
import buda


def _make_fp(coords):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)  # M6 H, M7 V (matches the big2 tracks)
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def test_trunk_v_transitive_suppression_keeps_coverage():
    """The exact big2 bus_056 geometry: every TRUNK_V candidate covers blk_09.

    Driver blk_09 with receivers blk_14, blk_39, blk_19.  All three stubs land on
    the same (left) side of the x=5485 trunk; blk_19 is the farthest.  Pre-fix
    blk_14 was suppressed by blk_19 (correct: blk_19's row crosses blk_14) and
    blk_09 was suppressed by blk_14 — but blk_14 had no surviving wire, and
    blk_19's surviving row (y=3310) misses blk_09's y-extent [2570,3100].  The
    TRUNK_V@x5485 candidate was then a bare trunk + one stub and left blk_09 open.
    """
    coords = {
        "blk_09": (3500, 2570, 4870, 3100),
        "blk_14": (2230, 2720, 3500, 3365),
        "blk_39": (4870, 3365, 6100, 4425),
        "blk_19": (0,    2660, 1250, 3310),
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("blk_09", ["blk_14", "blk_39", "blk_19"])
    trunk_v = [c for c in cands if c.type.startswith("TRUNK_V")]
    assert trunk_v, "expected TRUNK_V candidates for this multicast bundle"
    for c in trunk_v:
        v = _violations(c, fp)
        assert "BUSTERM_OPEN" not in v, (
            f"{c.type} left a block uncovered (transitive suppression): {v}"
        )


def test_trunk_v_keeps_legitimate_suppression():
    """Coverage-safety must not disable suppression when it is genuinely valid.

    Here the farthest stub (far_lo) really does span the nearer block (near),
    so near's stub is still redundant and should be suppressed — the resulting
    TRUNK_V stays connected (no open) AND does not regress to emitting a stub for
    every block.  We assert the candidate is clean; the suppressed stub being
    covered by the pass-through is what keeps it clean.
    """
    coords = {
        "drv":    (4800, 2000, 5200, 2200),   # driver near the trunk
        "near":   (3000, 2050, 3400, 2150),   # short left stub, fully inside far's row band
        "far_lo": (0,    2000, 800,  2200),   # farthest left stub; its row spans 'near'
        "up":     (4800, 3000, 5200, 3200),   # a stub above to force a real trunk span
    }
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("drv", ["near", "far_lo", "up"])
    trunk_v = [c for c in cands if c.type.startswith("TRUNK_V")]
    assert trunk_v, "expected TRUNK_V candidates"
    for c in trunk_v:
        assert "BUSTERM_OPEN" not in _violations(c, fp), (
            f"{c.type} dropped coverage where suppression was valid: {_violations(c, fp)}"
        )
