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

"""`derive_cell_layer_shares` (convergence ladder item 4, rung 4).

The complement of the top's measured demand, per cell and layer, as
`set_cell_layer_share` lines — the budget handed DOWN derived from the
top's own plan instead of guessed.  What is worth pinning:

  * the share is the complement of the WORST instance (a template is solved
    once and copied, so the cell gets what its most crowded occurrence
    leaves), floored to a whole percent;
  * a layer the top does not touch gets no line, and a complement whose
    thinning keeps zero slots per period is skipped and said rather than
    handed to a command that refuses it;
  * `apply` declares through the command itself and `file` writes lines a
    fresh session can `source` BEFORE `run_planner hier` — the E1 recipe —
    and that session routes clean under them;
  * the collision count is honest: a share is a budget, the top's tracks
    are specific, and the number of the top's tracks inside the kept slots
    is reported rather than assumed away;
  * the share is FLOORED by the cell's own worst seat (subtree included,
    since the thinning covers the instance bbox) — E1 measured the pure
    complement stranding a cluster's own 32-bit buses — and `nofloor` is
    the study knob;
  * the derivation is the budget of every cell in scope: a scoped cell's
    share on a layer it emits no line for is removed by `apply` AND written
    as a `... 100` line by `file` (Codex P2 on #934, both halves — a session
    reopening the same BDB restores the persisted share before it sources
    the file), while a cell outside the scope keeps its shares.
"""
import contextlib
import io
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

from test_layer_demand import _DESIGN, _cmd, _quiet, _row  # noqa: E402


def _session(*extra):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN, *extra)
    return s


def test_the_share_is_the_complement_of_the_worst_instance():
    s = _session("run_nuts")
    lines, notes, _ = s._derive_cell_layer_shares()
    assert any("owning a cell-local bundle" in n for n in notes), notes
    assert [(l["cell"], l["layer_name"]) for l in lines] == [("top_cell", "M6")]
    l = lines[0]
    worst = max(_row(s._layer_demand(i, "M6"), i, "M6")["pct"]
                for i in ("u1", "u2"))
    assert l["pct"] == int(math.floor(100.0 - worst)), (l, worst)
    assert l["worst_pct"] == worst and l["n_inst"] == 2
    assert l["n_sig"] == 8 and l["kept"] == int(l["pct"] / 100.0 * 8 + 1e-9)
    out = _cmd(s, "derive_cell_layer_shares")
    assert f"set_cell_layer_share top_cell M6 {l['pct']}" in out, out
    assert "abstract bus tracks" in out


def test_apply_declares_and_a_sourced_file_routes_clean(tmp_path):
    """The E1 recipe: session 1 routes top-down and derives; session 2
    sources the lines before planning and routes under them."""
    s = _session("run_nuts", "run_detailed_nuts")
    path = tmp_path / "shares.buda"
    out = _cmd(s, f"derive_cell_layer_shares apply file {path}")
    assert "detailed bit tracks" in out and "applied 1 share(s)" in out, out
    lines, _, _ = s._derive_cell_layer_shares()
    pct = lines[0]["pct"]
    assert s._cell_layer_shares[("top_cell", 6)] == pct / 100.0
    text = path.read_text()
    assert f"set_cell_layer_share top_cell M6 {pct}" in text, text
    assert text.startswith("# derive_cell_layer_shares")
    # applying twice is the same declaration again, not a second one
    _cmd(s, "derive_cell_layer_shares apply")
    assert s._cell_layer_shares == {("top_cell", 6): pct / 100.0}
    # session 2: the lines govern the plan
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in _DESIGN[:-3] + [f"source {path}"] + _DESIGN[-3:]:
            s2.do_command(c)
    log = buf.getvalue()
    assert f"[LayerShare] top_cell: layer M6 share {pct}%" in log, log
    assert "carrying fractional shares" in log, log
    _quiet(s2, "run_nuts", "run_detailed_nuts")
    assert s2.nuts_result.num_overlaps == 0
    assert s2.detailed_result.num_unplaced == 0
    assert "Success" in _cmd(s2, "check_design")


