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

"""Two MST edges leaving one block must not duplicate each other's first leg.

`realize_mst_edge` routes each edge on its own, from the closest point between
its two blocks.  Two edges incident on the SAME block therefore start at the
same face point, and when both go L-shaped with the same first axis the
shorter one's leg lies entirely inside the longer one's — the longer runs over
it and on past.

That stretch is a duplicate whose start is a FREE END: the shorter leg owns the
block tap, and nothing joins the long leg until they diverge.  Every audit
misses it — the ANTENNA rule counts attachment POSITIONS and the long leg has
two elsewhere; #514's tap-overhang rule wants the piece over a block the
segment itself taps, and this one taps nothing.  Its real cost is downstream:
it pushes the leg's end PAST the divergence junction, which makes that junction
a mid-span conn rather than an endpoint one, and DetailedNUTS only snaps a bit
to its own via at an ENDPOINT conn.  On rnr/mix2_topdown_refine bundle 35 that
left 8.75 + 5.75 + 2.75 units of metal past the last via with `check_design`
reporting Success.

flow/mst_shared_leg_prefix.buda is the shape on its own.  Measured on main it
produces the containment on BOTH standalone MST strategies; the assertions
below are what the trim has to keep true.
"""
import io
import contextlib
import os
import tempfile
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "flow/mst_shared_leg_prefix.buda"
_SUFFIX_FLOW = _ROOT / "flow/mst_shared_leg_suffix.buda"

pytestmark = pytest.mark.mid


def _solve(flow):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {flow}")
    return s


@pytest.fixture(scope="module")
def solved():
    return _solve(_FLOW)


@pytest.fixture(scope="module")
def solved_suffix():
    return _solve(_SUFFIX_FLOW)


def _pairs(session, kinds=None):
    """Containment pairs over every candidate, optionally by candidate type."""
    out = []
    for w in session.bundles:
        for t in w.input.candidates:
            if kinds and not any(k in t.type for k in kinds):
                continue
            out += _contained_pairs(t)
    return out


def _ray(anchor, far):
    """A leg seen as a ray from `anchor`: (is_horizontal, sign, length).

    None when it is not axis-aligned or has no length.  Anchoring on a POINT
    rather than on `seg.start` is what lets this see both halves of the shape.
    `compute_mst` emits every edge with `u < v` and `realize_mst_edge` routes
    `u -> v`, so whether a shared node's legs START or END at it depends
    entirely on that node's index — and the index is not something the defect
    cares about.  Matching `start` to `start` saw only the half where the
    shared node happens to sort first.
    """
    h = anchor.y == far.y
    v = anchor.x == far.x
    if h == v:                       # degenerate (a point) or diagonal
        return None
    d = (far.x - anchor.x) if h else (far.y - anchor.y)
    if d == 0:
        return None
    return h, d > 0, abs(d)


def _contained_pairs(topo):
    """Legs meeting at ANY shared endpoint, same axis and direction from it,
    one lying inside the other.  Returns (type, short_idx, long_idx, where)."""
    out = []
    segs = topo.segments
    for i, a in enumerate(segs):
        for j, b in enumerate(segs):
            if i == j:
                continue
            for pa, qa in ((a.start, a.end), (a.end, a.start)):
                ra = _ray(pa, qa)
                if ra is None:
                    continue
                for pb, qb in ((b.start, b.end), (b.end, b.start)):
                    if (pa.x, pa.y) != (pb.x, pb.y):
                        continue
                    rb = _ray(pb, qb)
                    if rb is None or rb[0] != ra[0] or rb[1] != ra[1]:
                        continue
                    if ra[2] < rb[2]:
                        out.append((topo.type, i, j, (pa.x, pa.y)))
    return sorted(set(out))


def _bundle_blocks(flow):
    """The bundle's endpoint blocks, read from the DESIGN.

    Deliberately NOT read off a candidate.  The point of the test below is that
    the trim never cuts a leg off the block it exists to reach, and a candidate
    is exactly the thing the trim can move — deriving the expectation from one
    made the assertion self-referential (it compared every candidate against
    candidate 0, and passed unchanged if a block were dropped from all of them).
    The `add_bus` line is the contract, and the trim cannot touch it.
    """
    for line in flow.read_text().splitlines():
        parts = line.split()
        if parts[:1] == ["add_bus"]:
            pins = [parts[2], *parts[3].split(",")]
            return {p.split(".")[0] for p in pins}
    raise AssertionError(f"no add_bus line in {flow}")


def test_no_MST_candidate_duplicates_a_sibling_edges_first_leg(solved):
    """THE regression, over EVERY candidate rather than the selected one: the
    shape is a generation defect, so it must be absent from the whole pool.

    On main this vehicle yields two — MST_HV seg1 (len 120) inside seg3 (len
    1320), and MST_VH seg3 (len 70) inside seg1 (len 620), both at the shared
    corner (2010,1010).

    Scoped to MST candidates because that is what the trim covers; the two
    xfails below record where the same shape still lives.
    """
    bad = _pairs(solved, kinds=["MST"])
    assert not bad, f"duplicated MST leg prefixes: {bad}"


