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

"""THE JUDGE (`tools/independent_audit.py`, convergence ladder build item 2).

A judge is only worth what it CATCHES, so the shape of this file is a
mutation matrix: the two-instance vehicle routes clean and the judge says
so, and then each fault it exists to find is planted in the persisted tables
— one at a time, by hand, in SQL — and the judge must name that fault.  A
test that only ever sees a clean design proves nothing about a referee.

The independence claim gets its own test rather than a comment.  It is
checked twice, because each check alone has a hole: the source is scanned
for a forbidden import (which a runtime `__import__` would evade) and the
tool is RUN in a subprocess with `PYTHONPATH` emptied (which a
conditionally-skipped import would evade).  The second is the one that
matters here and it has a trap this repository has been caught by before:
`conftest.py` pins the repo's own `build/` at `sys.path[0]`, so an
in-process check would find `buda` importable no matter what the tool does.
"""
import contextlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
import buda_cli  # noqa: E402

from subprocess_env import buda_env  # noqa: E402
from test_layer_demand import _DESIGN  # noqa: E402

_JUDGE = _ROOT / "tools" / "independent_audit.py"

# The net whose row every mutation below edits.  It is one 8-bit cell-local
# bus's bit 0: one horizontal M6 wire, x 150..350 at y = 133, width 2, with
# `x_0` (a top-level bus, a DIFFERENT net) on the same track at x 430..920
# and `loc_1` on the next track up at y = 136.  Every planted fault is
# arithmetic off those four numbers.
_NET = "loc_0"


def _build(path, patterns_first=True):
    """Route the two-instance vehicle into a file-backed BDB.

    `patterns_first` picks which side of the `open_bdb` the track patterns
    are declared on.  Both orders must leave the same grid in the
    checkpoint, which they did not until the journal fix — see
    `test_a_grid_declared_before_open_bdb_reaches_the_checkpoint`.
    """
    src = [c for c in _DESIGN if c.startswith("source ")]
    rest = [c for c in _DESIGN
            if not c.startswith("source ") and c != "open_bdb :memory:"]
    design = ([f"open_bdb {path}"] + src + rest if patterns_first
              else src + [f"open_bdb {path}"] + rest)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in design + ["run_nuts", "run_detailed_nuts", "save_bdb"]:
            s.do_command(c)
    return path


@pytest.fixture(scope="module")
def routed(tmp_path_factory):
    """The clean routed design, built once."""
    return _build(str(tmp_path_factory.mktemp("judge") / "clean.bdb"))


def _judge(path, *args):
    """Run the judge as the user runs it, and return (exit, stdout+stderr)."""
    r = subprocess.run([sys.executable, str(_JUDGE), str(path), *args],
                       capture_output=True, text=True, cwd=str(_ROOT))
    return r.returncode, r.stdout + r.stderr


def _mutated(routed, tmp_path, *statements):
    """A copy of the routed design with SQL applied directly to the tables."""
    p = str(tmp_path / "mutated.bdb")
    shutil.copy(routed, p)
    con = sqlite3.connect(p)
    for sql in statements:
        con.execute(sql)
    con.commit()
    con.close()
    return p


def _kinds(out):
    """The violation kinds the judge counted, from its own summary lines."""
    kinds = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "[judge]" and parts[2].isdigit():
            kinds.add(parts[1])
    return kinds


# ---------------------------------------------------------------------------
# The clean verdict
# ---------------------------------------------------------------------------

def test_the_judge_passes_the_routed_vehicle(routed):
    """The design `check_design` calls clean, an independent audit also calls
    clean — the agreement that makes a disagreement elsewhere mean
    something."""
    code, out = _judge(routed)
    assert code == 0, out
    assert "VERDICT: CLEAN (0)" in out, out
    # and it judged something: a verdict over zero wires would also be clean
    assert "24 bit-wire(s), 24 net(s), layers M6" in out, out


def test_the_json_carries_the_same_verdict(routed, tmp_path):
    import json
    out_json = tmp_path / "res.json"
    code, out = _judge(routed, "--json", str(out_json), "--quiet")
    assert code == 0, out
    assert out.strip() == "", out          # --quiet is quiet
    res = json.loads(out_json.read_text())
    assert res["clean"] is True and res["total"] == 0
    assert res["wires"] == 24 and res["counts"]["SHORT"] == 0


# ---------------------------------------------------------------------------
# The mutation matrix — one planted fault per test
# ---------------------------------------------------------------------------

def _net_id(con_path, name=_NET):
    con = sqlite3.connect(con_path)
    row = con.execute("SELECT id FROM net WHERE name=?", (name,)).fetchone()
    con.close()
    return row[0]


