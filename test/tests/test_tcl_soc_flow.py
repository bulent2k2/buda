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


def _verdict(r):
    """`(overlaps, unplaced, violations)` from the vehicle's own last line.

    `verdict` prints the clean case on stdout and the failure on stderr, so
    both streams are read; a run that printed neither is a crash, not a
    result, and asserting that here keeps every caller from re-deriving it.
    """
    out = r.stdout + r.stderr
    if "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in out:
        return (0, 0, 0)
    m = re.search(r"FAILED -- (\d+) overlaps, (\d+) unplaced, "
                  r"(\d+) audit violations", out)
    assert m, out[-4000:]
    return tuple(int(g) for g in m.groups())


def _wl(r):
    m = re.findall(r"total detailed WL = (\d+)", r.stdout)
    assert m, r.stdout[-4000:]
    return int(m[-1])


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


def test_a_wider_channel_is_not_the_lever_a_wider_bus_needs(tmp_path):
    """The finding, pinned by RUNNING the design rather than by reading
    `configure` back.

    A face IS derived from the bits that land on it (`soc_lib.tcl`); a
    CHANNEL is not, and the reason is that widening the channel is not the
    lever.  Two measurements, both made here:

    (a) At the default bus widths a wider gap buys NO routing — the design
        is already clean — and costs wire monotonically, inflating the die
        while the contention sits elsewhere.  That is `tpu.tcl`'s recorded
        lesson in the direction it recorded it.

    (b) At DW=128 the design does not route, and it does not route at a 6x
        channel either: the bits are culled for CROSSING A KEEPOUT (one
        cross-level NoC leg, `…/rtr/fi_out → l2/mc`), which no gap width
        addresses.  A wider gap moves the count a little because it shifts
        every block's track phase — a perturbation, not a supply.

    So a derivation from the bus width has nothing to target, and `GAP`/`M`
    stay CONSTANTS: a design far from the default sweeps `-GAP` and MEASURES
    the result, which is what this test does.

    Two things this replaces, both worth remembering.  The first version
    asserted only that `configure` left the two values unchanged — a routing
    change could not falsify it, so it pinned nothing (Codex P2, #930).  And
    the numbers it cited in its own docstring had ALREADY gone stale by the
    time that was asked: the earlier sweep read as non-monotone (clean at
    GAP 40/48/80, stranded at 16/24/32/56/64/96), and re-running it after a
    second healer round landed gives no clean point at all.  A recorded
    measurement that nothing re-runs decays into a claim.
    """
    # (a) the default widths: clean either way, and the wide one costs wire.
    narrow = _run(tmp_path, 1, "-GAP", 16)
    wide = _run(tmp_path, 1, "-GAP", 48)
    assert _verdict(narrow) == (0, 0, 0), narrow.stdout + narrow.stderr
    assert _verdict(wide) == (0, 0, 0), wide.stdout + wide.stderr
    assert _wl(wide) > _wl(narrow), ("a wider channel is supposed to cost "
                                     "wire and buy nothing here",
                                     _wl(narrow), _wl(wide))

    # (b) a 4x bus: unroutable at the default channel AND at 6x it, for a
    # reason a channel cannot reach.  `-DW` ALONE, which is the sweep the
    # two documents record: `-IW` sizes `dec_cell` and every cell enclosing
    # it, so passing both would guard a different design than the one whose
    # numbers are written down, and the documented one could then regress
    # while this still passed (Codex P2, #930).  Here they happen to agree
    # bit for bit, but that is a measurement, not a reason to conflate them.
    for gap in (16, 96):
        r = _run(tmp_path, 1, "-DW", 128, "-GAP", gap)
        _ov, un, _vi = _verdict(r)
        assert un > 0, (
            f"DW=128 at GAP {gap} now routes clean.  If a channel really is "
            "what this design was short of, the constants are derivable "
            "after all — update `soc_lib.tcl`'s channel note and the "
            "`flow/tcl/ReadMe.md` table before relaxing this.", r.stdout)
        assert "crosses a keepout" in r.stdout, (
            "the bits are supposed to be CULLED, not short of tracks — a "
            "different cause means the note above no longer explains it",
            gap, r.stdout)


