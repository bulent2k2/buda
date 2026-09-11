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

"""`corridor_notch_check.py` -- and mostly its REFUSALS.

The answer this tool gives is a zero, which is the same shape a broken
instrument produces.  That is not hypothetical in this tree: #905 was opened
on a "pdngen made none of 512" that was a reporting defect, and a strap walk
written while chasing it found zero straps and reported every via as having
none on either side, which read as confirmation.  So the tests that matter
are the ones proving a zero cannot be printed when nothing was measured.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_T1A = Path(__file__).resolve().parents[2] / "flow" / "librelane" / "tier1a"
_TOOL = _T1A / "corridor_notch_check.py"

# One instance of one cell.  The macro is 100 x 100 at (0, 0); the notch piece
# sits INSIDE it, and the corridor runs in the channel to its right -- the real
# shape (BUDA plans corridors between the blocks; the notch patches metal
# within them), so the honest answer is a zero on a layer that could have met.
PLACEMENT = {
    "design": "top", "dbu": 1000, "divider": "/", "die_um": [0.0, 0.0, 600.0, 700.0],
    "instances": [{"def_name": "u1", "name": "u1", "cell": "pe_cell",
                   "def_location": [0.0, 0.0], "x": 0.0, "y": 0.0,
                   "orient": "N", "size": [100.0, 100.0]}],
}
NOTCH = {"cell": "pe_cell", "layer": "met2", "uncovered": [[10.0, 10.0, 10.5, 10.5]]}


def _guides(layer="met2", x1=200000, x2=220000):
    return {"tool": "buda", "artifact": "corridor_manifest", "bundles": [
        {"bundle": 1, "nets": ["n[0]"], "corridors": [
            {"seg": 0, "layer": 2, "layer_name": layer,
             "x1": x1, "y1": 0, "x2": x2, "y2": 100000, "nets": ["n[0]"]}]}]}


def _arm(tmp_path, guides=None, notch=NOTCH, run_tag="h", placement=PLACEMENT,
         cells=("pe_cell",), macros=None, extra_layers=()):
    """An arm directory: top/{placement,config}.json, the guides, and a patched
    LEF plus its notch JSON per cell.  `config.json` matters -- the tool
    resolves which hardening run to read from `MACROS.<cell>.lef` rather than
    globbing, so the fixture has to name it the way harm.py does."""
    a = tmp_path / "arm"
    (a / "top" / "out").mkdir(parents=True)
    (a / "top" / "placement.json").write_text(json.dumps(placement))
    g = guides or _guides()
    (a / "top" / "out" / "buda_guides.json").write_text(json.dumps(g))
    # `buda_bus.guide` is what `read_guides` consumes and what the checker reads;
    # the manifest beside it is only cross-checked (#925).
    lines = []
    for b in g.get("bundles", []):
        for i, c in enumerate(b["corridors"]):
            lines.append("%s_%d\n(\n%d %d %d %d %s\n)\n" % (
                b["nets"][0].replace("[", "\\[").replace("]", "\\]"), i,
                c["x1"], c["y1"], c["x2"], c["y2"], c["layer_name"]))
    (a / "top" / "out" / "buda_bus.guide").write_text("".join(lines))
    m = {}
    for cell in cells:
        d = a / cell / "runs" / run_tag / "final" / "lef"
        d.mkdir(parents=True)
        (d / ("%s.notch.lef" % cell)).write_text("MACRO %s\nEND %s\n" % (cell, cell))
        m[cell] = {"lef": ["dir::../%s/runs/%s/final/lef/%s.notch.lef" % (cell, run_tag, cell)]}
        if notch is not None:
            (d / ("%s.notch.met2.json" % cell)).write_text(json.dumps(notch))
            for lay in extra_layers:
                (d / ("%s.notch.%s.json" % (cell, lay))).write_text(
                    json.dumps({"cell": cell, "layer": lay, "uncovered": [[1.0, 1.0, 1.5, 1.5]]}))
            # the provenance notch.sh writes beside the LEF on its success path
            (d / ("%s.notch.layers" % cell)).write_text(
                " ".join(["met2", *extra_layers]) + "\n")
    (a / "top" / "config.json").write_text(json.dumps({"MACROS": macros if macros is not None else m}))
    return a


def _run(arm, *args):
    return subprocess.run([sys.executable, str(_TOOL), str(arm), *args],
                          capture_output=True, text=True)


# ── the refusals: a zero must be unreachable when nothing was measured ──────

def test_it_refuses_when_there_is_no_notch_json_at_all(tmp_path):
    """The blocker.  An arm with guides and placement but no notch JSON used to
    print `VERDICT: INERT` and, worse, `the check was live: True` -- the
    liveness line keyed on macro boxes rather than on pieces (#913)."""
    r = _run(_arm(tmp_path, notch=None))
    assert r.returncode == 1, r.stdout
    # the provenance refusal fires first now, and is the sharper message: the
    # LEF was never built, so nothing records what it was built from
    assert "REFUSING" in r.stderr and "notch.layers" in r.stderr, r.stderr
    assert "INERT" not in r.stdout, r.stdout


def test_it_refuses_when_every_uncovered_list_is_empty(tmp_path):
    """The other door to a zero with complete coverage: the JSON is present for
    every cell and every layer, and holds no rectangle.  `notch_obs.py` writes
    that for a cell it found nothing to patch, so this is a real state, and a
    verdict from it would be arithmetic rather than measurement."""
    r = _run(_arm(tmp_path, notch={"cell": "pe_cell", "layer": "met2", "uncovered": []}))
    assert r.returncode == 1, r.stdout
    assert "REFUSING" in r.stderr and "0 notch pieces" in r.stderr, r.stderr
    assert "INERT" not in r.stdout, r.stdout


def test_it_refuses_when_the_manifest_has_no_corridor(tmp_path):
    r = _run(_arm(tmp_path, guides={"bundles": []}))
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "0 corridors" in r.stderr, r.stderr
    assert "INERT" not in r.stdout


def test_it_refuses_when_the_two_sets_share_no_layer(tmp_path):
    """Corridors on met3, notch metal on met2: they cannot meet on geometry, so
    a zero is arithmetic rather than evidence."""
    r = _run(_arm(tmp_path, guides=_guides(layer="met3")))
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "No shared layer" in r.stderr, r.stderr


def test_it_refuses_a_rotated_instance_rather_than_misplacing_the_pieces(tmp_path):
    """`placement.json` carries DEF orientation tokens and only the identity
    transform is implemented; the BDB table differs on all four flips, so
    guessing would put the notch metal somewhere it is not."""
    pl = json.loads(json.dumps(PLACEMENT))
    pl["instances"][0]["orient"] = "FN"
    r = _run(_arm(tmp_path, placement=pl))
    assert r.returncode == 1
    assert "orient" in r.stderr and "FN" in r.stderr, r.stderr


# ── the measurement itself ──────────────────────────────────────────────────

def test_a_live_layer_reports_its_zero_with_the_closest_approach(tmp_path):
    r = _run(_arm(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "INERT" in r.stdout and "met2" in r.stdout, r.stdout
    # the corridor starts at x=200 um, the piece ends at x=10.5 -> 189.5 um
    assert "189.500" in r.stdout, r.stdout


def test_an_overlap_is_reported_and_exits_nonzero(tmp_path):
    """The positive case: a corridor laid over the notch piece must be found,
    or the zero above means nothing."""
    r = _run(_arm(tmp_path, guides=_guides(x1=10000, x2=11000)))
    assert r.returncode == 1, r.stdout
    assert "intersection(s)" in r.stdout and "diverge" in r.stdout, r.stdout


def test_the_run_tag_is_discovered_not_assumed(tmp_path):
    """An arm whose blocks hardened under a tag other than `h` must still be
    read -- matching no file would otherwise read as 'no notch metal', which is
    the refused zero wearing a second hat."""
    r = _run(_arm(tmp_path, run_tag="hb"))
    assert r.returncode == 0, r.stderr
    assert "INERT" in r.stdout, r.stdout


def test_json_carries_the_liveness_per_layer(tmp_path):
    out = tmp_path / "r.json"
    r = _run(_arm(tmp_path), "--json", str(out))
    assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    assert res["verdict"] == "inert" and res["shared_layers"] == ["met2"]
    (met2,) = [l for l in res["layers"] if l["layer"] == "met2"]
    assert met2["live"] is True and met2["pieces"] == 1


# ── coverage must be all-or-nothing (#925) ─────────────────────────────────

_TWO = {"design": "top", "dbu": 1000, "divider": "/", "instances": [
    dict(PLACEMENT["instances"][0]),
    {"def_name": "u2", "name": "u2", "cell": "acc_cell", "def_location": [200.0, 0.0],
     "x": 200.0, "y": 0.0, "orient": "N", "size": [100.0, 100.0]}]}


def test_it_refuses_when_one_placed_cell_has_no_notch_json(tmp_path):
    """Partial coverage.  `pe_cell` supplies pieces, `acc_cell` supplies none,
    and the global piece count is satisfied -- so the old code printed INERT
    with every acc_cell instance unmeasured, and the missing one could be
    exactly where a corridor crosses."""
    a = _arm(tmp_path, placement=_TWO, cells=("pe_cell", "acc_cell"))
    for j in (a / "acc_cell").rglob("*.notch.met2.json"):
        j.unlink()
    r = _run(a)
    assert r.returncode == 1, r.stdout
    assert "REFUSING" in r.stderr and "acc_cell" in r.stderr, r.stderr
    assert "INERT" not in r.stdout, r.stdout


def test_it_refuses_when_a_placed_cell_is_not_in_the_tops_macros(tmp_path):
    a = _arm(tmp_path, placement=_TWO, cells=("pe_cell", "acc_cell"),
             macros={"pe_cell": {"lef": ["dir::../pe_cell/runs/h/final/lef/pe_cell.notch.lef"]}})
    r = _run(a)
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "MACROS" in r.stderr and "acc_cell" in r.stderr, r.stderr


def test_it_refuses_when_cells_disagree_on_the_patched_layers(tmp_path):
    """Now keyed on PROVENANCE rather than on which JSONs happen to sit there,
    which also settles whether the rule is too strict: the recorded list is what
    `notch.sh` was ASKED for (`layer_list`), not what it found, so a cell with
    no met3 pins still records met3 and writes an empty met3 JSON.  Cells
    therefore disagree only when they came from DIFFERENT invocations, and that
    is worth refusing -- a cell patched by an older, narrower run is a cell
    measured against a different abstract."""
    a = _arm(tmp_path, placement=_TWO, cells=("pe_cell", "acc_cell"))
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    (d / "pe_cell.notch.layers").write_text("met2 met3\n")
    (d / "pe_cell.notch.met3.json").write_text(json.dumps(
        {"cell": "pe_cell", "layer": "met3", "uncovered": [[1.0, 1.0, 1.5, 1.5]]}))
    r = _run(a)
    assert r.returncode == 1, r.stdout
    assert "REFUSING" in r.stderr and "disagree" in r.stderr, r.stderr


def test_the_run_is_taken_from_the_tops_config_not_the_first_glob_hit(tmp_path):
    """Two hardenings exist; `MACROS` names `hb`, whose piece sits where the
    corridor runs.  Globbing would take `h` (lexicographically first) and its
    piece is elsewhere -- so a stale-run read reports INERT and the correct
    read reports the overlap."""
    a = _arm(tmp_path, run_tag="hb", guides=_guides(x1=10000, x2=11000))
    stale = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    stale.mkdir(parents=True)
    (stale / "pe_cell.notch.lef").write_text("MACRO pe_cell\nEND pe_cell\n")
    (stale / "pe_cell.notch.met2.json").write_text(
        json.dumps({"cell": "pe_cell", "layer": "met2", "uncovered": [[90.0, 90.0, 90.5, 90.5]]}))
    r = _run(a)
    assert r.returncode == 1, r.stdout
    assert "intersection(s)" in r.stdout, ("read the stale `h` run instead of the "
                                           "`hb` one the config names:\n" + r.stdout)


# ── the MACROS list and stale artefacts (#925 round 2) ─────────────────────

def test_a_supplemental_lef_beside_the_patched_one_does_not_misdirect_it(tmp_path):
    """`MACROS.<cell>.lef` is a list and other readers here consume all of it
    (`pdn_phase.py` builds `lef_paths` from every element), so a cell may carry
    a supplemental view.  Taking the LAST entry would derive the JSON path from
    that file; the patched abstract must be selected by name."""
    a = _arm(tmp_path)
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    (d / "pe_cell.extra.lef").write_text("MACRO pe_cell\nEND pe_cell\n")
    cfg = json.loads((a / "top" / "config.json").read_text())
    # FIRST, not appended: with the patched LEF second, neither "take the last"
    # (the original bug) nor "take the first" gets it right, so only selection
    # BY NAME passes.  Appending it left cands[0] correct by luck.
    cfg["MACROS"]["pe_cell"]["lef"].insert(
        0, "dir::../pe_cell/runs/h/final/lef/pe_cell.extra.lef")
    (a / "top" / "config.json").write_text(json.dumps(cfg))
    r = _run(a)
    assert r.returncode == 0, ("followed the supplemental LEF instead of the "
                               "patched one:\n" + r.stderr)
    assert "INERT" in r.stdout, r.stdout


def test_two_patched_lefs_in_one_entry_are_refused(tmp_path):
    a = _arm(tmp_path)
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    (d / "other.notch.lef").write_text("MACRO pe_cell\nEND pe_cell\n")
    cfg = json.loads((a / "top" / "config.json").read_text())
    cfg["MACROS"]["pe_cell"]["lef"].append("dir::../pe_cell/runs/h/final/lef/other.notch.lef")
    (a / "top" / "config.json").write_text(json.dumps(cfg))
    r = _run(a)
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "patched LEFs" in r.stderr, r.stderr


def test_a_json_from_a_wider_earlier_run_is_ignored_not_read(tmp_path):
    """`notch.sh` cleared only `<cell>.notch.lef` until #925, so a narrower
    `--layers` re-run left the wider run's JSON standing.  The layer set comes
    from the provenance file now, so an unlisted JSON is simply not read --
    mtime cannot decide this, because with `--layers met2,met3` the met2 JSON
    is written a whole KLayout pass before the LEF gets met3's timestamp and a
    FRESH met2 JSON is legitimately minutes older than the LEF beside it.

    The leftover has to be able to CHANGE the answer or this proves nothing:
    the arm carries a met3 corridor too, and the stale met3 rects sit directly
    under it.  Read, they make the verdict MEETS; ignored, met3 is simply not
    live.  (An earlier version put the leftover on met3 with met2-only
    corridors, where reading it could not alter the outcome -- the mutation
    that should have failed this test failed a different one, which is how the
    vacuity showed.)
    """
    g = _guides()
    g["bundles"][0]["corridors"].append(
        {"seg": 1, "layer": 3, "layer_name": "met3",
         "x1": 200000, "y1": 0, "x2": 220000, "y2": 100000, "nets": ["n[0]"]})
    a = _arm(tmp_path, guides=g)
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    (d / "pe_cell.notch.met3.json").write_text(json.dumps(
        {"cell": "pe_cell", "layer": "met3", "uncovered": [[200.0, 0.0, 220.0, 100.0]]}))
    r = _run(a)
    assert r.returncode == 0, ("read a JSON the provenance does not list:\n"
                               + r.stdout + r.stderr)
    assert "INERT" in r.stdout, r.stdout


def test_a_multi_layer_arm_is_measured_not_refused(tmp_path):
    """The case an mtime rule broke: two layers patched in one invocation, the
    met2 JSON older than the LEF by a whole KLayout pass.  Provenance lists
    both, so both are read and neither is called stale."""
    import os
    a = _arm(tmp_path, extra_layers=("met3",))
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    old = os.path.getmtime(d / "pe_cell.notch.lef") - 600
    os.utime(d / "pe_cell.notch.met2.json", (old, old))
    r = _run(a, "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stderr
    res = json.loads((tmp_path / "r.json").read_text())
    assert sorted(l["layer"] for l in res["layers"] if l["pieces"]) == ["met2", "met3"], res


def test_it_refuses_when_the_configured_patched_lef_is_absent(tmp_path):
    """`notch.sh` moves the LEF into place only on success, so a run that fails
    on a later layer leaves early JSONs and no LEF.  An earlier cut skipped its
    freshness check when the LEF was missing, which let exactly those partial
    JSONs reach a verdict."""
    a = _arm(tmp_path)
    (a / "pe_cell" / "runs" / "h" / "final" / "lef" / "pe_cell.notch.lef").unlink()
    r = _run(a)
    assert r.returncode == 1, r.stdout
    assert "REFUSING" in r.stderr and "does not exist" in r.stderr, r.stderr
    assert "INERT" not in r.stdout


def test_it_refuses_when_the_provenance_is_missing(tmp_path):
    """No record of which layers the LEF was patched from -- so a JSON beside it
    cannot be told from an earlier run's leftover."""
    a = _arm(tmp_path)
    (a / "pe_cell" / "runs" / "h" / "final" / "lef" / "pe_cell.notch.layers").unlink()
    r = _run(a)
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "notch.layers" in r.stderr, r.stderr


def test_it_refuses_when_a_recorded_layer_has_no_json(tmp_path):
    a = _arm(tmp_path)
    d = a / "pe_cell" / "runs" / "h" / "final" / "lef"
    (d / "pe_cell.notch.layers").write_text("met2 met3\n")
    r = _run(a)
    assert r.returncode == 1
    assert "REFUSING" in r.stderr and "met3" in r.stderr, r.stderr


# ── the artefact that reaches the router, and the units it is in (#925) ────

def test_it_reads_the_guide_openroad_consumes_not_the_manifest(tmp_path):
    """`guides.sh` writes two files and only `buda_bus.guide` reaches
    `read_guides`.  The `.guide` is gcell-expanded per bit and carries the
    `terminal met2,met3` pin-access strips, so it is a SUPERSET of the manifest
    -- on the real N=4 arm the manifest reads 0 intersections while the guide
    reads 11,311.  A box present only in the guide must be seen."""
    a = _arm(tmp_path)
    # the manifest keeps its far-away corridor; the guide gains one over the piece
    (a / "top" / "out" / "buda_bus.guide").write_text(
        "n\n(\n10000 10000 11000 11000 met2\n)\n")
    r = _run(a)
    assert r.returncode == 1, ("read the manifest instead of the guide:\\n" + r.stdout)
    assert "intersection(s)" in r.stdout, r.stdout


def test_it_reports_both_extents_so_a_scale_mismatch_is_visible(tmp_path):
    """Codex (#925) suspected the guide was in microns while the placement was
    in DBU, which would make every comparison arithmetic on unrelated numbers
    and fail silently toward INERT.  It is not: `guides.sh` declares
    `set_import_scale dbu` (line 87), and on the real N=4 arm the guide's
    extent is 772720 against a 944000 DBU die.

    A guard was written and removed.  The only available signal is the guide
    extent against the die, and a legitimately small corridor near the origin
    is indistinguishable from a 1000x error -- it rejected valid fixtures.
    Refusing real designs to defend a hypothetical is the trade #924 already
    corrected.  Both numbers are printed instead."""
    r = _run(_arm(tmp_path), "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stderr
    assert "guide extent" in r.stdout and "die" in r.stdout, r.stdout
    res = json.loads((tmp_path / "r.json").read_text())
    assert res["die_extent"] == 700000.0, res
    assert res["guide_extent"] > 0, res
