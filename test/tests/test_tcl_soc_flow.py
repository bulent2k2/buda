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

"""flow/tcl/soc.tcl — an SoC-shaped design: DEEP and DIVERSE.

What the corpus was missing here is not SIZE.  `tpu.tcl` is a mesh, so every
leaf is the same cell at the same depth; `flow/chip` is heterogeneous but
uniform in depth; `flow/ariane133` is a real design whose synthesized netlist
is uniquified, so nothing repeats.  This vehicle is many DIFFERENT cell types
(most appearing once) at RAGGED DEPTH — an ALU four levels down, a UART two.

These pin the claims that make it worth having, and each is chosen so that a
vehicle which quietly became a mesh, or flattened, would fail it.  Sizes are
deliberately small (NQ=1..2) so the tier stays fast; the sweep to NQ=16 and
the two lessons the vehicle paid for are in `flow/tcl/ReadMe.md`.
"""
import collections
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_VEHICLE = _ROOT / "flow" / "tcl" / "soc.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _run(tmp_path, *args):
    # cwd=tmp_path on purpose: the vehicle resolves the repo from its own
    # path, so it must run from anywhere.
    return subprocess.run(["tclsh", str(_VEHICLE), *map(str, args)],
                          capture_output=True, encoding="utf-8",
                          errors="replace", cwd=tmp_path, timeout=900)


def _depths(out):
    """The `dump_hbundles` rows tallied by DEPTH (`D0`..`D3`)."""
    k = collections.Counter()
    for ln in out.splitlines():
        if ln.startswith("hb-"):
            k[ln.split()[1]] += 1
    return k


def _kinds(out):
    """...and by kind — cross-level, or the cell whose template it is."""
    k = collections.Counter()
    for ln in out.splitlines():
        if ln.startswith("hb-"):
            k[ln.split()[2]] += 1
    return k


def test_the_soc_routes_clean(tmp_path):
    """NQ=2 rather than 1 on purpose: the flat-channel fault this vehicle was
    built through does not bite at NQ=1, so a one-quadrant run is not a
    regression guard for it (measured — restoring the flat gap leaves NQ=1
    clean and NQ=2 at 1 overlap / 32 unplaced)."""
    r = _run(tmp_path, 2)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_the_bundles_land_at_four_different_depths(tmp_path):
    """The property the whole vehicle exists for.  A mesh gives two depths
    (cell-local and cross-level) and a uniform-depth design gives two; this
    has to give FOUR, or it has quietly become one of them."""
    r = _run(tmp_path, 2)
    assert r.returncode == 0, r.stdout + r.stderr
    d = _depths(r.stdout)
    assert set(d) == {"D0", "D1", "D2", "D3"}, d
    assert all(v > 0 for v in d.values()), d


def test_cell_local_templates_form_at_two_different_nesting_levels(tmp_path):
    """`core_cell` is a template INSIDE `cluster_cell`, which is itself a
    template.  A flattened hierarchy would still route; it would just have
    one level of template, which is what this catches."""
    r = _run(tmp_path, 2)
    assert r.returncode == 0, r.stdout + r.stderr
    k = _kinds(r.stdout)
    assert k["cell:core_cell"] > 0, k
    assert k["cell:cluster_cell"] > 0, k
    assert k["cell:io_blk_cell"] > 0, k     # the SHALLOW branch, two levels up
    assert k["cross-level"] > 0, k
    # A template carries every occurrence, so the dump lists them.
    assert "quad_0/cl_0/core, quad_0/cl_1/core" in r.stdout


def test_one_by_depth_declaration_gives_a_different_band_per_level(tmp_path):
    """What the ragged depth BUYS.  A cell's level is intrinsic — how deep
    its own content goes — so `sram_cell` (1), `l1_cell` (2), `cluster_cell`
    (3) and `quad_cell` (4) take different caps from ONE line.  On a
    uniform-depth vehicle every cell is one level and this collapses to the
    `reserve_top_layers` case, which is why no existing vehicle pins it."""
    r = _run(tmp_path, 2, "-bydepth", "M3 M4 M5")
    assert r.returncode == 0, r.stdout + r.stderr
    levels = dict(re.findall(r"\[LayerCaps\] level (\d+) -> band \[lowest\.\.(M\d)\]",
                             r.stdout))
    assert levels == {"1": "M3", "2": "M4", "3": "M5"}, r.stdout
    assert "level 4+ unrestricted" in r.stdout
    assert "clean -- 0 overlaps" in r.stdout


