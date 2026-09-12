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
at RAGGED DEPTH — an ALU four levels down, a UART two — each type repeating a
different number of times (1 to 20; `-census` counts them).

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


def test_the_leaf_census_is_what_the_documents_claim(tmp_path):
    """The diversity claim is a COUNT, so it gets measured (Codex P2, #930).

    Three documents said "eleven leaf cell types, **most appearing once**"
    and named `xbar_cell` as one of the singletons — it has an instance per
    router, four at the default.  The census is eleven types with **two**
    singletons, `bridge_cell` and `memctl_cell`, and that is narrower than
    what was claimed.

    What is real, and what those documents say now, is that the counts span
    an order of magnitude where a mesh gives one count for every cell — and
    the two extremes are different code paths: `set_bottom_up *` copies a
    template to many instances and FREEZES a single-instance cell as a
    keepout with nothing to copy, so a design with no singleton exercises
    only half of it.

    `soc_vehicle::leaf_census` walks the structure `_fill` builds the
    instances from, so this measures the design rather than a second model
    of it — the failure mode that let the wrong sentence stand in the first
    place.
    """
    r = _run(tmp_path, 2, "-census")
    assert r.returncode == 0, r.stdout + r.stderr
    census = {ln.split()[1]: int(ln.split()[2])
              for ln in r.stdout.splitlines() if ln.startswith("census ")}
    assert len(census) == 11, census            # ELEVEN leaf types
    assert sum(census.values()) == 63, census   # ...and the banner's leaf count
    assert {c for c, n in census.items() if n == 1} == \
        {"bridge_cell", "memctl_cell"}, census  # TWO singletons, named
    assert census["xbar_cell"] == 4, census     # one per router, NOT a singleton
    # the spread is the property: an order of magnitude, not one count
    assert max(census.values()) == 20 and min(census.values()) == 1, census


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

    (b) At DW=128 — a 4x datapath — the same holds: the design routes at the
        default channel and a 6x channel only costs more wire.

    So a derivation from the bus width has nothing to target, and `GAP`/`M`
    stay CONSTANTS: a design far from the default sweeps `-GAP` and MEASURES
    the result, which is what this test does.

    Three things this replaces, all worth remembering.  The first version
    asserted only that `configure` left the two values unchanged — a routing
    change could not falsify it, so it pinned nothing (Codex P2, #930).  The
    numbers it cited in its own docstring had ALREADY gone stale by the time
    that was asked: an earlier sweep read as non-monotone (clean at GAP
    40/48/80), and a second healer round changed every point.  And then part
    (b) itself was wrong about the CAUSE — it asserted DW=128 could not
    route at any channel because the bits were "culled for crossing a
    keepout", when they were culled because three faces were stars (see
    `test_a_pin_is_sized_from_every_bit_that_lands_on_it`).  With those
    sized, DW=128 is clean at every gap swept.  A recorded measurement that
    nothing re-runs decays into a claim — and one that nothing re-runs
    against its own explanation decays into a wrong one.
    """
    # (a) the default widths: clean either way, and the wide one costs wire.
    narrow = _run(tmp_path, 1, "-GAP", 16)
    wide = _run(tmp_path, 1, "-GAP", 48)
    assert _verdict(narrow) == (0, 0, 0), narrow.stdout + narrow.stderr
    assert _verdict(wide) == (0, 0, 0), wide.stdout + wide.stderr
    assert _wl(wide) > _wl(narrow), ("a wider channel is supposed to cost "
                                     "wire and buy nothing here",
                                     _wl(narrow), _wl(wide))

    # (b) a 4x bus: clean at the default channel AND at 6x it, the wide one
    # only costing wire.  `-DW` ALONE, which is the sweep the two documents
    # record: `-IW` sizes `dec_cell` and every cell enclosing it, so passing
    # both would guard a different design than the one whose numbers are
    # written down, and the documented one could then regress while this
    # still passed (Codex P2, #930).
    wide_bus = [_run(tmp_path, 1, "-DW", 128, "-GAP", g) for g in (16, 96)]
    for gap, r in zip((16, 96), wide_bus):
        assert _verdict(r) == (0, 0, 0), (
            f"DW=128 at GAP {gap} strands bits again.  It used to, and the "
            "cause was a FACE (three stars), not the channel — find which "
            "face before touching GAP/M or the tables that quote them",
            r.stdout + r.stderr)
    assert _wl(wide_bus[1]) > _wl(wide_bus[0]), (
        "even at a 4x bus the wider channel is supposed to be pure cost",
        _wl(wide_bus[0]), _wl(wide_bus[1]))


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
    lands on it rather than a second cell type.

    What this cost was not the sizes but a CAUSAL CLAIM, and that is the part
    worth keeping.  `-AW 128` was reported here and in two documents as 128
    bits unplaced on a *supply-doomed seat*, i.e. "the channel from the other
    side".  The seat was real — and it was a CONSEQUENCE of a face too narrow
    to land on, which pushed the bus into a window that could not host it.
    Completing the rule makes `-IW` and `-AW` both CLEAN.  A symptom the tool
    reports is not a cause; the advisory named the seat and never the reason.

    A `max` over buses is only half the rule; the SUM at each pin is the
    other half, and it has its own test below.
    """
    cells = ("sram_cell", "alu_cell", "xbar_cell", "dec_cell")
    # ONE interpreter per setting: `configure` mutates the namespace, so a
    # loop over knobs measures {IW 128}, then {IW 128, AW 128}, and reports
    # the second as if `-AW` alone had done it.
    rows = [_sizes(tmp_path, knob, cells)
            for knob in ((), ("IW", 128), ("AW", 128))]
    # (sram_w, sram_h, alu_w, alu_h, xbar_w, xbar_h, dec_w, dec_h)
    assert rows[0] == [152, 152, 280, 152, 280, 280, 152, 152], rows[0]
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