def test_a_short_is_caught(routed, tmp_path):
    """`loc_0` stretched to x = 500 runs into `x_0`, which holds the same
    track (y = 133) from x = 430.  Two nets, one layer, overlapping metal."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"UPDATE net_segment SET x2=500 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"SHORT"}, out
    assert "two different nets on M6" in out and "x_0" in out, out


def test_metal_over_a_keepout_is_caught(routed, tmp_path):
    """A keepout declared on M6 across `loc_0`'s run.  It blocks BY LAYER:
    the same rectangle on another layer must not be a violation, which the
    control below pins."""
    p = _mutated(routed, tmp_path,
                 "INSERT INTO keepout (x1,y1,x2,y2,layers,inside_block,net) "
                 "VALUES (200,130,250,135,'6',0,'')")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"KEEPOUT"}, out
    assert "(200,130)-(250,135)" in out, out

    other = _mutated(routed, tmp_path,
                     "DELETE FROM keepout",
                     "INSERT INTO keepout (x1,y1,x2,y2,layers,inside_block,net) "
                     "VALUES (200,130,250,135,'5',0,'')")
    code, out = _judge(other)
    assert code == 0, out                  # M5 keepout, M6 wire: not a crossing


def test_an_off_grid_bit_is_caught(routed, tmp_path):
    """y = 134.5 is not a SIGNAL slot centre of the M6 pattern (its tracks
    here are 133, 136, 139, ...).  The move ALSO shorts `loc_1` at y = 136,
    which is a true consequence of the mutation and not noise — a judge that
    reported only one of the two would be hiding the other."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 "UPDATE net_segment SET track_position=134.5, y1=133.5, "
                 f"y2=135.5 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"OFF_GRID", "SHORT"}, out
    assert "track 134.5 is not a SIGNAL slot centre" in out, out


def test_a_wire_across_its_layer_direction_is_caught(routed, tmp_path):
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"UPDATE net_segment SET is_horiz=0 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert "LAYER_DIR" in _kinds(out), out
    assert "runs vertically on a horizontal layer" in out, out


def test_a_net_that_does_not_reach_its_block_is_caught(routed, tmp_path):
    """Metal moved off the design: still one connected piece, still on a
    signal track, shorting nothing — and electrically useless.  This is the
    fault a check on tracks and overlaps alone cannot see."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"UPDATE net_segment SET x1=2000, x2=2100 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"OPEN"}, out
    assert "does not reach its endpoint block u1/a" in out, out


def test_a_net_with_no_metal_at_all_is_caught(routed, tmp_path):
    """A net a bundle carries and DNUTS placed nothing for.  Reported as its
    own kind: "nothing was built" and "what was built is broken" are
    different findings and a table that merges them says less."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"DELETE FROM net_segment WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"NO_METAL"}, out
    assert f"net {_NET} is carried by a bundle and has no placed metal" in out, out


def test_metal_broken_into_two_pieces_is_caught(routed, tmp_path):
    """Half a net's run, as a second row that does not touch the first.  The
    vias table is empty on this vehicle (every bit is one segment), so this
    exercises the GEOMETRIC half of the connectivity rule: same net, same
    layer, no contact, two pieces."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 "INSERT INTO net_segment (bundle_id, seg_idx, bit_index, "
                 "net_id, layer, is_horiz, x1, y1, x2, y2, track_position, "
                 "width) SELECT bundle_id, seg_idx + 90, bit_index, net_id, "
                 "layer, is_horiz, 600, y1, 700, y2, track_position, width "
                 f"FROM net_segment WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert "OPEN" in _kinds(out), out
    assert "is 2 disconnected pieces" in out, out


def test_abutting_metal_of_two_nets_is_not_a_short(routed, tmp_path):
    """The control for the short check: a T-junction touches without
    overlapping, and a judge that called abutment a short would fail every
    correct route.  `loc_0` extended to exactly x = 430 meets `x_0`'s start
    and shares no area."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"UPDATE net_segment SET x2=430 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 0, out


# ---------------------------------------------------------------------------
# Independence, and the two things a verdict must not be confused with
# ---------------------------------------------------------------------------

def test_the_judge_imports_no_engine():
    """Statically: nothing of BUDA's is imported anywhere in the file."""
    import ast
    tree = ast.parse(_JUDGE.read_text())
    got = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            got.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            got.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "__import__":
                pytest.fail("the judge calls __import__ — the static check "
                            "cannot see what it loads")
    allowed = {"argparse", "json", "math", "os", "sqlite3", "sys",
               "collections"}
    assert got <= allowed, f"the judge imports {sorted(got - allowed)}"


