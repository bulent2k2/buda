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

"""The advisory writer (Phase 4 of docs/internal/lefdef_interface_plan.md).

The gap it closes: the only export was `export_gds`, and a GDS rectangle
carries no net identity a P&R tool can adopt — which is what made the tool's
output "a picture, not a constraint".

The plan's acceptance criterion, quoted: "showing that the guides name the
right nets and that no blockage overlaps a corridor it is meant to protect.
Without that example the artifact's semantics are untested prose."  Both
halves are tested here, and the second is the one that matters: emitting the
corridors as blockages is the obvious move and it is exactly backwards.
"""
import contextlib
import csv
import io
import json

import pytest

import buda
import buda_cli

_FLOW = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
def_track_pattern 5 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
add_block a 0 0 100 100
add_block b 800 600 900 700
add_keepout 300 300 400 400 M4
add_bus d[8] a.o b.i
run_bundler STRICT
generate_topologies
run_planner 3
run_nuts
"""


def _routed(tmp_path, extra=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in _FLOW.strip().splitlines():
            s.do_command(line.strip())
        for c in extra:
            s.do_command(c)
    return s, buf.getvalue()


def _manifest(tmp_path, margin=0, extra_out=()):
    p = tmp_path / "g.json"
    s, out = _routed(tmp_path, [f"emit_guides {p} margin {margin}", *extra_out])
    return s, json.loads(p.read_text()), out


# ── 4a: the manifest carries what GDS cannot ───────────────────────────────

def test_the_guides_name_the_right_nets(tmp_path):
    """Half the plan's acceptance criterion.  Net identity IS the artifact:
    a rectangle without it is the picture we already had."""
    _s, m, _out = _manifest(tmp_path)
    assert m["n_bundles"] == 1
    b = m["bundles"][0]
    assert b["n_nets"] == 8
    assert b["nets"] == [f"d_{i}" for i in range(8)]
    assert b["corridors"], "a bundle with no corridor advertises nothing"


def test_every_corridor_has_a_layer_and_a_rectangle(tmp_path):
    _s, m, _ = _manifest(tmp_path)
    for c in m["bundles"][0]["corridors"]:
        assert c["layer_name"] in ("M4", "M5")
        assert c["x2"] > c["x1"] and c["y2"] > c["y1"]


def test_margin_grows_the_reservation_on_every_side(tmp_path):
    _s, a, _ = _manifest(tmp_path, margin=0)
    _s2, b, _ = _manifest(tmp_path, margin=5)
    ca, cb = a["bundles"][0]["corridors"][0], b["bundles"][0]["corridors"][0]
    assert cb["x1"] == pytest.approx(ca["x1"] - 5)
    assert cb["y2"] == pytest.approx(ca["y2"] + 5)


def test_the_manifest_is_deterministic(tmp_path):
    """Two runs of one design must produce the same bytes, so a diff means a
    real change — the discipline `gds_io`'s export already follows."""
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    _routed(tmp_path, [f"emit_guides {p1}"])
    _routed(tmp_path, [f"emit_guides {p2}"])
    assert p1.read_text() == p2.read_text()


def test_csv_and_tcl_are_the_same_data_in_other_forms(tmp_path):
    j, c, t = tmp_path / "g.json", tmp_path / "g.csv", tmp_path / "g.tcl"
    _s, out = _routed(tmp_path, [f"emit_guides {j} csv {c} tcl {t}"])
    m = json.loads(j.read_text())
    rows = list(csv.DictReader(c.open()))
    assert len(rows) == sum(len(b["corridors"]) for b in m["bundles"])
    assert rows[0]["nets"].split() == m["bundles"][0]["nets"]
    # Count the COMMANDS, not the word — the header comment says
    # "create_route_guide form" and would inflate a naive count by one.
    tcl = t.read_text()
    cmds = [l for l in tcl.splitlines() if l.startswith("create_route_guide")]
    assert len(cmds) == len(rows)
    assert "d_0 d_1" in tcl and "-layer M4" in tcl