def test_bottom_up_changes_the_flow_and_not_the_geometry(tmp_path):
    """`-bottomup` used to widen the channel behind the caller's back
    (`GAP 24 M 24`), because a fixed copy at every instance left overlaps
    the healers could not clear.  It does not any more, and the reason is
    the point: both the overlaps AND the non-monotone sweep that justified
    the number (NQ=4: 16 X, 24 ok, 32 ok, 48 ok, **64 X**, 96 ok, read at
    the time as a fixed copy making the channel a phase lottery) were the
    STAR faces.  With those sized from the bits that land on them the flag
    needs no channel through NQ=4, and at NQ=8, where it does, the curve is
    plain monotone and 24 is not enough anyway.

    So the flag is a FLOW flag again and the geometry is the caller's.  That
    is asserted rather than asserted-away: `-dry` must give the SAME die
    with and without it, and the routed endpoint must be clean at the
    default channel — the die check alone would also pass if the flag broke
    the run."""
    plain = _run(tmp_path, 2, "-dry")
    bu = _run(tmp_path, 2, "-bottomup", "-dry")
    assert plain.returncode == 0 and bu.returncode == 0, plain.stdout + bu.stdout
    assert _die(bu.stdout) == _die(plain.stdout), (bu.stdout, plain.stdout)

    # a channel the caller names is honoured exactly, and moves the die
    other = _run(tmp_path, 2, "-bottomup", "-GAP", 32, "-M", 32, "-dry")
    assert other.returncode == 0, other.stdout + other.stderr
    assert _die(other.stdout) != _die(plain.stdout), other.stdout

    # ...and no widening is needed for the run to be clean here.
    routed = _run(tmp_path, 1, "-bottomup")
    assert _verdict(routed) == (0, 0, 0), routed.stdout + routed.stderr


def _sizes(tmp_path, knob, cells):
    """`soc_vehicle::size` for each of `cells`, in a FRESH interpreter under
    exactly the one override in `knob`."""
    probe = tmp_path / ("sz_%s.tcl" % ("_".join(str(k) for k in knob) or "def"))
    probe.write_text(
        'source [file join {%s} flow tcl soc_lib.tcl]\n'
        'soc_vehicle::configure [list %s]\n'
        'puts "[join [list %s] { }]"\n'
        % (_ROOT, " ".join(str(k) for k in knob),
           " ".join("[soc_vehicle::size %s]" % c for c in cells)))
    r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                       encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return [int(v) for v in r.stdout.split()]


