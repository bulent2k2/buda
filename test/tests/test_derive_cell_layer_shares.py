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
    is reported rather than assumed away.
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
    lines, notes = s._derive_cell_layer_shares()
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
    lines, _ = s._derive_cell_layer_shares()
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
    a share: `set_cell_layer_share` would refuse it, so it is not emitted."""
    s = _session("run_nuts")
    real = s._layer_demand()
    for r in real:
        if r["inst"] == "u1" and r["layer_name"] == "M6":
            r["used"], r["pct"] = 45, 100.0 * 45 / r["supply"]
    s._layer_demand = lambda *_a, **_k: real
    lines, notes = s._derive_cell_layer_shares()
    assert lines == [], lines
    assert any("minimum meaningful share" in n and "set_cell_layer_cap" in n
               for n in notes), notes
    assert "nothing to declare" in _cmd(s, "derive_cell_layer_shares")


def test_scope_defaults_to_the_bottom_up_marks_and_cells_narrows():
    s = _session("set_bottom_up top_cell", "run_nuts")
    lines, notes = s._derive_cell_layer_shares()
    assert any("marked set_bottom_up" in n and "top_cell" in n for n in notes)
    assert len(lines) == 1
    lines, notes = s._derive_cell_layer_shares(["leaf", "nosuch"])
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