def test_the_trim_is_OPT_IN_and_the_default_leaves_the_geometry_alone():
    """The other half of "byte-identical when unused".

    The trim ships default-off because it is not a local fix: candidates are
    WL-SORTED, so shortening one re-sorts the pool and changes which candidates
    clear generation gates (chip3_topdown: 9 of 640 bundles change selected
    type, 3 change pool size, against a main-vs-main control of 0).  Same
    reason set_prune_dominated / set_dedup_loci / set_drop_dangling are opt-in.

    A default that quietly turned on would be caught by the corpus, eventually
    and expensively.  This catches it here: run the fixture with the
    `set_trim_mst_legs on` line REMOVED and the duplication must come back, in
    the exact shape the flow header documents.  If this ever fails, the knob
    has stopped being opt-in.
    """
    src = _FLOW.read_text().replace("set_trim_mst_legs on\n", "")
    # Comments may name the knob (the header explains it); no COMMAND may.
    assert not [ln for ln in src.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")
                and "set_trim_mst_legs" in ln], "the opt-in must be gone"
    # Unique name in the fixture's own directory — one caller today, but CI runs
    # `-p xdist -n 4` and a fixed name is a collision waiting for the second
    # caller (the sibling module had exactly that; Codex #732).
    fd, path = tempfile.mkstemp(dir=_FLOW.parent, prefix="_mst_default_off_",
                                suffix=".buda")
    tmp = Path(path)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(src)
        bad = _pairs(_solve(tmp), kinds=["MST"])
    finally:
        tmp.unlink(missing_ok=True)

    # Exactly the pair the vehicle exists for: MST_HV len 120 inside len 1320,
    # MST_VH len 70 inside len 620, both at the shared corner (2010,1010).
    assert {p[0] for p in bad} == {"MST_HV", "MST_VH"}, bad
    assert all(p[3] == (2010, 1010) for p in bad), bad


@pytest.mark.xfail(strict=True, reason="OPEN: the trim matches shared STARTS only")
def test_MST_legs_that_END_at_the_shared_node_are_also_trimmed(solved_suffix):
    """The mirror half, still open.

    `compute_mst` emits every edge with `u < v` and `realize_mst_edge` routes
    `u -> v`, so a shared node holding the HIGHER index has both its legs END
    there.  The trim requires identical STARTS, so it never fires.

    The lever is which block is the DRIVER, not which is declared first — the
    busterm list leads with the driver, so re-ordering `add_block` cannot move
    the hub's index.  flow/mst_shared_leg_suffix.buda demotes the geometric hub
    to a receiver, which does.  Measured there: MST_VH seg2 (120) inside seg4
    (1320) and MST_HV seg4 (70) inside seg2 (620), both at the END (2010,1010).

    The harm differs in kind and the mirror should not be sold as the same
    defect: both legs tap the hub's face, so there is NO free end and
    `check_design` reports Success.  What is wrong is that the short leg is pure
    duplicate metal (480 of 11114 detailed WL) and its perpendicular partner
    claims an ENDPOINT conn to both legs, which NUTS reports as a junction
    infeasibility and which feeds ripup's contender list.
    """
    bad = _pairs(solved_suffix, kinds=["MST"])
    assert not bad, f"duplicated MST leg suffixes: {bad}"


@pytest.mark.xfail(strict=True, reason="OPEN: the shape is not MST-specific")
def test_TRUNK_candidates_do_not_duplicate_a_stub_the_spine_already_covers(solved):
    """The scope the trim does not reach at all — found by generalizing the
    scanner off `seg.start`, on the PR's own fixture.

    `TRUNK_H@y950` on this vehicle:

        seg0  H (700,950)  -> (2010,950)     the spine
        seg1  V (2010,1000)-> (2010,950)     stub to `hub`s bottom face
        seg3  V (2010,1800)-> (2010,950)     leg to `up`

    seg3 leaves the same trunk point going the same way and runs to y=1800,
    straight through `hub` (x 2000..2100, y 1000..1100) — so seg1's 50 units are
    already covered by seg3 and duplicate it exactly, the same geometry as the
    MST case by a completely different code path (trunk stub generation, not
    `realize_mst_edge`).  Nine such pairs on this fixture.

    Recorded rather than fixed: the trim lives in the MST realization paths, and
    widening it to trunk stubs is a much larger change than this PR makes — it
    would want its own measurement, since the pool re-sorting that drives this
    PR's corpus regression applies there too.
    """
    bad = _pairs(solved, kinds=["TRUNK"])
    assert not bad, f"duplicated TRUNK stubs: {bad}"


def test_the_trimmed_leg_still_reaches_its_own_block(solved):
    """The trim cuts a leg's START, so the obvious way to break it is to cut a
    leg off the block it exists to reach.  Every candidate must still name every
    bundle block in its contract."""
    blocks = _bundle_blocks(_FLOW)
    assert blocks == {"hub", "near", "far", "up"}, blocks
    for w in solved.bundles:
        for t in w.input.candidates:
            assert set(t.connected_block_names) == blocks, t.type


def test_the_flow_ends_clean(solved):
    """Connectivity is preserved by construction — the cut point is the shorter
    leg's far end, which is either that edge's own bend or a block face, so
    something is always waiting at the seam.  This checks it rather than
    asserting it."""
    assert solved.detailed_result.num_unplaced == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        solved.do_command("check_design dnuts")
    assert "no violations found" in buf.getvalue(), buf.getvalue()[-600:]