def test_a_pin_is_sized_from_every_bit_that_lands_on_it(tmp_path):
    """A pin is ONE PLACE, so what has to fit there is the SUM of the bits
    that land on it — not the widest bus taken alone.

    Reading the face rule per BUS is what let the star through, three times.
    §3b's first cut wired every cluster straight to `l2/mc`; then wiring the
    banks (below) put every `NBANK` bank on one `tag.d_in`, every `NBANK2`
    bank on one `mc.d_in`, and every peripheral of a cluster on one
    `xbar.p_in`.  Each of those buses passed a per-bus check and the pin did
    not, and what the tool then reported was a supply-doomed seat somewhere
    downstream — which is how `-DW`/`-CW` came to be written up as a keepout
    cull and a dead span.

    So `check_bus_faces` accumulates per ENDPOINT PATH, and the multiplicity
    knobs grow the cells they feed.  Both halves are asserted: the SIZE moves
    with the knob, and the enforcement REFUSES a design where it does not.
    """
    cells = ("tag_cell", "memctl_cell", "bridge_cell")
    base = _sizes(tmp_path, (), cells)
    assert base == [280, 280, 536, 536, 152, 152], base
    # each knob grows exactly the pin it stars into, and nothing else
    assert _sizes(tmp_path, ("NBANK", 4), cells) == [536, 536, 536, 536,
                                                     152, 152]
    assert _sizes(tmp_path, ("NBANK2", 8), cells) == [280, 280, 1048, 1048,
                                                      152, 152]
    assert _sizes(tmp_path, ("NIO", 8), cells) == [280, 280, 536, 536,
                                                   280, 280]

    # ...and the guard is a guard: pin `tag_cell` to its one-bank size while
    # asking for four banks, and declaring the buses must FAIL.  Without the
    # accumulator every one of those buses fits on its own.
    probe = tmp_path / "starve.tcl"
    probe.write_text(
        'source [file join {%s} flow tcl soc_lib.tcl]\n'
        'soc_vehicle::configure {NBANK 4}\n'
        'set soc_vehicle::SZ(tag_cell) [list 280 280]\n'
        'array set soc_vehicle::CELLOF {quad_cell,cl_0 cluster_cell\n'
        '                               cluster_cell,l1i l1_cell\n'
        '                               l1_cell,tag tag_cell}\n'
        'set pin quad_0/cl_0/l1i/tag.d_in\n'
        'if {[catch {soc_vehicle::check_bus_faces 32 $pin $pin $pin $pin} e]} {\n'
        '    puts "REFUSED $e"\n'
        '} else { puts "ACCEPTED" }\n' % _ROOT)
    r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                       encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("REFUSED"), (
        "four 32-bit buses on one 280-unit pin have to be refused; a per-bus"
        " check accepts every one of them", r.stdout)
    # it refuses at the bus that first breaks the pin (96 bits, the third of
    # the four), naming the RUNNING TOTAL and not that bus's own width
    assert "96 bits land" in r.stdout and "contributes 32" in r.stdout, r.stdout


def test_every_configured_bank_carries_a_net(tmp_path):
    """`NBANK`/`NBANK2` are advertised as dials, so every bank they add has
    to be WIRED.  Only `bank_0` ever was: at the defaults 11 of the 20
    `sram_cell` instances carried no net, so raising either knob added
    filler geometry, moved the die and the census, and left the routed
    workload alone (Codex P2, #930).

    Pinned by comparing the banner's advertised bus count against the number
    of buses the ENGINE actually received — the bundler's hbundle count,
    which is one per bus in this design.  Both halves matter and the first
    draft of this test had neither: it read the advertised count at two
    `NBANK` settings and asserted the difference, which `describe` computes
    by arithmetic from the very knob being varied.  Re-wiring only `bank_0`
    left all thirteen tests passing.  A count a flow COMPUTES cannot witness
    what that flow DECLARED."""
    def counts(*knobs):
        r = _run(tmp_path, 1, *knobs)
        assert r.returncode == 0, r.stdout + r.stderr
        said = re.search(r"(\d+) buses", r.stdout)
        built = re.search(r"HierBundler: (\d+) hbundles", r.stdout)
        assert said and built, r.stdout
        return int(said.group(1)), int(built.group(1))

    base_said, base_built = counts()
    assert base_said == base_built, (base_said, base_built)
    # 2 clusters x 2 caches x 2 buses per extra L1 bank; 2 per extra L2 bank
    for knobs, delta in ((("-NBANK", 3), 8), (("-NBANK2", 6), 4)):
        said, built = counts(*knobs)
        assert said == built, (knobs, said, built)
        assert built == base_built + delta, (knobs, base_built, built)


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