def test_the_judge_runs_with_the_engine_unimportable(routed, tmp_path):
    """Dynamically, and this is the one that matters: run it in a subprocess
    with `PYTHONPATH` emptied, from a directory that is not the repo, so
    `import buda` could not succeed even if the file tried.  An in-process
    check would be VACUOUS — `conftest.py` pins the repo's `build/` at
    `sys.path[0]`, so `buda` is importable there whatever the tool does."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    probe = subprocess.run(
        [sys.executable, "-c", "import buda"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert probe.returncode != 0, ("this test is vacuous where the engine is "
                                   "importable without PYTHONPATH (an "
                                   "installed wheel) — skip it there rather "
                                   "than believing it")
    r = subprocess.run([sys.executable, str(_JUDGE), str(routed)],
                       capture_output=True, text=True, env=env,
                       cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERDICT: CLEAN" in r.stdout, r.stdout


def test_a_grid_declared_before_open_bdb_reaches_the_checkpoint(tmp_path):
    """The gap building the judge found, and the reason it is a test here.

    v29 persists the routing grid by WRITE-THROUGH at each declaration, so a
    flow that sources its track patterns BEFORE `open_bdb` — the order
    nearly every flow in this tree is written in, `_DESIGN` included — left a
    checkpoint carrying a route and no grid at all: zero `track_pattern`
    rows against the six the session routed under.  That is unjudgeable
    here, and worse elsewhere, since resuming such a checkpoint is exactly
    the failure v29 exists to remove.  The session now journals each
    declaration and `open_bdb` replays it, so both orders store the same
    grid."""
    before = _build(str(tmp_path / "before.bdb"), patterns_first=False)
    after = _build(str(tmp_path / "after.bdb"), patterns_first=True)
    rows = {}
    for tag, path in (("before", before), ("after", after)):
        con = sqlite3.connect(path)
        rows[tag] = sorted(con.execute(
            "SELECT layer_id, origin, is_horiz, slots FROM track_pattern"))
        con.close()
    assert len(rows["before"]) == 6, rows["before"]
    assert rows["before"] == rows["after"]      # same grid, either order
    for path in (before, after):
        code, out = _judge(path)
        assert code == 0, out
        assert "VERDICT: CLEAN (0)" in out, out


def test_a_design_with_no_grid_cannot_be_judged_and_says_so(routed, tmp_path):
    """Exit 2, not 1.  Every on-grid question against a design with no
    stored grid is vacuous, and "I cannot judge this" must not share a
    status with "this is broken" — a harness gating on the judge would read
    a missing grid as a clean design."""
    p = _mutated(routed, tmp_path, "DELETE FROM track_pattern")
    code, out = _judge(p)
    assert code == 2, out
    assert "cannot judge" in out and "no track pattern" in out, out


def test_a_design_with_no_route_cannot_be_judged_either(routed, tmp_path):
    p = _mutated(routed, tmp_path, "DELETE FROM net_segment")
    code, out = _judge(p)
    assert code == 2, out
    assert "no placed bit-wires" in out, out


# ---------------------------------------------------------------------------
# Hierarchy: which component a bundle is entitled to stop at
# ---------------------------------------------------------------------------

def _flow(tmp_path_factory, flow, name):
    """Route a checked-in flow into a checkpoint through the `:memory:`
    redirect (the one `btcl -b` arms), so its text is untouched."""
    out = str(tmp_path_factory.mktemp(name) / f"{name}.bdb")
    env = buda_env(_ROOT, BUDA_BDB_MEMORY_TO=out)
    r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        str(_ROOT / "flow" / flow), "--no-viz"],
                       capture_output=True, text=True, env=env, cwd=str(_ROOT))
    assert os.path.isfile(out), r.stdout + r.stderr
    return out


@pytest.fixture(scope="module")
def vias(tmp_path_factory):
    """`flow/hier_four_blocks.buda` — 34 bit-wires over M5/M6/M7 with 20
    per-bit vias.  The two-instance vehicle routes entirely on M6 and has
    NONE, so nothing else here exercises a via at all."""
    return _flow(tmp_path_factory, "hier_four_blocks.buda", "vias")


@pytest.fixture(scope="module")
def hier(tmp_path_factory):
    """`flow/hier_testcase.buda` routed into a checkpoint (~0.2s).

    The two-instance vehicle above is FLAT in its pin structure — every net
    names leaves and nothing else — so it cannot exercise the rule this
    section is about.  This flow carries `s2p_0`, whose pins sit on the
    container `src_i` AND on `src_i/buf_i` inside it.
    """
    return _flow(tmp_path_factory, "hier_testcase.buda", "hier")


# `s2p_0` runs x 230..370 on M6 and happens to touch both leaves
# (`src_i/buf_i` ends at exactly x = 230), so the flow AS ROUTED cannot tell
# the two candidate rules apart.  Pulling the wire back to x = 250 —
# `src_i`'s right face — makes it touch the CONTAINER and not the leaf
# inside it, which is the shape the rule is about.
_PULL_BACK = ("UPDATE net_segment SET x1=250 WHERE net_id="
              "(SELECT id FROM net WHERE name='s2p_0')")


def test_a_bundle_may_stop_at_the_container_that_carries_the_pin(hier, tmp_path):
    """A cross-level bus lands on a container's face and the container's own
    routing takes it to the leaf.  That is the contract BUDA routes to, and
    the FIRST rule written here did not know it: requiring every pin-bearing
    component reported 2048 OPENs on `flow/soc_small.buda` against a route
    `check_design` calls clean and inspection agrees with."""
    con = sqlite3.connect(hier)
    nested = con.execute("""SELECT count(*) FROM pin p
                            JOIN component c ON c.id = p.comp_id
                            WHERE c.parent_id IN (SELECT comp_id FROM pin
                                                  WHERE net_id = p.net_id)"""
                         ).fetchone()[0]
    con.close()
    assert nested > 0, ("this test is vacuous on a design with no net whose "
                        "pins nest — pick another flow")
    code, out = _judge(hier)
    assert code == 0, out                  # as routed
    code, out = _judge(_mutated(hier, tmp_path, _PULL_BACK))
    assert code == 0, out                  # and stopping at the container face
    assert "VERDICT: CLEAN (0)" in out, out


def test_the_container_only_counts_because_it_carries_a_pin(hier, tmp_path):
    """The control that keeps the weakening honest.  Same pulled-back wire,
    with the CONTAINERS' pin rows dropped: the leaf is then the outermost
    pin-bearing component, nothing licenses stopping at a face, and the
    judge must say so.  A rule satisfied by any enclosing box would pass
    this and would be worth nothing."""
    drop_container_pins = """DELETE FROM pin WHERE net_id =
        (SELECT id FROM net WHERE name='s2p_0')
        AND EXISTS (SELECT 1 FROM pin q JOIN component c ON c.id = q.comp_id
                    WHERE q.net_id = pin.net_id AND c.parent_id = pin.comp_id)"""
    p = _mutated(hier, tmp_path, _PULL_BACK, drop_container_pins)
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"OPEN"}, out
    assert "net s2p_0 does not reach its endpoint block src_i/buf_i" in out, out


def test_without_bundle_membership_the_judge_narrows_and_says_so(routed, tmp_path):
    """A design that never mirrored its nets into the BDB has no
    `bundle_net` rows, and "which nets were supposed to carry metal" is then
    unanswerable.  The judge falls back to the nets that HAVE metal — so
    shorts and broken metal are still judged — and NAMES what the fallback
    costs, because a silent narrowing is how an audit comes to mean less
    than the reader thinks.  Without the fallback the scope is empty and
    connectivity is judged for nothing at all, silently."""
    p = _mutated(routed, tmp_path, "DELETE FROM bundle_net")
    code, out = _judge(p)
    assert code == 0, out
    assert "records no bundle membership" in out, out
    assert "NO_METAL not judged" in out, out

    # and it still judges: the same planted short is still caught
    nid = _net_id(routed)
    short_dir = tmp_path / "short"
    short_dir.mkdir()
    q = _mutated(routed, short_dir, "DELETE FROM bundle_net",
                 f"UPDATE net_segment SET x2=500 WHERE net_id={nid}")
    code, out = _judge(q)
    assert code == 1, out
    assert _kinds(out) == {"SHORT"}, out


def test_a_via_joins_two_wires_only_where_it_lands_on_both(vias, tmp_path):
    """A `net_via` row SAYS two segments are joined; whether they are is
    geometry.  Move one via off its own wires and the net it was holding
    together falls into two pieces — a judge that took the row's word for
    it would report the design connected."""
    con = sqlite3.connect(vias)
    assert con.execute("SELECT count(*) FROM net_via").fetchone()[0] > 0
    con.close()
    code, out = _judge(vias)
    assert code == 0, out                  # as routed

    p = _mutated(vias, tmp_path,
                 """UPDATE net_via SET x = x + 10000, y = y + 10000
                    WHERE rowid = (SELECT min(rowid) FROM net_via)""")
    code, out = _judge(p)
    assert code == 1, out
    assert "OPEN" in _kinds(out), out
    assert "disconnected pieces" in out, out


# ---------------------------------------------------------------------------
# Overlap is a property of two rectangles, not of what they claim
# ---------------------------------------------------------------------------

def test_a_short_under_a_wrong_orientation_is_still_caught(routed, tmp_path):
    """One wire with the WRONG `is_horiz`, overlapping another net's metal.

    Both faults are real and both must be named.  Bucketing or intersecting
    by each wire's own `along`/`perp` makes those a function of its declared
    orientation, so the pair lands in unrelated bins and the comparison that
    does happen is one wire's x-interval against the other's y-interval — a
    checkpoint could then report LAYER_DIR and hide the SHORT in the very
    same metal (Codex P2 on #942).

    `loc_0` stretched to x = 500 overlaps `x_0` (x 430..920, same track) by
    70 x 2 units of metal, whichever way either wire says it runs."""
    nid = _net_id(routed)
    p = _mutated(routed, tmp_path,
                 f"UPDATE net_segment SET x2=500, is_horiz=0 WHERE net_id={nid}")
    code, out = _judge(p)
    assert code == 1, out
    assert _kinds(out) == {"LAYER_DIR", "SHORT"}, out
    assert "two different nets on M6" in out and "x_0" in out, out


# ---------------------------------------------------------------------------
# Partial coverage is not a clean verdict
# ---------------------------------------------------------------------------

def test_metal_on_an_unpatterned_layer_cannot_be_judged(vias, tmp_path):
    """A checkpoint patterned on SOME of its routed layers.

    The whole-design guard passes (patterns exist), and if nothing else
    fires the tool would exit 0 with OFF_GRID entirely unevaluated for that
    layer's wires — a converge table reading `clean` for metal nothing
    judged (Codex P1 on #942).  Partial coverage is exactly what exit 2 is
    for, and the layer is NAMED so the reader knows which metal went
    unjudged."""
    con = sqlite3.connect(vias)
    layers = sorted({r[0] for r in con.execute("SELECT DISTINCT layer FROM net_segment")})
    con.close()
    assert len(layers) > 1, layers      # else the whole-design guard covers it
    victim = layers[0]

    p = _mutated(vias, tmp_path,
                 f"DELETE FROM track_pattern WHERE layer_id={victim}")
    code, out = _judge(p)
    assert code == 2, out
    assert f"M{victim}" in out and "no track pattern" in out, out
    assert "must not read as a clean one" in out, out


def test_an_unreadable_design_is_unjudgeable_not_dirty(tmp_path):
    """Exit 2, not 1.  A path that does not exist is a design this file
    cannot READ, and exit 1 is documented as violations — automation gating
    on that contract would book a typo'd path as a dirty route (Codex P2 on
    #942)."""
    code, out = _judge(tmp_path / "nope.bdb")
    assert code == 2, out
    assert "cannot judge" in out and "no such file" in out, out


def test_a_wire_inside_one_override_crosses_no_boundary(routed, tmp_path):
    """The note counts wires whose effective pattern CHANGES along them.

    A wire lying wholly inside one region override intersects that
    override, and counting intersections reported it as spanning a boundary
    it never reaches (Codex P3 on #942).  The first override covers the
    whole design, so EVERY wire is inside one and none crosses; the second
    ends at x = 200, under `loc_0`'s run (x 150..350), so some do.

    (The override also makes those wires OFF_GRID, since its pattern puts
    tracks elsewhere — true, and beside the point here: this test reads the
    note, not the verdict.)"""
    import json
    slots = json.dumps([{"t": "SIGNAL", "l": "", "w": 1.0, "s": 2.0}])
    inside = _mutated(
        routed, tmp_path,
        "INSERT INTO grid_override (layer_id,x1,y1,x2,y2,origin,slots) "
        f"VALUES (6,0,0,2000,400,0,'{slots}')")
    out_json = tmp_path / "in.json"
    code, out = _judge(inside, "--json", str(out_json))
    res = json.loads(out_json.read_text())
    assert res["notes"]["wires_crossing_a_region_override"] == 0, out

    # ...and a wire that genuinely leaves the region is still counted.
    d = tmp_path / "crossing"
    d.mkdir()
    crossing = _mutated(
        routed, d,
        "INSERT INTO grid_override (layer_id,x1,y1,x2,y2,origin,slots) "
        f"VALUES (6,0,0,200,300,0,'{slots}')")
    out_json = d / "cross.json"
    code, out = _judge(crossing, "--json", str(out_json))
    res = json.loads(out_json.read_text())
    assert res["notes"]["wires_crossing_a_region_override"] > 0, out