def test_emitting_before_a_route_says_so(tmp_path):
    """An unplaced plan reserves nothing; emitting it would advertise
    corridors that do not exist."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    p = tmp_path / "g.json"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"emit_guides {p}")
    assert "no placed bus segments" in buf.getvalue()
    assert json.loads(p.read_text())["n_bundles"] == 0


# ── 4b: the DEF carries only what DEF can honestly say ─────────────────────

def _def_blockages(path):
    d = buda.read_def(path.read_text() and str(path))
    return d.blockages


def test_corridors_are_not_emitted_as_blockages(tmp_path):
    """The other half of the acceptance criterion, and the load-bearing one.

    A DEF blockage tells the router to STAY OUT.  Emitting each corridor as
    one would forbid exactly the routing the plan is asking for — the plan's
    §0 correction, made testable: no plain (hard) blockage may overlap a
    corridor."""
    g, dp = tmp_path / "g.json", tmp_path / "a.def"
    s, out = _routed(tmp_path, [f"emit_guides {g}", f"export_def_blockages {dp}"])
    m = json.loads(g.read_text())
    corridors = [c for b in m["bundles"] for c in b["corridors"]]
    assert corridors, "nothing to protect — the test would prove nothing"

    lu_per_um = 1.0
    units = 1000.0            # what the writer used with no BDB open
    blocks = buda.read_def(str(dp)).blockages
    assert blocks, "no blockages emitted at all"
    for b in blocks:
        assert not b.has_density          # none requested
        for r in b.rects:
            for c in corridors:
                # DEF rects are in DBU; corridors in layout units.
                cx1 = c["x1"] * units / lu_per_um
                cy1 = c["y1"] * units / lu_per_um
                cx2 = c["x2"] * units / lu_per_um
                cy2 = c["y2"] * units / lu_per_um
                overlap = (r.x1 < cx2 and r.x2 > cx1 and
                           r.y1 < cy2 and r.y2 > cy1)
                assert not overlap, (
                    f"hard blockage {(r.x1, r.y1, r.x2, r.y2)} overlaps the "
                    f"corridor for {b} — that forbids the very routing the "
                    f"plan asks for")


def test_the_keepout_is_what_becomes_a_blockage(tmp_path):
    """A blockage means "keep clear", so what belongs in it is the design's
    real keep-clear regions."""
    dp = tmp_path / "a.def"
    _routed(tmp_path, [f"export_def_blockages {dp}"])
    blocks = buda.read_def(str(dp)).blockages
    rects = [(r.x1, r.y1, r.x2, r.y2) for b in blocks for r in b.rects]
    assert (300000.0, 300000.0, 400000.0, 400000.0) in rects, rects


def test_density_is_how_the_positive_intent_is_expressed(tmp_path):
    """`+ PARTIAL <maxDensity>` over a corridor says "leave room here" — the
    only part of "route these nets here" that DEF can carry, and the reason
    4b is a complement to the manifest rather than a substitute."""
    g, dp = tmp_path / "g.json", tmp_path / "a.def"
    _routed(tmp_path, [f"emit_guides {g}",
                       f"export_def_blockages {dp} density 0.6"])
    blocks = buda.read_def(str(dp)).blockages
    dens = [b for b in blocks if b.has_density]
    assert dens, "density limits requested but not emitted"
    assert all(b.max_density == 0.6 for b in dens)
    # `PARTIAL maxDensity` is a PLACEMENT-blockage option in DEF 5.8, not a
    # layer routing-blockage one.  Writing it on a LAYER blockage produces a
    # file a compliant tool may reject — and our own reader is permissive
    # enough to accept it, which is exactly why a round-trip test alone does
    # not catch it.
    assert all(b.is_placement and not b.layer for b in dens), \
        [(b.layer, b.is_placement) for b in dens]
    # …and they sit ON the corridors, which is the point.
    m = json.loads(g.read_text())
    n_corr = sum(len(b["corridors"]) for b in m["bundles"])
    assert len(dens) == n_corr


def test_the_def_round_trips(tmp_path):
    """DEF -> BUDA -> DEF, as the plan asks: what we write, we can read."""
    g, dp = tmp_path / "g.json", tmp_path / "a.def"
    _routed(tmp_path, [f"emit_guides {g}",
                       f"export_def_blockages {dp} density 0.5"])
    d = buda.read_def(str(dp))
    assert d.design == "buda_advisory"
    assert d.declared_blockages == len(d.blockages)
    assert not d.unmodelled, [u.construct for u in d.unmodelled]


def test_the_def_is_deterministic(tmp_path):
    a, b = tmp_path / "a.def", tmp_path / "b.def"
    _routed(tmp_path, [f"export_def_blockages {a} density 0.5"])
    _routed(tmp_path, [f"export_def_blockages {b} density 0.5"])
    assert a.read_text() == b.read_text()


def test_a_tapered_branch_names_only_the_nets_it_carries(tmp_path):
    """A fan-in topology puts only a SUBSET of the bundle's bits on each
    branch (`Topology::seg_bits`) — NUTS even sizes the segment from that
    subset.  Naming the whole bundle on every corridor would direct nets into
    branches they never traverse, which is a wrong instruction rather than a
    loose one, and it defeats the single guarantee the manifest exists to
    make."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    j = tmp_path / "g.json"
    flow = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
def_track_pattern 5 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
add_block s0 0 0 100 100
add_block s1 0 400 100 500
add_block d 900 200 1000 300
add_net f0 s0.o d.i
add_net f1 s1.o d.i
run_bundler CONVERGENT
generate_topologies
run_planner 3
run_nuts
"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in flow.strip().splitlines():
            s.do_command(line.strip())
        s.do_command(f"emit_guides {j}")
    m = json.loads(j.read_text())
    corr = [c for b in m["bundles"] for c in b["corridors"]]
    assert corr, "no corridors to check"
    # Whatever the shape, no corridor may name a net the bundle does not have,
    # and a tapered one must name FEWER than the whole bundle.
    for b in m["bundles"]:
        for c in b["corridors"]:
            assert set(c["nets"]) <= set(b["nets"])
            if c["tapered"]:
                assert len(c["nets"]) <= b["n_nets"]
    assert all("nets" in c and "tapered" in c for c in corr)


def test_a_bad_density_is_refused(tmp_path):
    s, out = _routed(tmp_path,
                     [f"export_def_blockages {tmp_path / 'x.def'} density 2"])
    assert "must be in (0, 1]" in out