def test_every_cell_a_bus_lands_on_is_sized_from_that_bus(tmp_path):
    """The face rule has to hold for EVERY endpoint of a bus, not just the
    one whose knob names it (Codex P2 x2, #930).  Three cells broke it:

    * `sram_cell` drives `id_[IW]` out of `l1i/bank_0` and `alu_cell`
      receives `i_[IW]`, both sized from `DW` — so `-IW` widened `dec_cell`
      and every container above it while both other ends stayed narrow;
    * `xbar_cell` was `2*DW` on both axes while `nr_[AW]` joins two routers
      directly and `pc_[CW]` arrives from an io pad.

    All are SHARED cell types (an `sram_cell` also serves `l1d` and the L2,
    an `alu_cell` also takes `r_[DW]`), so each face is a `max` over what
    lands on it rather than a second cell type.  At the defaults every max IS
    the old expression, which is why the sizes below are asserted at BOTH
    settings: the whole design and every recorded table are unchanged.

    What this cost was not the sizes but a CAUSAL CLAIM, and that is the part
    worth keeping.  `-AW 128` was reported here and in two documents as 128
    bits unplaced on a *supply-doomed seat*, i.e. "the channel from the other
    side".  The seat was real — and it was a CONSEQUENCE of a face too narrow
    to land on, which pushed the bus into a window that could not host it.
    Completing the rule makes `-IW` and `-AW` both CLEAN.  A symptom the tool
    reports is not a cause; the advisory named the seat and never the reason.
    """
    probe = tmp_path / "probe.tcl"
    cells = ("sram_cell", "alu_cell", "xbar_cell", "dec_cell")
    probe.write_text(
        'source [file join {%s} flow tcl soc_lib.tcl]\n'
        'foreach knob {{} {IW 128} {AW 128}} {\n'
        '    soc_vehicle::configure $knob\n'
        '    puts "[join [list %s] { }]"\n'
        '}\n' % (_ROOT, " ".join("[soc_vehicle::size %s]" % c for c in cells)))
    r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                       encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [[int(v) for v in ln.split()]
            for ln in r.stdout.split("\n") if ln.strip()]
    # (sram_w, sram_h, alu_w, alu_h, xbar_w, xbar_h, dec_w, dec_h)
    assert rows[0] == [152, 88, 280, 152, 280, 280, 152, 88], rows[0]
    # -IW: every face the IW buses land on grows with them, to the same size
    assert rows[1][0] == rows[1][2] == rows[1][3] == rows[1][6] == 536, rows[1]
    # -AW: the router pair, which `2*DW` used to cap at 280
    assert rows[2][4] == rows[2][5] == 536, rows[2]

    # ...and the routing says so: both knobs are CLEAN where the rule was
    # broken, which is the correction, not just the derivation.
    for knob in ("-IW", "-AW"):
        r = _run(tmp_path, 1, knob, 128)
        assert _verdict(r) == (0, 0, 0), (
            f"{knob} 128 used to strand bits because a face at the far end of"
            " its bus was sized from DW; if it strands again, find which face"
            " before blaming the channel", knob, r.stdout + r.stderr)


def test_bottom_up_routes_the_diverse_hierarchy_clean(tmp_path):
    """`-bottomup` END TO END.  `-dry` exits before `buda::start`, so the
    flag's whole point — mark, align, solve once, copy, verify the tracks —
    was covered by nothing at all, and the die-geometry test below runs dry
    by construction (Codex P2, #930).

    NQ=1 is enough and costs ~2s, because the repetition this path keys on
    is WITHIN one quadrant: two clusters, two cores, four io pads.  What the
    DIVERSE hierarchy adds over `tpu.tcl`'s mesh is the failure mode —
    `align_bottom_up` can only nudge, and a 2-D packing of differently sized
    cells has no common phase to nudge onto, so instances come out
    MISALIGNED and `check_template_tracks on_mismatch independent` is what
    carries them.  That policy is also the declaration ripup's
    uniformity-break pass is gated on, so a run that never reaches it
    exercises neither.
    """
    r = _run(tmp_path, 1, "-bottomup")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _verdict(r) == (0, 0, 0), r.stdout + r.stderr
    # solved once and COPIED — the property the flag exists for, at both
    # stages (the cell-local NUTS solve and the per-bit DNUTS one).
    assert re.search(r"\[BottomUp\] cell '\w+': local NUTS placed \d+ "
                     r"segment\(s\), \d+ overlap\(s\); "
                     r"copied [1-9]\d* fixed segment", r.stdout), r.stdout
    assert re.search(r"\[BottomUp\] DNUTS: \d+ reference bit\(s\) solved "
                     r"once, [1-9]\d* copied to \d+ sibling", r.stdout), r.stdout
    # ...and the mismatch policy actually ran, on a cell that needed it.
    assert "check_template_tracks: on_mismatch policy = independent" in r.stdout
    assert "[TemplateTracks]" in r.stdout and "MISALIGNED" in r.stdout, r.stdout


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