def test_a_complement_below_one_slot_is_skipped_and_said():
    """The top leaving less than one slot per period is a band question, not
    a share: `set_cell_layer_share` would refuse it, so it is not emitted
    (`nofloor`: the pure complement).  WITH the own-need floor the same
    row yields the block's own minimum instead — the top is over-subscribed
    and the block still gets what its own buses need, said."""
    s = _session("run_nuts")
    real = s._layer_demand()
    for r in real:
        if r["inst"] == "u1" and r["layer_name"] == "M6":
            r["used"], r["pct"] = 45, 100.0 * 45 / r["supply"]
    s._layer_demand = lambda *_a, **_k: real
    lines, notes, _ = s._derive_cell_layer_shares(floor_own=False)
    assert lines == [], lines
    assert any("minimum meaningful share" in n and "set_cell_layer_cap" in n
               for n in notes), notes
    assert "nothing to declare" in _cmd(s, "derive_cell_layer_shares nofloor")
    lines, notes, _ = s._derive_cell_layer_shares()
    assert len(lines) == 1 and lines[0]["floored"], lines
    assert lines[0]["kept"] * 1.0 / lines[0]["n_sig"] >= lines[0]["own_pct"] / 100.0
    assert any("share floored" in n and "own bundle" in n for n in notes), notes


def test_the_share_is_floored_by_the_cells_own_seat():
    """E1's first measurement: the pure complement of the top's demand
    stranded a cluster's own 32-bit buses (35-track seats under a 71%
    share).  The derivation floors the share by the worst OWN seat over
    the cell's instances — including the subtree, since the thinned
    pattern is installed over the instance's bbox — and drops the line
    (full use, said) when the block needs every slot."""
    s = _session("run_nuts")
    rows = s._layer_demand()
    own = [r for r in rows if r["cell"] == "top_cell" and r["layer_name"] == "M6"]
    assert own and all(r["own_seat"] is not None for r in own), own
    # the cell's own 8-bit bus in its seat: a real fraction of the window
    assert all(0.0 < r["own_need"] <= 1.0 for r in own), own
    lines, notes, _ = s._derive_cell_layer_shares()
    l = [l for l in lines if l["layer_name"] == "M6"][0]
    assert l["kept"] * 1.0 / l["n_sig"] >= l["own_need"] if "own_need" in l \
        else l["kept"] * 1.0 / l["n_sig"] >= l["own_pct"] / 100.0
    # force the own need past every slot: no line, and the note names why
    for r in rows:
        if r["cell"] == "top_cell" and r["layer_name"] == "M6":
            r["own_need"] = 1.0
    s._layer_demand = lambda *_a, **_k: rows
    lines, notes, _ = s._derive_cell_layer_shares()
    assert not [l for l in lines if l["layer_name"] == "M6"], lines
    assert any("no share (full use)" in n and "positional reservation" in n
               for n in notes), notes
    # nofloor ignores it
    lines, _, _ = s._derive_cell_layer_shares(floor_own=False)
    assert [l for l in lines if l["layer_name"] == "M6"]


