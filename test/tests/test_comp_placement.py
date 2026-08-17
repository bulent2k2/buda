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

"""What "placed" means, and what happens when a block is not.

Found on `flow/ariane133`, whose `btcl -i … dnuts` resume refused to load a
checkpoint its own build session had just reported as routed:

    Error: load_pipeline: block(s) i_cache_subsystem, … are not in the
    current Floorplan

`load_pipeline` was right and was the only thing checking.  Two defects put
those names into the persisted topologies:

1. the BDB -> session Floorplan projection read ANY negative coordinate as
   the no-placement sentinel, so 496 of 504 depth-0 components with real
   geometry were dropped (die ports at x1 = -70, and a derived container box
   whose margin put it at y1 = -240);
2. hier generation never asked whether an endpoint block was in its frame,
   and `get_block_bounds` answers for an unknown name with a degenerate
   {0,0,0,0} — so unplaced containers became blocks at the die origin and
   were routed to.  The flat flow has guarded this since `add_net` existed
   (`_validate_endpoint_blocks`); the hier path did not.
"""
import contextlib
import io

import pytest

import buda
import buda_cli
from comp_placement import bbox_is_placed, is_placed


class _Row:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


# ── the predicate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bbox, placed, why", [
    ((-1, -1, -1, -1), False, "the import sentinel"),
    ((-70, 475020, 70, 475160), True, "a DEF PIN straddling the die edge"),
    ((1920, -240, 2712800, 2714000), True, "a derived container below y=0"),
    ((-200, 0, -140, 60), True, "a block placed left of the origin"),
    ((5920, 3760, 5920, 3760), False, "a degenerate point, no extent"),
    ((0, 0, 0, 0), False, "the get_block_bounds phantom"),
    ((10, 10, 20, 20), True, "an ordinary block"),
])
def test_placement_is_the_sentinel_not_the_sign(bbox, placed, why):
    assert bbox_is_placed(*bbox) is placed, why
    assert is_placed(_Row(*bbox)) is placed, why


# ── the projection, and the frames, agree ──────────────────────────────────

def _neg_db():
    """Two blocks left of the origin plus one unplaced container — the
    ariane133 shapes, at four components instead of five thousand."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf", 60, 60)
    db.add_comp("L", "leaf", "", -200, 0, -140, 60)     # negative x, real
    db.add_comp("R", "leaf", "", 1200, 0, 1260, 60)
    db.add_comp("D", "leaf", "", 100, -40, 160, 20)     # negative y, real
    db.add_comp("ghost", "leaf", "", -1, -1, -1, -1)    # the sentinel
    return db


def _session(db, *cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


def test_a_block_at_negative_coordinates_is_projected():
    """The regression proper: `x1 < 0 or y1 < 0` dropped these three."""
    s, out = _session(_neg_db(), "add_blocks_from_bdb 0")
    names = {n for n, _ in s.fp.get_all_blocks()}
    assert names == {"L", "R", "D"}
    assert "ghost" not in names                  # the sentinel still skipped
    assert "1 skipped (unplaced)" in out         # counted, and only the one


def test_every_frame_gives_the_same_answer():
    """Generation validates a candidate in ITS frame and `load_pipeline`
    validates it in the session floorplan, so a block the two disagree about
    is a resume failure by construction — which is how this surfaced."""
    s, _ = _session(_neg_db(), "add_blocks_from_bdb 0")
    session_fp = {n for n, _ in s.fp.get_all_blocks()}
    depth_fp = {n for n, _ in s._build_bdb_floorplan(0).get_all_blocks()}
    assert session_fp == depth_fp == {"L", "R", "D"}


def test_a_degenerate_component_is_not_a_block():
    """A zero-area box hosts no routing and gives a generator nothing to
    tap; admitting one produces the same phantom the sentinel check is for."""
    db = _neg_db()
    db.add_comp("flat", "leaf", "", 500, 500, 500, 500)
    s, _ = _session(db, "add_blocks_from_bdb 0")
    assert "flat" not in {n for n, _ in s.fp.get_all_blocks()}


# ── an unplaced endpoint is refused, not routed to the origin ──────────────

def _hier_db(unplaced_sink):
    """A depth-0 bus between two leaves, the sink optionally unplaced — the
    `DRV:i_cache_subsystem|REC:i_perf_counters` shape."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf", 60, 60)
    db.add_comp("A", "leaf", "", 100, 100, 160, 160)
    if unplaced_sink:
        db.add_comp("B", "leaf", "", -1, -1, -1, -1)
    else:
        db.add_comp("B", "leaf", "", 900, 100, 960, 160)
    for i in range(4):
        db.add_net_pins(f"n_{i}", "A.out", ["B.in"])
    buda.BustermGen(db).derive(1)
    return db


