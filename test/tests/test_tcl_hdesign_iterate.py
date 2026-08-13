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

"""`hdesign.tcl` — the HIERARCHICAL one-file iteration vehicle.

`design.tcl` proved the loop on a flat design; this is the same loop on
flow/hbundles/08_cross_level.buda's THREE-level design (chip/blk/leaf, with
cell-local templates, same-level D0/D1 bundles, and seven cross-level buses)
plus the planning options a hierarchy adds: -mode topdown|bottomup|hybrid,
-reserve N (reserve_top_layers), -share cell=layer=pct.

What is pinned here, in separate `tclsh` processes: build; pin the D2
TEMPLATE at the prompt (one pin -> all four blk instances, resolved through
the expansion map); resume with NOTHING specified and find the pin still in
force on every instance; the -mode build-premise guard; the bottom-up mode
ending clean under `on_mismatch independent`; and the -reserve/-share
policies resolving onto the governed bundles.
"""
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "flow" / "tcl" / "hdesign.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _run(tmp_path, *args, stdin="done\n", **env):
    import os
    e = {"BUDA_HDESIGN_BDB": str(tmp_path / "it.bdb"), **env}
    return subprocess.run(["tclsh", str(_SCRIPT), *map(str, args)],
                          input=stdin, capture_output=True, encoding="utf-8",
                          errors="replace", cwd=tmp_path, timeout=900,
                          env={**os.environ, **e})


def _template_pin(bdb):
    """(bundle_id, cand_index) of the pinned PRE-expansion blk_cell template."""
    c = sqlite3.connect(str(bdb))
    try:
        return c.execute(
            "select t.bundle_id, t.cand_index from topology t join bundle b "
            "on b.id = t.bundle_id where t.is_pinned=1 and b.is_expanded=0 "
            "and b.cell_context='blk_cell'").fetchall()
    finally:
        c.close()


def _pre_expansion_ids(bdb):
    c = sqlite3.connect(str(bdb))
    try:
        return {r[0] for r in c.execute(
            "select id from bundle where is_expanded=0")}
    finally:
        c.close()


def test_the_hier_loop_pins_a_template_across_sessions(tmp_path):
    """One pin on the D2 template re-routes all four blk instances, and the
    choice survives into a session that specifies nothing."""
    bdb = tmp_path / "it.bdb"

    # 1. BUILD: 08's design, 14 bundles, clean.
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUILT 14 bundles (-mode topdown)" in r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert bdb.exists() and (tmp_path / "it.bdb.sql").exists()
    orig_ids = _pre_expansion_ids(bdb)
    assert len(orig_ids) == 14, orig_ids

    # 2. RESUME + pin the TEMPLATE by net-name hint at the prompt.  The hint
    #    resolves through the expansion map; `done` re-plans (coherence).
    r = _run(tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED 14 bundles" in r.stdout
    assert "expanded instances" in r.stdout      # the map fanned the pin out
    assert "pins changed since the last route" in r.stdout
    assert r.stdout.count("topo 2 of 5") >= 4, "not all instances pinned"
    assert "clean -- 0 overlaps" in r.stdout

    # The checkpoint must still hold the PRE-expansion view — the persist
    # from a post-expansion session must not clobber template/replica rows
    # with synthetic-id instance rows (the hdesign checkpoint-clobber).
    assert _pre_expansion_ids(bdb) == orig_ids
    pins = _template_pin(bdb)
    assert pins and all(ci == 1 for _b, ci in pins), pins   # 1-based 2

    # 3. Nothing specified: the choice is the checkpoint's now.
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED 14 bundles" in r.stdout
    assert r.stdout.count("topo 2 of 5") >= 4, "the pin was not honored"
    assert "clean -- 0 overlaps" in r.stdout


def test_mode_is_a_build_premise_a_resume_cannot_change(tmp_path):
    """Alignment happens before busterms are derived, so -mode binds at
    build; a resume with a different one is refused with the recipe."""
    assert _run(tmp_path, "-mode", "hybrid").returncode == 0
    r = _run(tmp_path, "-mode", "topdown")
    assert r.returncode != 0
    assert "cannot change" in r.stdout + r.stderr
    assert "BUDA_HDESIGN_FRESH=1" in r.stdout + r.stderr
    # Same mode resumes fine; FRESH really rebuilds under the new mode.
    assert _run(tmp_path, "-mode", "hybrid").returncode == 0
    r = _run(tmp_path, "-mode", "topdown", BUDA_HDESIGN_FRESH="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUILT 14 bundles (-mode topdown)" in r.stdout


def test_bottom_up_mode_ends_clean_under_independent_policy(tmp_path):
    """08's fixed geometry cannot fully phase-align (the x offsets defeat
    the LCM period and the y nudges revert on parent overlap), so the modes
    declare `check_template_tracks on_mismatch independent`: aligned
    instances copy the template's solve, the rest solve individually — and
    the flow still ends clean."""
    r = _run(tmp_path, "-mode", "bottomup")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "set_bottom_up * on" in r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_reserve_and_share_resolve_onto_the_governed_bundles(tmp_path):
    """-reserve N keeps the top N layers for the TOP level (cells capped
    just under), and -share leases a slice of a reserved layer back — the
    wiring-limited escape valve.  Both must resolve and end clean."""
    r = _run(tmp_path, "-reserve", "2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reserving the top 2 layer(s) (M6, M7)" in r.stdout
    assert "governed by cell layer policies" in r.stdout
    assert "clean -- 0 overlaps" in r.stdout

    r = _run(tmp_path, "-reserve", "2", "-share", "blk_cell=M6=50",
             BUDA_HDESIGN_FRESH="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "layer M6 share 50%" in r.stdout
    assert "clean -- 0 overlaps" in r.stdout
