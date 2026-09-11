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
    "design": "top", "dbu": 1000, "divider": "/",
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


def _arm(tmp_path, guides=None, notch=NOTCH, run_tag="h", placement=PLACEMENT):
    a = tmp_path / "arm"
    (a / "top" / "out").mkdir(parents=True)
    (a / "top" / "placement.json").write_text(json.dumps(placement))
    (a / "top" / "out" / "buda_guides.json").write_text(json.dumps(guides or _guides()))
    if notch is not None:
        d = a / "pe_cell" / "runs" / run_tag / "final" / "lef"
        d.mkdir(parents=True)
        (d / "pe_cell.notch.met2.json").write_text(json.dumps(notch))
    return a


def _run(arm, *args):
    return subprocess.run([sys.executable, str(_TOOL), str(arm), *args],
                          capture_output=True, text=True)


# ── the refusals: a zero must be unreachable when nothing was measured ──────

def test_it_refuses_when_no_notch_piece_was_placed(tmp_path):
    """The blocker.  An arm with guides and placement but no notch JSON used to
    print `VERDICT: INERT` and, worse, `the check was live: True` -- the
    liveness line keyed on macro boxes rather than on pieces (#913)."""
    r = _run(_arm(tmp_path, notch=None))
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