_LAYERS = ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50"]


def test_a_placed_pair_still_generates():
    """The control: nothing about the guard changes an ordinary bundle."""
    s, out = _session(_hier_db(False), *_LAYERS,
                      "run_hier_bundler", "generate_hier_topologies")
    assert "BUDA-1211" not in out and "BUDA-1210" not in out
    assert any(w.input.candidates for w in s.bundles)


def test_an_unplaced_endpoint_generates_nothing_and_says_so():
    """Before the guard this produced candidates anchored at (0,0): the
    generator asked `get_block_bounds('B')`, got the degenerate {0,0,0,0}
    back, and routed to the die origin — on ariane133, with placed metal at
    y = -840, off the die, under a reported-clean run."""
    s, out = _session(_hier_db(True), *_LAYERS,
                      "run_hier_bundler", "generate_hier_topologies")
    assert "BUDA-1211" in out
    assert "have no placement" in out
    for w in s.bundles:
        for t in w.input.candidates:
            for seg in t.segments:
                assert (seg.start.x, seg.start.y) != (0, 0), \
                    "a route anchored at the die origin is the phantom block"
                assert (seg.end.x, seg.end.y) != (0, 0)


def test_the_message_carries_the_remedy():
    _, out = _session(_hier_db(True), *_LAYERS,
                      "run_hier_bundler", "generate_hier_topologies")
    line = next(ln for ln in out.splitlines() if "BUDA-1211" in ln)
    assert "derive_container_bboxes" in line       # how to give it geometry
    assert "B" in line                             # which block


# ── check_design says it in the session that can still fix it ──────────────

def test_check_design_reports_an_unresolvable_block_contract():
    """BUDA-1502 is `load_pipeline`'s gate asked one session earlier.  The
    contract is broken here by hand (a candidate naming a block no frame
    has), because the two fixes above mean generation no longer produces
    one — the audit still has to catch a restored or hand-edited topology."""
    s, _ = _session(_hier_db(False), *_LAYERS, "add_blocks_from_bdb 0",
                    "run_hier_bundler", "generate_hier_topologies",
                    "run_planner 1")
    w = next(w for w in s.bundles if w.plan.selected_topology_index >= 0)
    idx = w.plan.selected_topology_index
    cands = list(w.input.candidates)                 # pybind copy semantics:
    t = cands[idx]                                   # copies all the way down,
    t.connected_block_names = list(t.connected_block_names) + ["nowhere"]
    cands[idx] = t                                   # so write each level back
    w.input.candidates = cands
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("check_design")
    out = buf.getvalue()
    assert "BUDA-1502" in out
    assert "nowhere" in out
    assert "load_pipeline" in out                    # names the consequence


def test_a_clean_hier_design_says_nothing():
    """No block-contract fault = no line, so the id means something."""
    _, out = _session(_hier_db(False), *_LAYERS, "add_blocks_from_bdb 0",
                      "run_hier_bundler", "generate_hier_topologies",
                      "run_planner 1", "check_design")
    assert "BUDA-1502" not in out
