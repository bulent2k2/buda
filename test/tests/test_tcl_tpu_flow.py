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

"""flow/tcl/tpu.tcl — the TPU-shaped systolic array.

The corpus had no genuine MESH.  `flow/chip` is arrayed but assembled from
heterogeneous cells and `flow/ariane133` is a CPU core, so the bottom-up
family — which keys on many CONGRUENT instances of ONE cell — had nothing
array-shaped to work on.  This pins that: the array forms, the cell-local
template carries every row instance, the solve-once-copy actually copies,
and the whole thing ends clean at both a top-down and a bottom-up run.

Sizes here are deliberately small (N=4) so the tier stays fast; the vehicle
runs to N=32 (1024 PEs, 42k bit-wires, ~29s) and the sweep lives in the
ReadMe.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_VEHICLE = _ROOT / "flow" / "tcl" / "tpu.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _run(tmp_path, *args):
    # cwd=tmp_path on purpose: the vehicle resolves the repo from its own
    # path, so it must run from anywhere.
    return subprocess.run(["tclsh", str(_VEHICLE), *map(str, args)],
                          capture_output=True, encoding="utf-8",
                          errors="replace", cwd=tmp_path, timeout=900)


def test_the_mesh_routes_clean_top_down(tmp_path):
    r = _run(tmp_path, 4)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in out
    assert "16 PEs in 4 rows" in out
    # The mesh's defining split, and the reason a systolic array is worth
    # having here at all: the west->east activation chain is confined to a
    # row (cell-local, carrying every row instance), while the north->south
    # psum and weight chains cross row instances (cross-level).  A design
    # where those two came out the same would not be a mesh.
    kinds = _bundle_kinds(out)
    assert kinds["cell:row_cell"] == 4 * 3, kinds     # N rows x (N-1) hops
    assert kinds["cross-level"] == 4 * 3, kinds       # (N-1) rows x N cols x 2
    assert "[row_0, row_1" in out, "no template instance list in the dump"


def test_bottom_up_solves_one_row_and_copies_it(tmp_path):
    """The experiment the vehicle exists for — and the reason its row pitch
    is snapped to a track period: congruent instances must see IDENTICAL
    tracks, and `check_template_tracks` refuses DNUTS when they do not."""
    r = _run(tmp_path, 4, "-bottomup")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in out
    assert "ALIGNED" in out and "see identical signal tracks" in out
    # The COLLAPSE is the measurable part, and it is what the pre-expansion
    # dump does not show: the bundler lists one cell-local bundle per (row,
    # hop) — as `flow/tcl/array.tcl` does too — and the bottom-up planner
    # solves N-1 of them (the hops in ONE row) and copies to the rest.
    import re
    m = re.search(r"(\d+) template bundle\(s\) planned locally;"
                  r" decision pinned for (\d+) instance", out)
    assert m, out[-800:]
    assert (int(m.group(1)), int(m.group(2))) == (3, 4), m.groups()
    assert "solved once" in out and "copied to" in out


def test_bottom_up_and_top_down_agree_at_equal_geometry(tmp_path):
    """Solve-once-copy must not COST anything on a congruent mesh.

    Measured at equal geometry the two are byte-identical, which is the
    property worth pinning — a template copy that quietly routed worse than
    the full solve would be a real defect and would still end 'clean'.  The
    geometry has to be held equal explicitly: `-bottomup` snaps the row
    pitch onto the track period, so comparing the DEFAULTS compares two
    different floorplans (a ~2.9x WL gap that is entirely die size)."""
    gap = _row_gap_for_align(tmp_path)
    a = _run(tmp_path, 4, "-ROWGAP", gap)
    b = _run(tmp_path, 4, "-bottomup")
    assert a.returncode == 0 and b.returncode == 0, a.stderr + b.stderr
    assert _wl(a.stdout) == _wl(b.stdout), (
        f"top-down {_wl(a.stdout)} vs bottom-up {_wl(b.stdout)} "
        f"at the same geometry")


def test_every_knob_is_settable_and_a_typo_is_refused(tmp_path):
    """`-dry` prints the size model without building, which is what makes a
    sweep cheap to plan.  An unknown knob is an ERROR: a typo in a sweep that
    runs for an hour must not report on a design nobody asked for."""
    r = _run(tmp_path, 8, "-PW", 32, "-AW", 16, "-PIPE", 4, "-dry")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "64 PEs in 8 rows" in r.stdout
    # PEW auto-sizes to the bus that lands on it: (32+8)*4 + 24 = 184.
    r2 = _run(tmp_path, 8, "-PW", 32, "-AW", 16, "-dry", "-PEW", 184)
    assert r2.returncode == 0 and "die " in r2.stdout

    bad = _run(tmp_path, 4, "-NOSUCHKNOB", 1, "-dry")
    assert bad.returncode != 0
    assert "unknown parameter" in (bad.stdout + bad.stderr)


def test_a_face_too_narrow_for_its_bus_is_reported(tmp_path):
    """The failure that cost the most to diagnose, now said at declaration.

    A block narrower than the bus landing on it is unroutable however much
    CHANNEL it is given — measured: PEW 60 against a 24-bit psum on a 4.0
    bit pitch stranded 672 of 832 bits, and widening the channel made it
    WORSE (1272).  Nothing reported it until DNUTS."""
    r = _run(tmp_path, 4, "-PEW", 60, "-dry")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "narrower than the" in r.stderr
    assert "needs >= 128" in r.stderr, r.stderr


def _bundle_kinds(out):
    """The `dump_hbundles` rows tallied by kind — depth + cell context."""
    import collections
    k = collections.Counter()
    for ln in out.splitlines():
        if ln.startswith("hb-"):
            k[ln.split()[2]] += 1
    return k


def _wl(out):
    import re
    m = re.search(r"total detailed WL = (\d+)", out)
    assert m, out[-500:]
    return int(m.group(1))


def _row_gap_for_align(tmp_path):
    """The ROWGAP `-bottomup` derives, read from the vehicle itself rather
    than restated here — a second copy of the arithmetic would drift."""
    r = subprocess.run(
        ["tclsh", "-c"], capture_output=True, encoding="utf-8", input="")
    script = (f'source [file join {_ROOT} flow tcl tpu_lib.tcl]\n'
              'tpu_vehicle::configure {N 4 ALIGN 1}\n'
              'puts [tpu_vehicle::get ROWGAP]\n')
    p = tmp_path / "gap.tcl"
    p.write_text(script)
    r = subprocess.run(["tclsh", str(p)], capture_output=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()