def test_the_stack_relative_band_also_runs_clean(tmp_path):
    r = _run(tmp_path, 2, "-caps")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reserving the top 2 layer(s)" in r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_the_channel_is_a_constant_and_not_derived(tmp_path):
    """The finding, pinned as a finding.  A face IS derived from the bits
    that land on it; a channel is NOT, because the relationship is measured
    non-monotone (at DW=128 the design is clean at GAP 40/48/80 and strands
    bits at 16/24/32/56/64/96) -- a gap shifts every block and with it which
    blocks land on which track phase.  So `GAP`/`M` must stay CONSTANTS: a
    later "improvement" that derives them from the bus widths would assert a
    law this vehicle's own numbers deny, and would cost 2.6x the wirelength
    while failing at NQ=16, which is the measurement in `flow/tcl/ReadMe.md`.
    """
    probe = tmp_path / "probe.tcl"
    probe.write_text(
        'source [file join {%s} flow tcl soc_lib.tcl]\n'
        'foreach w {16 32 64 128} {\n'
        '    soc_vehicle::configure [list DW $w IW $w]\n'
        '    puts "$w [soc_vehicle::get GAP] [soc_vehicle::get M]"\n'
        '}\n' % _ROOT)
    r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                       encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = {int(ln.split()[0]): (int(ln.split()[1]), int(ln.split()[2]))
            for ln in r.stdout.split("\n") if ln.strip()}
    assert set(seen) == {16, 32, 64, 128}, seen
    assert len(set(seen.values())) == 1, ("the channel moved with the bus "
                                          "width — see the docstring", seen)


def test_bottom_up_gets_the_channel_it_needs_but_never_overrides_the_caller(tmp_path):
    """`-bottomup` changes the GEOMETRY, not just the flow (as `tpu.tcl`'s
    does): a fixed copy at every instance leaves an OVERLAP the healers
    cannot clear at the top-down channel, and 24 is the cheapest that does.

    The pair is supplied ATOMICALLY, and the single-knob forms are the ones
    that matter — filling each half in independently made the flag's own
    contribution partial, so `-bottomup -GAP 16` left `M` at 24 and gave a
    4976x1576 die where the default geometry is 4720x1440: a sweep meant to
    vary the channel alone varied two things (Codex P2, #930).  The first
    version of this test passed `-GAP` AND `-M` together, which is precisely
    why it did not see that."""
    plain = _run(tmp_path, 2, "-dry")
    bu = _run(tmp_path, 2, "-bottomup", "-dry")
    assert plain.returncode == 0 and bu.returncode == 0, plain.stdout + bu.stdout
    # the flag widened the die, so it widened the channel
    assert _die(bu.stdout)[0] > _die(plain.stdout)[0], (bu.stdout, plain.stdout)

    # ...and naming EITHER knob suppresses the whole pair, so the caller's
    # geometry is exactly what they asked for.  Both single-knob forms, since
    # each half leaked on its own.
    for args in (("-GAP", 16), ("-M", 16), ("-GAP", 16, "-M", 16)):
        forced = _run(tmp_path, 2, "-bottomup", *args, "-dry")
        assert forced.returncode == 0, forced.stdout + forced.stderr
        assert _die(forced.stdout) == _die(plain.stdout), (args, forced.stdout)

    # A channel the caller pins to something else is honoured as given, with
    # the other knob left at its configured default rather than at 24.
    other = _run(tmp_path, 2, "-bottomup", "-GAP", 32, "-dry")
    assert other.returncode == 0, other.stdout + other.stderr
    assert _die(other.stdout) not in (_die(plain.stdout), _die(bu.stdout)), \
        other.stdout


def _die(out):
    m = re.search(r"die (\d+)x(\d+)", out)
    assert m, out
    return int(m.group(1)), int(m.group(2))


def test_an_unknown_knob_is_an_error_not_a_silent_default(tmp_path):
    """`array set P {...}` holds no `;#` comments, because Tcl does not treat
    `#` as a comment inside braces — every one would become key/value
    ELEMENTS and shift the parameters after it.  That happened once here,
    leaving `GAP` undefined and `at`, `the` and `DIAL` as accepted knobs.
    A typo in an hour-long sweep must not report on a design nobody asked
    for, so the guard is worth a test of its own."""
    r = _run(tmp_path, 2, "-NOSUCHKNOB", 3)
    assert r.returncode != 0
    assert "unknown parameter" in (r.stdout + r.stderr)
    for junk in ("-the", "-at", "-DIAL"):
        rj = _run(tmp_path, 2, junk, 3)
        assert rj.returncode != 0, junk
        assert "unknown parameter" in (rj.stdout + rj.stderr), junk