def test_the_own_need_is_the_admission_demand_not_the_bit_count():
    """A governed cell-local bus is admitted on its NDR GROUP DEMAND — a
    `width x2` bit pays two slots — so the floor must read that, not the
    bit count, or it derives a share the block's own routing cannot live
    under (Codex P2 on #935).  Same design, the cell-local `loc` buses
    under an x2 rule: the seat's need doubles while the bits do not, and
    it is `_seg_admission_need`'s number — the doomed-seat census's own
    arithmetic — that the row carries."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN[:i], "def_ndr w2 width x2", "set_ndr loc w2",
           *_DESIGN[i:], "run_nuts")
    rows = s._layer_demand()
    own = [r for r in rows if r["cell"] == "top_cell"
           and r["layer_name"] == "M6" and r["own_seat"] is not None]
    assert len(own) == 2, rows
    wm = {w.input.original_bundle.id: w for w in s.bundles}
    for r in own:
        bid, si, need, pool = r["own_seat"]
        w = wm[bid]
        sel = w.plan.selected_topology_index
        assert w.input.ndr.active()
        assert s._seg_member_bits(w, sel, si) == 8
        assert need == s._seg_admission_need(w, sel, si, layer=r["layer"]) == 16
        ts = next(t for t in s.nuts_result.segments
                  if t.bundle_id == bid and t.seg_idx == si)
        assert pool == s._seg_admission_pool(
            ts, s.routing_grid.get_layer_grid(r["layer"]),
            s._seg_admission_need(w, sel, si, credited=False, layer=r["layer"]))
        assert r["own_need"] == min(1.0, 16 / pool), r
    # the ungoverned design reads the bit count: the identity, by construction
    s0 = _session("run_nuts")
    for r in s0._layer_demand():
        if r["cell"] == "top_cell" and r["layer_name"] == "M6":
            assert r["own_seat"][2] == 8, r


def test_the_file_names_its_whole_scope_not_just_the_cells_with_a_line(tmp_path):
    """A driver re-deriving next round pins the SAME scope; a cell the top
    took nothing over this round has no line, so the file's `# scope:`
    header is where the scope lives (Codex P2 on #935)."""
    s = _session("run_nuts")
    path = tmp_path / "shares.buda"
    _cmd(s, f"derive_cell_layer_shares cells top_cell,leaf file {path}")
    lines, _, scope = s._derive_cell_layer_shares(["top_cell", "leaf"])
    assert scope == ["top_cell", "leaf"]
    with_line = {l["cell"] for l in lines}
    assert "leaf" not in with_line, lines          # in scope, no line
    hdr = [ln for ln in path.read_text().splitlines() if ln.startswith("# scope:")]
    assert hdr == ["# scope: top_cell,leaf"], path.read_text()
    # an empty scope says so rather than leaving the header out
    _cmd(s, f"derive_cell_layer_shares cells nosuch file {path}")
    assert "# scope: (none)" in path.read_text()


def test_a_floor_that_rounds_to_100_percent_is_full_use_not_a_share():
    """The share is declared in WHOLE percent and `set_cell_layer_share
    ... 100` REMOVES a share, so on a pattern with more than 100 SIGNAL
    slots an own need of 127/128 rounded to a 100% line that would have
    run the next session UNRESTRICTED while reporting a 127/128
    reservation (Codex P2 on #935).  Such a floor is full use, said.  And
    a floor that does round to a share reports the slot count the
    DECLARED percent keeps: 50 of 128 rounds up to 40%, which keeps 51."""
    s = _session("run_nuts")
    _quiet(s, "def_layer 9 M9 H TOP 20",
           "def_track_pattern 9 0 VDD 2 1 (_ 1 1)x128 GND 2 1")
    base = [dict(r) for r in s._layer_demand()
            if r["cell"] == "top_cell" and r["layer_name"] == "M6"]
    assert len(base) == 2

    def rows(worst_pct, need, pool):
        out = []
        for r in base:
            r = dict(r, layer=9, layer_name="M9", pct=worst_pct, used=10,
                     supply=128, used_tracks=[], own_need=need / pool,
                     own_seat=(4, 0, need, pool))
            out.append(r)
        return out

    s._layer_demand = lambda *_a, **_k: rows(10.0, 127, 128)
    lines, notes, _ = s._derive_cell_layer_shares(["top_cell"])
    assert not [l for l in lines if l["layer"] == 9], lines
    assert any("127 of 128 slots" in n and "no share (full use)" in n
               for n in notes), notes
    # the complement alone would have been 90%: the floor is what said no
    lines, _, _ = s._derive_cell_layer_shares(["top_cell"], floor_own=False)
    assert [l["pct"] for l in lines if l["layer"] == 9] == [90]
    # a floor that IS a share reports what the declared percent keeps
    s._layer_demand = lambda *_a, **_k: rows(80.0, 50, 128)
    lines, notes, _ = s._derive_cell_layer_shares(["top_cell"])
    l = [l for l in lines if l["layer"] == 9][0]
    assert (l["pct"], l["kept"], l["n_sig"], l["floored"]) == (40, 51, 128, True), l
    assert l["kept"] == int(l["pct"] / 100.0 * 128 + 1e-9)   # the command's count
    assert any("share floored 20% -> 40% (51/128 slots)" in n for n in notes), notes


def test_scope_defaults_to_the_bottom_up_marks_and_cells_narrows():
    s = _session("set_bottom_up top_cell", "run_nuts")
    lines, notes, _ = s._derive_cell_layer_shares()
    assert any("marked set_bottom_up" in n and "top_cell" in n for n in notes)
    assert len(lines) == 1
    lines, notes, scope = s._derive_cell_layer_shares(["leaf", "nosuch"])
    assert lines == [] and any("'nosuch'" in n for n in notes), notes
    assert "usage" in _cmd(s, "derive_cell_layer_shares bogus")


def test_the_collision_count_is_the_tops_tracks_inside_the_kept_slots():
    s = _session("run_nuts", "run_detailed_nuts")
    l = s._derive_cell_layer_shares()[0][0]
    pat = s.routing_grid.get_layer_grid(6).global_pattern()
    tp = s._thinned_pattern(pat, l["pct"] / 100.0)
    comps = {c.name: c for c in s.bdb.all_components()}
    expect = 0
    for inst in ("u1", "u2"):
        c = comps[inst]
        kept = [p for p, sl in tp.tracks_in_range(c.y1, c.y2)
                if sl.type == "SIGNAL"]
        used = _row(s._layer_demand(inst, "M6"), inst, "M6")["used_tracks"]
        expect = max(expect, sum(1 for u in used
                                 if any(abs(u - k) < 1e-6 for k in kept)))
    assert l["collide"] == expect and 0 <= expect <= l["worst_used"], l


def test_without_a_nuts_result_it_says_so():
    s = _session()
    assert s._derive_cell_layer_shares() is None
    out = _cmd(s, "derive_cell_layer_shares")
    assert "Error" in out and "run_nuts" in out, out


def test_an_empty_derivation_still_rewrites_the_file(tmp_path):
    """A later session sources the file, so a run that derives nothing must
    not leave an earlier run's lines in it (Codex P2 on #934): the file is
    rewritten header-only, and sourcing it declares nothing."""
    s = _session("run_nuts")
    path = tmp_path / "shares.buda"
    path.write_text("set_cell_layer_share top_cell M6 50\n")   # stale
    out = _cmd(s, f"derive_cell_layer_shares cells leaf file {path}")
    assert "nothing to declare" in out and "header only" in out, out
    text = path.read_text()
    assert "set_cell_layer_share" not in text, text
    assert text.startswith("# derive_cell_layer_shares"), text
    s2 = _session()
    _quiet(s2, f"source {path}")
    assert not getattr(s2, "_cell_layer_shares", None)


def test_a_quoted_spaced_path_is_one_path(tmp_path):
    """`file "results run/shares.buda"` is one path under the repository's
    quoted-path convention; the handler read the whitespace split and took
    `"results` as the path (Codex P2 on #934)."""
    s = _session("run_nuts")
    d = tmp_path / "results run"
    d.mkdir()
    path = d / "shares.buda"
    out = _cmd(s, f'derive_cell_layer_shares file "{path}"')
    assert "written to" in out and "usage" not in out, out
    assert "set_cell_layer_share top_cell M6" in path.read_text()


def test_apply_removes_a_scoped_share_the_derivation_no_longer_emits():
    """`apply` after the demand changed used to update only the emitted
    lines, so a share held from an earlier declaration on a layer the top
    no longer touches (or whose complement now keeps zero slots) kept
    constraining the next plan (Codex P2 on #934).  A scoped cell's stale
    share is removed through the command's own pct-100 path — session AND
    BDB — while a cell outside the scope keeps its share."""
    s = _session("run_nuts")
    _quiet(s, "set_cell_layer_share leaf M6 50",       # in scope, no line
           "set_cell_layer_share top_cell M5 50")      # out of scope
    assert ("leaf", 6) in s._cell_layer_shares
    assert s.bdb.cell_layer_shares("leaf") == [(6, 0.5)]   # persisted
    out = _cmd(s, "derive_cell_layer_shares cells leaf apply")
    assert "nothing to declare" in out, out
    assert "leaf M6: share 50% held from an earlier declaration" in out, out
    assert "removed 1 stale share(s) in scope" in out, out
    assert ("leaf", 6) not in s._cell_layer_shares
    assert s._cell_layer_shares == {("top_cell", 5): 0.5}
    assert s.bdb.cell_layer_shares("leaf") == []
    assert s.bdb.cell_layer_shares("top_cell") == [(5, 0.5)]
    # the scope with lines: the emitted one is declared, a stale one on
    # ANOTHER layer of the same cell goes, and re-applying is a no-op
    _quiet(s, "set_cell_layer_share top_cell M4 50")
    out = _cmd(s, "derive_cell_layer_shares cells top_cell apply")
    assert "applied 1 share(s) to this session; removed 2 stale share(s)" \
        in out, out
    assert set(s._cell_layer_shares) == {("top_cell", 6)}
    out = _cmd(s, "derive_cell_layer_shares cells top_cell apply")
    assert "applied 1 share(s) to this session" in out and "stale" not in out


def test_the_file_carries_the_removal_of_a_stale_scoped_share(tmp_path):
    """The file half of the stale-share rule (Codex P2 on #934, round 4):
    a later session that opens the SAME BDB restores the persisted shares
    before it sources the file, so the new lines alone would leave a share
    the top no longer supports in force.  The file writes a `... 100` line
    for every scoped share the derivation did not emit, and sourcing it
    into a session holding that share removes it — session and BDB."""
    s = _session("run_nuts")
    _quiet(s, "set_cell_layer_share leaf M6 50",       # in scope, no line
           "set_cell_layer_share top_cell M5 50")      # out of scope
    path = tmp_path / "shares.buda"
    out = _cmd(s, f"derive_cell_layer_shares cells leaf file {path}")
    assert "header only" in out and "1 removal line(s)" in out, out
    text = path.read_text()
    assert "set_cell_layer_share leaf M6 100" in text, text
    assert "top_cell" not in text, text
    # the writer did NOT touch this session (no apply)
    assert s._cell_layer_shares[("leaf", 6)] == 0.5
    # a session holding the stale share, as a reopened BDB would: sourcing
    # the file removes it and leaves the out-of-scope one alone
    s2 = _session("set_cell_layer_share leaf M6 50",
                  "set_cell_layer_share top_cell M5 50")
    log = _cmd(s2, f"source {path}")
    assert "share 100% — explicit full use (share removed)" in log, log
    assert s2._cell_layer_shares == {("top_cell", 5): 0.5}
    assert s2.bdb.cell_layer_shares("leaf") == []
    # with lines: the emitted line and the removal of the OTHER layer's
    # stale share both land in the file
    _quiet(s, "set_cell_layer_share top_cell M4 50")
    _cmd(s, f"derive_cell_layer_shares cells top_cell file {path}")
    text = path.read_text()
    assert "set_cell_layer_share top_cell M6 " in text, text
    assert "set_cell_layer_share top_cell M4 100" in text, text
    assert "set_cell_layer_share top_cell M5 100" in text, text
    assert "leaf" not in text, text


def test_marks_on_instance_less_cells_keep_an_empty_scope():
    """`set_bottom_up` accepts a defined cell with no instance; a mark like
    that used to be filtered out and the scope fell through to the
    bundle-owning cells, so `apply` derived — and removed — shares for
    cells the marked scope never named (Codex P2 on #934).  The marks
    decide the rung; an all-unplaced set is an empty scope, said."""
    s = _session("add_cell orphan 10 10", "set_bottom_up orphan", "run_nuts",
                 "set_cell_layer_share top_cell M5 50")   # not in scope
    lines, notes, scope = s._derive_cell_layer_shares()
    assert scope == [] and lines == [], (scope, lines)
    assert any("marked set_bottom_up" in n and "0 cell(s)" in n
               for n in notes), notes
    assert any("marked cell 'orphan': no placed instance" in n
               for n in notes), notes
    out = _cmd(s, "derive_cell_layer_shares apply")
    assert "nothing to declare" in out and "stale" not in out, out
    assert s._cell_layer_shares == {("top_cell", 5): 0.5}   # untouched
    # an explicit `cells` still overrides the marks
    lines, _, scope = s._derive_cell_layer_shares(["top_cell"])
    assert scope == ["top_cell"] and len(lines) == 1


def test_an_empty_cells_argument_is_refused_not_widened():
    """`cells ""` / `cells ,` name no cell; read as an omitted option the
    scope silently widened to the default rungs, which `apply` then
    replaced shares of (Codex P2 on #934).  The handler refuses it, and the
    API keeps an explicit empty list as an empty scope."""
    s = _session("run_nuts", "set_cell_layer_share top_cell M5 50")
    for arg in ('cells ""', "cells ,"):
        out = _cmd(s, f"derive_cell_layer_shares {arg} apply")
        assert "Error: derive_cell_layer_shares: `cells` names no cell" \
            in out, out
        assert "===" not in out and "applied" not in out, out
    assert s._cell_layer_shares == {("top_cell", 5): 0.5}   # untouched
    lines, notes, scope = s._derive_cell_layer_shares([])
    assert scope == [] and lines == []
    assert any("0 cell(s) (named)" in n for n in notes), notes
