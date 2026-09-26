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
the two lessons the vehicle paid for are in `flow/tcl/soc.md`.
"""
import collections
import os
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

    `soc_vehicle::leaf_census` is derived from `leaf_paths`, which walks the
    structure `_fill` builds the instances from, so this measures the design
    rather than a second model of it — the failure mode that let the wrong
    sentence stand in the first place.

    A census counts instances and says NOTHING about whether they are wired,
    which is the hole two of them fell through (`l2/tag`, and `rtr/fi_in` at
    the chain start).  That question needs the paths rather than the tally,
    and it is asked by
    `test_every_leaf_face_is_exactly_the_bits_that_land_on_it`.
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

    * `sram_cell` drives `l1id_*[IW]` out of each `l1i` bank and `alu_cell`
      receives `i_[IW]`, both sized from `DW` — so `-IW` widened `dec_cell`
      and every container above it while both other ends stayed narrow.
      (Written as `id_[IW]` out of `l1i/bank_0` until the banks were wired;
      `id_` now leaves `l1i/tag.d_out` and touches no SRAM, so the citation
      outlived the netlist — Codex P2, #930.  The size is unchanged; what was
      wrong is the dependency a later face change would trace.)
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
    # Every one is 152 at the defaults now, because every face is EXACTLY
    # the bits on its busiest pin (`test_every_leaf_face_is_exactly_the_bits`)
    # and at DW == IW == 32 that is one 32-bit bus everywhere.  The 280s here
    # were the `2*DW` coefficients, which had nothing behind them.
    assert rows[0] == [152, 152, 152, 152, 152, 152, 152, 152], rows[0]
    # -IW: every face the IW buses land on grows with them, to the same size
    assert rows[1][0] == rows[1][2] == rows[1][3] == rows[1][6] == 536, rows[1]
    # ...and a face NO IW bus reaches does not move: `xbar` sees DW/AW/CW
    assert rows[1][4] == rows[1][5] == 152, rows[1]
    # -AW: the router pair, which `2*DW` used to cap at 280
    assert rows[2][4] == rows[2][5] == 536, rows[2]
    # ...while `alu`, which no AW bus reaches, stays put
    assert rows[2][2] == rows[2][3] == 152, rows[2]

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
    # ...and NO supply-doomed seat here, which is the cheap half of a claim
    # that was first written too strongly.  At NQ=16 `-bottomup` strands 8
    # bits of `pc_0` on a seat the tool reports at EVERY gap swept including
    # the clean one, and that was written up as the seat being "invariant".
    # It is not: the assigned layer moves M7/TOP <-> M4/LOW across those
    # rows, and a seat IS its layer plus span x slide, so what repeats is
    # the SEGMENT (Codex P2, #930).  Nor is the repeat size-independent --
    # NQ=2/4/8
    # bottom-up report no seat at all, and NQ=16 TOP-DOWN — the same design,
    # the same size, the same `pc_0` and the same `io_cell` face — is clean
    # with none either.  So the seat is a product of the bottom-up fixed copy
    # AT SCALE, not of a declared width.
    #
    # This pins the size-dependence at the only price the mid tier can pay
    # (the NQ=16 contrast is minutes, and is measured in the two documents).
    #
    # What it is WORTH is stated from measurement rather than asserted, and
    # the first version of this comment got it wrong: it claimed narrowing
    # `io_cell`'s face would produce the seat here.  It does not.  Three
    # attempts to make this line fire at NQ=1 all failed, and each failed
    # differently:
    #
    #   io_cell face /4      the flow REFUSES at declaration (rc=1) — a
    #                        too-narrow face never reaches the router, which
    #                        is `tpu.tcl`'s lesson already baked in here
    #   -GAP 4 / -GAP 8      clean, no seat — a starved channel does not
    #                        produce one either
    #   -CW 64 (4x)          clean, no seat
    #
    # So this is a CANARY, not a guard with a demonstrated live mutation,
    # and that is the honest label for it.  The negative result is worth
    # more than the assertion: at this size the seat is not reachable by
    # width OR by channel, which is independent evidence for the finding it
    # accompanies — the seat is a bottom-up-fixed-copy-at-scale artifact,
    # not a declared-width fault.  The line stays because it costs nothing
    # and pins one row of a table two documents now publish.
    assert "supply-doomed" not in r.stdout, r.stdout


def test_a_copied_core_lands_on_its_own_signal_tracks(tmp_path):
    """#946: the bottom-up COPY put a whole core's bus in a GROUND slot while
    `check_template_tracks` read `core_cell` ALIGNED.

    The core's M3 segment sits over a LOW-layer keepout, so the span-clear
    pool the check compared was EMPTY at the reference and at every copy —
    empty against empty, "identical" — while DetailedNUTS, finding it short
    of the bits, seats them from the MIDPOINT pool instead
    (`signal_tracks_in`, detailed_nuts.cpp), and those are a phase apart:
    the copies sit at x offsets 1024 / 2080 / 3104, none a multiple of the
    layer's 18-unit period.  So the verdict gated a copy onto tracks the
    sibling does not have.  The check now compares every pool the bits can
    come from, and the misaligned core is solved on its own under
    `on_mismatch independent`.

    Judged by `tools/independent_audit.py`, which reads the stored
    rectangles and none of the engine: on the defect the NQ=1 round had 32
    OFF_GRID bit-wires (every bit of one copied core; 96 at NQ=2), and the
    engine's own verdict could not see it — `check_design` has no on-grid
    check (#947) and reported the same 1/8/8 before and after, all of it
    the `pc_0` supply-doomed seat.  `-noheal` because a healer re-roll can
    move the copy and hide the fault; the remaining judge findings (the
    eight `pc_0` OPENs, the cross-bundle SHORTs of #948) are not this
    defect and are deliberately not asserted here."""
    import json
    ckpt = tmp_path / "ckpt.bdb"
    env = dict(os.environ, BUDA_BDB_MEMORY_TO=str(ckpt))
    r = subprocess.run(["tclsh", str(_VEHICLE), "1", "-bottomup", "-noheal"],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=tmp_path, env=env, timeout=900)
    out = r.stdout + r.stderr
    # The checkpoint is written CONTINUOUSLY, so its existence says nothing
    # about whether the flow finished: a crash after the verdict line below
    # would leave a partial route the judge could call clean on the kinds
    # asserted here (Codex P2 on #955).  So the run must reach its own
    # verdict (`_verdict` refuses a run that printed none), exit as that
    # verdict says — `-noheal` is dirty here on purpose (the `pc_0` seat),
    # which is why the exit code is not simply 0 — and have run the
    # detailed copy stage this test is about.
    v = _verdict(r)
    assert r.returncode == (0 if v == (0, 0, 0) else 1), out[-4000:]
    assert re.search(r"\[BottomUp\] DNUTS: \d+ reference bit\(s\) solved "
                     r"once, [1-9]\d* copied", out), out[-6000:]
    assert ckpt.exists(), out[-4000:]
    # the engine's check now SEES the misaligned core, by the pool it missed
    assert re.search(r"\[TemplateTracks\] cell 'core_cell': MISALIGNED — "
                     r"\S+: L3 seg\d+ midpoint:", out), out[-6000:]

    res = tmp_path / "judge.json"
    judged = subprocess.run(
        ["python3", str(_ROOT / "tools" / "independent_audit.py"),
         str(ckpt), "--json", str(res), "--quiet"],
        capture_output=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONPATH=""), timeout=300)
    assert judged.returncode in (0, 1), judged.stdout + judged.stderr
    verdict = json.loads(res.read_text())
    counts = verdict["counts"]
    assert verdict["wires"] > 0, verdict      # a route to judge, not none
    assert counts["OFF_GRID"] == 0, (counts, judged.stdout[-4000:])
    assert counts["ROW_MISMATCH"] == 0, counts


def test_bottom_up_changes_the_flow_and_not_the_geometry(tmp_path):
    """`-bottomup` used to widen the channel behind the caller's back
    (`GAP 24 M 24`), because a fixed copy at every instance left overlaps
    the healers could not clear.  It does not any more, and the reason is
    the point: both the overlaps AND the non-monotone sweep that justified
    the number (NQ=4: 16 X, 24 ok, 32 ok, 48 ok, **64 X**, 96 ok, read at
    the time as a fixed copy making the channel a phase lottery) were the
    STAR faces.

    Every sizing fix since pushed the need further out -- the stars from
    NQ=4 to NQ=8, the phantom coefficients from NQ=8 to NQ=16 -- so the flag
    is clean at the default channel through NQ=8, where EVERY gap is clean,
    and NQ=16 is where a fixed copy at the default channel first comes back
    DIRTY -- the observation; what such a run actually needs is ONE segment
    re-seated and not a channel (Codex P2, #930: this docstring still said
    "needs one" after the documents had retracted that cause).  There the gap
    sweep is NON-monotone (16 X, 24 ok, 32 X, 48 X, 96 ok) and `-GAP 24 -M 24`
    is the cheapest clean point, which is the workaround the two documents
    name.

    This docstring said the opposite of all three until 2026-09-12 -- NQ=8
    needing a channel, a monotone curve, and 24 not being enough (Codex P2,
    #930) -- and the last of those directly contradicted the recipe the same
    revision published.  Why the grep missed it is the lesson: I searched
    for the SENTENCES I remembered writing ("the flag cannot pick one",
    "GAP 48 -M 48") and this docstring uses neither.  A moved threshold is
    found by grepping the VALUE (`NQ=8`, `monotone`) across the whole tree,
    which is what finally turned it up -- and the fourth instance of a
    remedy outliving its cause in one PR.

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


def test_every_leaf_face_is_exactly_the_bits_that_land_on_it(tmp_path):
    """The face rule, enforced as an EQUALITY on the sum at each pin -- and,
    FIRST, that every leaf INSTANCE has a bus at all.

    `check_bus_faces` enforces the `>=` half at declaration.  This is the
    `<=` half, and it is the rule the vehicle's own thesis states: a leaf's
    size IS derived from the bits that land on its faces, so a term with no
    bits behind it is wrong whether it is a whole knob or a coefficient.

    Three rounds of review walked in from the weak end and each fix was too
    narrow (Codex P2 x3, #930):

    * PHANTOM KNOBS -- `dec_cell`'s `2*CW`, `tagpin`'s `CW`,
      `bridge_cell`'s `DW`, sizing cells no bus of that width touches.
      `-CW 128` grew the whole core/cluster stack for nothing (die
      4576x5600 against 4320x4704).
    * PHANTOM COEFFICIENTS -- `mul_cell`'s `2*DW` over a cell with ONE bus
      on ONE pin, and the same `2*DW` on `alu_cell`, `regf_cell` and
      `xbar_cell`.  `-DW 128` gives die 8784x6624 with them and 6736x6624
      without -- a pair now taken from the repository's OWN HISTORY
      (`git worktree` at 53f1f91b and 56ffdb4e, `-dry` needing no build)
      rather than transcribed, because the second figure was written down by
      hand as 7760x6624 in the very commit whose subject was "re-measure
      every table", understating the saving by half.  The guard this
      replaces could not see any of them: it asked whether a knob APPEARS,
      never whether its coefficient matches the endpoint multiplicity.
    * ...and it skipped a leaf absent from the landing map as though it were
      a container, so a leaf that lost its last bus made every one of its
      terms phantom and still read clean.

    Asking what the INSTRUMENT cannot witness is what those cost, so this
    one is direct rather than differential: for every leaf, the declared
    size must EQUAL `_dim` of the worst per-pin bit sum -- the same quantity
    `check_bus_faces` accumulates, taken from the recorder rather than from
    a model of it.  There is no perturbation and no regime to choose (the
    open question I had about the previous guard's coverage): a wrong
    expression is falsified wherever it disagrees, and the settings below
    only have to make the knobs distinguishable and put each in turn on top.

    The FOURTH round is the one the size comparison cannot reach, and it is
    the same lesson a level down: this check reduced the design to cell TYPES
    before comparing, and an unwired OCCURRENCE of a type that is wired
    elsewhere is invisible to that however exact it is about the type.
    `l2/tag` was instantiated and named by no bus while four live L1 tags
    answered for `tag_cell` (Codex P2, #930).  So the leaf INSTANCE PATHS are
    asked of the vehicle and each one must appear as some bus's endpoint
    before anything is reduced -- which immediately found the other one, the
    chain-start `rtr/fi_in` that `nl_` never reaches because there is no hop
    upstream of the first.  Both are now wired; the check is what keeps them
    so, and mutation-tested in both directions (delete either bus and it
    names the INSTANCE, in every regime).

    `fifo_cell`'s `2*DW` SURVIVES, which is the check earning its keep: at
    the chain head `fi_in.in` receives `nl_` AND `mr`, so two DW buses land
    on one pin and the coefficient is the real multiplicity.  Likewise
    `xbar_cell` legitimately depends on `NQ`/`NC` through
    `percl = ceil(NIO/(NQ*NC))`, because `xbar.p_in` aggregates that many
    peripherals -- a dependency on a topology dial that is honest, which I
    could not settle by reading."""
    stub = tmp_path / "stub.tcl"
    stub.write_text(
        "namespace eval buda {}\n"
        "foreach p {set_die add_cell add_inst_to_cell add_inst} "
        "{ proc buda::$p args {} }\n"
        "source [file join {%s} flow tcl soc_lib.tcl]\n"
        "soc_vehicle::configure [lrange $argv 0 end]\n"
        "soc_vehicle::build_hierarchy\n"
        "foreach path [soc_vehicle::leaf_paths] { puts \"LEAF|$path\" }\n"
        "foreach path [split [read stdin] \"\\n\"] {\n"
        "    if {[string trim $path] eq \"\"} continue\n"
        "    puts \"[soc_vehicle::cell_at $path]|$path\"\n"
        "}\n" % _ROOT)

    def declared_sizes(flat):
        """Every LEAF cell's (w, h) and `_dim` of one bit, from the vehicle."""
        probe = tmp_path / ("sz_%s.tcl" % "_".join(flat))
        probe.write_text(
            "source [file join {%s} flow tcl soc_lib.tcl]\n"
            "soc_vehicle::configure [list %s]\n"
            "foreach c [lsort [array names soc_vehicle::LEAF]] "
            "{ puts \"$c [soc_vehicle::size $c]\" }\n"
            % (_ROOT, " ".join(flat)))
        r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                           encoding="utf-8", cwd=tmp_path, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        out = {}
        for ln in r.stdout.split("\n"):
            f = ln.split()
            if len(f) == 3:
                out[f[0]] = (int(f[1]), int(f[2]))
        assert out, r.stdout
        return out

    def dim(bits, flat):
        """`soc_vehicle::_dim`, asked of the vehicle rather than copied."""
        probe = tmp_path / ("dim_%s_%d.tcl" % ("_".join(flat), bits))
        probe.write_text(
            "source [file join {%s} flow tcl soc_lib.tcl]\n"
            "soc_vehicle::configure [list %s]\n"
            "puts [soc_vehicle::_dim %d]\n" % (_ROOT, " ".join(flat), bits))
        r = subprocess.run(["tclsh", str(probe)], capture_output=True,
                           encoding="utf-8", cwd=tmp_path, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return int(r.stdout.strip())

    def check(knobs, tag):
        flat = ["NQ", "1"] + [s for k, v in knobs.items() for s in (k, str(v))]
        dashed = [s for k, v in knobs.items() for s in ("-%s" % k, str(v))]
        rec = tmp_path / ("faces_%s.buda" % tag)
        # The route's verdict is irrelevant: this audits DECLARATIONS, and a
        # lopsided regime strands bits by design.  What must hold is that the
        # flow reached its buses, which the emptiness check is.
        r = subprocess.run(["tclsh", str(_VEHICLE), "1", *dashed],
                           capture_output=True, encoding="utf-8",
                           errors="replace", cwd=tmp_path, timeout=900,
                           env={**os.environ, "BUDA_RECORD": str(rec)})
        buses = [(f[1], f[2], f[3]) for f in
                 (ln.split() for ln in (rec.read_text() if rec.exists() else "")
                  .splitlines()) if f and f[0] == "add_bus"]
        assert buses, ("the flow declared no bus in regime %s" % tag,
                       r.stdout[-2000:], r.stderr[-2000:])
        paths = sorted({p for _n, d, rr in buses for p in (d, rr)})
        out = subprocess.run(["tclsh", str(stub), *flat],
                             input="\n".join(paths), capture_output=True,
                             encoding="utf-8", cwd=tmp_path, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        cell_of = {}
        leaf_paths = []
        for ln in out.stdout.splitlines():
            if "|" in ln:
                cell, path = ln.split("|", 1)
                if cell == "LEAF":
                    leaf_paths.append(path)
                else:
                    cell_of[path] = cell
        assert leaf_paths, out.stdout
        # the per-PIN sum, which is what `check_bus_faces` accumulates
        per_pin = collections.Counter()
        for name, d, rr in buses:
            m = re.search(r"\[(\d+)\]", name)
            assert m, name
            for path in (d, rr):
                per_pin[path] += int(m.group(1))
        # PER INSTANCE first, because the reduction below cannot see this.
        # An endpoint is `<instance path>.<pin>`, so a leaf instance is wired
        # iff some bus names one of its pins.
        landed = {p.split(".")[0] for p in per_pin}
        dead = [(tag, lp) for lp in leaf_paths if lp not in landed]

        worst = {}
        for path, bits in per_pin.items():
            cell = cell_of.get(path)
            assert cell, ("no cell for endpoint %s -- the vehicle's own "
                          "cell_at could not resolve it" % path)
            worst[cell] = max(worst.get(cell, 0), bits)

        wrong = []
        for cell, (w, h) in declared_sizes(flat).items():
            if cell not in worst:
                wrong.append((tag, cell, "declared but NO bus lands on it",
                              (w, h), None))
                continue
            want = dim(worst[cell], flat)
            if (w, h) != (want, want):
                wrong.append((tag, cell, "%d bits on its busiest pin"
                              % worst[cell], (w, h), want))
        return wrong, dead

    # Enough settings to distinguish the knobs and put each in turn on top,
    # plus one with the multiplicities above 1 so an aggregating pin (a tag's
    # `d_in`, `xbar.p_in`) is exercised rather than degenerate.
    regimes = [({"NBANK": 1, "NIO": 1,
                 "DW": 8, "AW": 9, "IW": 10, "CW": 11}, "flat")]
    for knob in ("DW", "AW", "IW", "CW"):
        regimes.append(({"NBANK": 1, "NIO": 1,
                         **dict({"DW": 8, "AW": 9, "IW": 10, "CW": 11},
                                **{knob: 200})}, knob.lower()))
    regimes.append(({"NBANK": 3, "NBANK2": 5, "NIO": 6,
                     "DW": 8, "AW": 9, "IW": 10, "CW": 11}, "aggregating"))
    found, orphans = [], []
    for knobs, tag in regimes:
        w, d = check(knobs, tag)
        found += w
        orphans += d

    # The per-INSTANCE verdict first, because it is the one the size
    # comparison below structurally cannot reach.
    assert not orphans, (
        "a leaf INSTANCE carries no bus: it occupies die area, is counted by "
        "`leaf_census` as workload, and routes nothing.  Wire it or stop "
        "instantiating it.  This is the check that was missing when `l2/tag` "
        "sat unwired behind four live L1 tags and when `rtr/fi_in` at the "
        "chain start had nothing upstream of it (Codex P2, #930) -- both "
        "invisible to any audit that reduces the design to cell TYPES, "
        "however exact it is about the type.", orphans)
    assert not found, (
        "a leaf's declared face is not the bits that land on it.  More than "
        "the busiest pin needs is whitespace that pollutes that knob's "
        "experiment; less is caught at declaration by check_bus_faces.",
        found)


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
    # `bridge_cell` has TWO kinds of pin -- the `NIO*CW` star into the pads
    # and one `DW` injection into the NoC chain start -- so its face is the
    # busier of them and `-DW` moves it once DW passes `NIO*CW`.  The bus
    # exists because the chain's first hop has no hop upstream of it, so its
    # inbound fifo was a leaf instance with no net at all (Codex P2, #930).
    assert _sizes(tmp_path, ("DW", 128), cells) == [1048, 1048, 2072, 2072,
                                                    536, 536]

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

    Asserted on ENDPOINT IDENTITY, which took two goes to get right, and the
    two wrong versions are the reason the docstring is this long:

    * v1 read the banner's advertised bus count at two `NBANK` settings and
      asserted the difference.  But `describe` COMPUTES that count by
      arithmetic from the very knob being varied, and `-dry` exits before a
      single bus is declared — re-wiring only `bank_0` left every test in
      this file passing.  A count a flow computes cannot witness what that
      flow declared.
    * v2 compared the advertised count against the bundler's hbundle count,
      which does come from the declarations.  Better, and still the wrong
      INSTRUMENT (Codex P2 again): an AGGREGATE cannot witness WHICH
      endpoint a bus reached, so whether it catches a bank being
      disconnected is a property of the BUNDLER rather than of the design.
      Here it happens to catch it — `bank_$b` → `bank_0` was MEASURED at
      54 declared buses against 50 hbundles, because STRICT grouping merges
      the duplicate-endpoint bus that takes the dead bank's place, and the
      degenerate substitutions are refused outright ("used as both driver
      and receiver").  Saying instead that the mutation SLIPS PAST the count
      is the claim this docstring first made, and it contradicted the four
      measurements recorded beside it (Codex P2 a third time): the count is
      adequate on today's grouping rule, and the point is that it should not
      have to be.

    So this reads the endpoints themselves, out of the recorder
    (`BUDA_RECORD` writes every command as it reaches `do_command`, so the
    `add_bus` lines carry the paths the flow actually passed), and requires
    each configured bank to drive its own read bus and receive its own
    address bus — no coupling to how nets are grouped.  The count check is
    kept as the second half, since it is what catches `describe` going
    stale.  Verified non-vacuous against the real data rather than by a flow
    mutation, since none isolates it: the recorded 54-bus list passes, and
    the same list with one bank's two lines removed fails naming the bank."""
    # `declared()` inherits `os.environ`, which I have twice flagged as an
    # open soft spot; the audit rather than the gesture: of the 31 `BUDA_*`
    # knobs in the tree, none can change what this test asserts.  It reads
    # `add_bus` lines -- DECLARATIONS, made before any knob-governed stage
    # runs; `BUDA_RECORD_NOTE`, the only knob that writes into the record
    # file, emits `#` comment lines the parser skips; `BUDA_UNIT_CHECK` can
    # abort a run, which the returncode assert catches LOUDLY; and
    # `BUDA_HIER_DEEP_FIRST` inverts a PLANNER level key, not the bundler,
    # so the hbundle count compared below is untouched.  Everything else
    # governs topology/planner/NUTS/threads/output.  Scrubbing the
    # environment would look careful and check nothing new.
    def declared(*knobs):
        rec = tmp_path / ("rec_%s.buda" % ("_".join(map(str, knobs)) or "def"))
        r = subprocess.run(["tclsh", str(_VEHICLE), "1", *map(str, knobs)],
                           capture_output=True, encoding="utf-8",
                           errors="replace", cwd=tmp_path, timeout=900,
                           env={**os.environ, "BUDA_RECORD": str(rec)})
        assert r.returncode == 0, r.stdout + r.stderr
        buses = []
        for ln in rec.read_text().splitlines():
            f = ln.split()
            if f and f[0] == "add_bus":
                buses.append((f[1], f[2], f[3]))      # name, driver, receiver
        said = re.search(r"(\d+) buses", r.stdout)
        built = re.search(r"HierBundler: (\d+) hbundles", r.stdout)
        cl = re.search(r"(\d+) clusters", r.stdout)
        assert said and built and cl, r.stdout
        return buses, int(said.group(1)), int(built.group(1)), int(cl.group(1))

    def assert_banks_wired(buses, clusters, nbank, nbank2):
        # The recorder hands over (name, driver, receiver) TRIPLES, so the
        # association is asserted, not just the membership: two independent
        # sets are satisfied by a CROSS-WIRED bank (an l1i bank driving the
        # l1d tag, an address arriving from the wrong tag) with every count
        # and every endpoint still present (Codex P2, #930).
        pairs = {(d, r) for _n, d, r in buses}
        # every L1 of every cluster, then the L2.  The cluster count comes
        # from the banner rather than from NQ*NC restated here, so this walks
        # whatever design the flow built.
        holders = [("quad_0/cl_%d/%s" % (c, side), "tag", nbank)
                   for c in range(clusters) for side in ("l1i", "l1d")]
        holders.append(("l2", "mc", nbank2))
        for holder, arb, n in holders:
            for b in range(n):
                bank = "%s/bank_%d" % (holder, b)
                want_addr = ("%s/%s.b_out" % (holder, arb), "%s.a_in" % bank)
                want_read = ("%s.out" % bank, "%s/%s.d_in" % (holder, arb))
                assert want_addr in pairs, (
                    "no address bus runs %s -> %s: a configured bank is "
                    "disconnected or CROSS-WIRED, which is what this test "
                    "exists for" % want_addr)
                assert want_read in pairs, (
                    "no read bus runs %s -> %s" % want_read)

    base, base_said, base_built, clusters = declared()
    assert base_said == base_built, (base_said, base_built)
    assert_banks_wired(base, clusters, 2, 4)           # the defaults

    # ...and raising either knob wires the banks it adds, not just counts
    # them.  The expected delta is DERIVED from what a bank costs -- an
    # address bus and a read bus -- times how many caches gain one: every L1
    # of every cluster for `NBANK`, the single L2 for `NBANK2`.  Restating it
    # as a literal 8 and 4 would have to be re-derived by hand the next time
    # a bank grows a third bus.
    for knobs, nbank, nbank2, caches in ((("-NBANK", 3), 3, 4, 2 * clusters),
                                         (("-NBANK2", 6), 2, 6, 1)):
        added = (nbank - 2) * caches + (nbank2 - 4) * 1
        buses, said, built, cl = declared(*knobs)
        assert cl == clusters, (knobs, cl, clusters)
        assert said == built, (knobs, said, built)
        assert built == base_built + 2 * added, (knobs, base_built, built,
                                                 2 * added)
        assert_banks_wired(buses, clusters, nbank, nbank2)


def _die(out):
    m = re.search(r"die (\d+)x(\d+)", out)
    assert m, out
    return int(m.group(1)), int(m.group(2))


def test_every_bus_family_wires_one_kind_of_thing_to_one_kind(tmp_path):
    """The cross-wire check, generalized off the banks — and the reason it
    can be general is that it restates NOTHING.

    `test_every_configured_bank_carries_a_net` asserts the exact expected
    pair per holder and bank, which is what catches a bank cross-wired to
    the other cache's tag.  That form does not generalize: writing the
    expected endpoints for all 24 bus families would be a second
    implementation of `build_buses`, and a hand-kept twin of the thing under
    test is exactly what `_fill`/`leaf_paths` were unified to avoid.

    What generalizes is an INTERNAL consistency claim.  Every bus of one
    family must run between the same KINDS of endpoint — same holder, same
    leaf, same pin, with a trailing index normalized away (`bank_0` and
    `bank_3` are one kind; `p_0` and `p_2` are one kind).  Measured on the
    recorder at the defaults: 24 families, every one with exactly ONE
    signature.  Nothing here says what any signature SHOULD be, so a
    deliberate re-wire of a whole family passes and only an INCONSISTENT one
    fails — which is the half the per-bank test cannot cover for the other
    nine cell types.

    It is the answer to a question I raised on the PR rather than one a
    reviewer asked: the per-instance landing guard tests PRESENCE, and this
    is as far towards CORRECTNESS as one can go without restating the
    design.  What it still cannot see is a swap between two siblings of the
    same kind in the same holder — `l1i/bank_0` and `l1i/bank_1` trading
    buses gives the identical signature — and that is precisely what the
    per-bank pair assertion is for.  Neither test subsumes the other."""
    rec = tmp_path / "families.buda"
    r = subprocess.run(["tclsh", str(_VEHICLE), "2"],
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=900,
                       env={**os.environ, "BUDA_RECORD": str(rec)})
    assert r.returncode == 0, r.stdout + r.stderr
    buses = [(f[1], f[2], f[3]) for f in
             (ln.split() for ln in rec.read_text().splitlines())
             if f and f[0] == "add_bus"]
    assert buses, r.stdout

    def kind(path):
        """`quad_0/cl_1/l1i/bank_3.out` -> `l1i/bank_#.out` — the holder and
        leaf with a trailing index collapsed, which is what makes an indexed
        family one kind rather than N."""
        inst, pin = path.rsplit(".", 1)
        parts = inst.split("/")
        leaf = re.sub(r"_\d+$", "_#", parts[-1])
        holder = re.sub(r"_\d+$", "_#", parts[-2]) if len(parts) > 1 else ""
        return "%s/%s.%s" % (holder, leaf, pin)

    sigs = collections.defaultdict(set)
    for name, drv, rcv in buses:
        family = re.sub(r"_\d+(_\d+)*$", "", name.split("[")[0])
        sigs[family].add((kind(drv), kind(rcv)))

    assert len(sigs) > 20, sigs.keys()          # the families are all present
    inconsistent = {f: s for f, s in sigs.items() if len(s) > 1}
    assert not inconsistent, (
        "a bus family runs between more than one KIND of endpoint, so some "
        "bus of it is wired somewhere its siblings are not — a cross-wire "
        "that every count, every set and every per-instance landing check "
        "accepts", {f: sorted(s) for f, s in inconsistent.items()})


def test_a_nonphysical_sizing_knob_is_refused_at_declaration(tmp_path):
    """The MIRROR of the zero-width guard, on the PHYSICAL sizing knobs
    (Codex P2, #930).  `-DW 0` was refused because it reported a CLEAN design
    over a deleted datapath; the count loop that fixed it left `BITPITCH`,
    `PAD`, `M` and `GAP` unchecked — four knobs, not the two the finding
    named, which is why the fix is a loop and this test sweeps all four.

    What breaks here is NOT what broke for `-DW 0`, and measuring the
    difference is what set the per-knob rules.  A ROUTED run with
    `-BITPITCH 0` or `-PAD -100` comes back loudly dirty (NQ=1: 89 ovl /
    1408 unplaced, 10 ovl / 1008 unplaced), so neither ever claimed success.
    Two other things broke:

      (a) the FACE GUARD was complicit — `check_bus_faces` prices a face
          through the same `_dim` that had just multiplied by zero, so this
          vehicle's advertised declaration-time too-narrow-face report could
          not fire.  A guard cannot audit the arithmetic it is written in.
      (b) `-dry`, the advertised way to sweep geometry without paying for a
          route, reported a plausible 1136x480 die and no complaint.

    So the assertion is on the DECLARATION, not on the routed endpoint: the
    run must stop before a route it would only fail anyway.

    ZERO IS ALLOWED where it was MEASURED honest, which is the other half —
    a guard that refuses working configurations is its own defect.  `-M 0`
    routes CLEAN; `-PAD 0` (a face exactly its bits) and `-GAP 0`
    (abutment) route DIRTY but report it with a non-zero exit, and an
    honest failure is a legitimate experiment to run.  Only `BITPITCH`
    takes `> 0`, since a zero pitch makes a face stop depending on its bits
    at all."""
    # every defect the guard claims to catch, re-injected
    for knob, val in (("-BITPITCH", 0), ("-BITPITCH", -1), ("-BITPITCH", "abc"),
                      ("-PAD", -100), ("-PAD", -1),
                      ("-GAP", -1), ("-M", -5)):
        r = _run(tmp_path, 1, knob, val, "-dry")
        assert r.returncode != 0, (knob, val, r.stdout)
        out = r.stdout + r.stderr
        assert "soc_vehicle: %s must be a number" % knob.lstrip("-") in out, \
            (knob, val, out)

    # ...and the values measured legal are still accepted, -dry being enough
    # since the refusal above is a DECLARATION-time one
    for knob, val in (("-GAP", 0), ("-M", 0), ("-PAD", 0), ("-BITPITCH", 2.5)):
        r = _run(tmp_path, 1, knob, val, "-dry")
        assert r.returncode == 0, (knob, val, r.stdout + r.stderr)
        assert _die(r.stdout), (knob, val, r.stdout)


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


# ── the top-level floorplan knob ──────────────────────────────────────────
_PROBE_TOP = (
    "source [file join {%s} flow tcl soc_lib.tcl]\n"
    "soc_vehicle::configure [lrange $argv 0 end]\n"
    "array set T [soc_vehicle::_top_geom]\n"
    "puts \"DIE $T(diew) $T(dieh) $T(nc) $T(nr) $T(holes) $T(band) $T(util)\"\n"
    "foreach inst $T(pos) {\n"
    "    lassign $inst name cell x y\n"
    "    lassign $soc_vehicle::SZ($cell) w h\n"
    "    puts \"BLK $name $cell $x $y $w $h\"\n"
    "}\n")


def _top_geom(tmp_path, *knobs):
    probe = tmp_path / ("top_%s.tcl" % "_".join(map(str, knobs)))
    probe.write_text(_PROBE_TOP % _ROOT)
    r = subprocess.run(["tclsh", str(probe), *map(str, knobs)],
                       capture_output=True, encoding="utf-8", cwd=tmp_path,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    die = None
    blocks = []
    for ln in r.stdout.splitlines():
        f = ln.split()
        if f and f[0] == "DIE":
            die = dict(w=int(f[1]), h=int(f[2]), nc=int(f[3]), nr=int(f[4]),
                       holes=int(f[5]), band=int(f[6]), util=float(f[7]))
        elif f and f[0] == "BLK":
            blocks.append((f[1], f[2], int(f[3]), int(f[4]), int(f[5]), int(f[6])))
    assert die and blocks, r.stdout
    return die, blocks


def test_both_layouts_place_every_top_level_block_inside_the_die_apart(tmp_path):
    """`_top_geom` is the ONE walk both the declared die and the placement
    come from, so what has to hold of it is geometric: every quadrant, the
    l2 and the io block inside the die, no two overlapping, and NQ + 2 of
    them -- at every size from 1 to 12 and the two the documents quote, for
    both layouts.  The compact chooser ranks candidates it never places, so
    this is what says the one it picked is a legal floorplan."""
    for nq in list(range(1, 13)) + [16, 32]:
        for layout in ("band", "compact"):
            die, blocks = _top_geom(tmp_path, "NQ", nq, "LAYOUT", layout)
            assert len(blocks) == nq + 2, (nq, layout, blocks)
            names = [b[0] for b in blocks]
            assert names[-2:] == ["l2", "io"] and names[0] == "quad_0"
            for name, _c, x, y, w, h in blocks:
                assert 0 <= x and 0 <= y and x + w <= die["w"] and y + h <= die["h"], \
                    (nq, layout, name, (x, y, w, h), die)
            for i, a in enumerate(blocks):
                for b in blocks[i + 1:]:
                    disjoint = (a[2] + a[4] <= b[2] or b[2] + b[4] <= a[2]
                                or a[3] + a[5] <= b[3] or b[3] + b[5] <= a[3])
                    assert disjoint, (nq, layout, a, b)


def test_the_band_layout_is_the_default_and_the_one_every_table_was_measured_on(tmp_path):
    """The historical placement must stay byte-identical under the knob,
    because every die and wirelength in soc.md was measured on it: no
    `-LAYOUT` is `band`, and `band` gives the dies those tables quote."""
    for nq, die in ((2, "4208x2016"), (8, "6288x4384"), (32, "12528x7936")):
        plain = _run(tmp_path, nq, "-dry")
        band = _run(tmp_path, nq, "-LAYOUT", "band", "-dry")
        assert plain.returncode == 0 and band.returncode == 0, plain.stderr + band.stderr
        assert plain.stdout == band.stdout
        assert ("die %s " % die) in plain.stdout and "layout band" in plain.stdout, plain.stdout
    r = _run(tmp_path, 2, "-LAYOUT", "diagonal", "-dry")
    assert r.returncode != 0 and "LAYOUT must be band or compact" in r.stderr, r.stderr


def test_compact_never_yields_utilization_to_band_and_fills_the_hole_it_can(tmp_path):
    """The chooser's contract: `compact` is never worse than `band` on the
    number it optimizes (the band layout is not among its candidates, so
    this is the claim that its search space contains something at least as
    good at every size), and the two shapes it decides between are both
    reached on the dial -- the l2 + io pair FILLING the short last row's
    hole (NQ = 8: a 3x3 grid, one hole, band 0) and a full grid with the
    pair in a centred band (NQ = 32: 4x8, no hole, band 1).  The empty
    upper-right corner of the historical NQ = 32 floorplan is four empty
    quadrant slots (6x6 for 32); compact has none."""
    for nq in list(range(1, 10)) + [16, 32]:
        band, _ = _top_geom(tmp_path, "NQ", nq, "LAYOUT", "band")
        compact, _ = _top_geom(tmp_path, "NQ", nq, "LAYOUT", "compact")
        assert compact["util"] >= band["util"], (nq, band, compact)
        assert 0.5 <= compact["w"] / compact["h"] <= 2.0, (nq, compact)
    band32, _ = _top_geom(tmp_path, "NQ", 32, "LAYOUT", "band")
    assert band32["holes"] == 4
    c32, _ = _top_geom(tmp_path, "NQ", 32, "LAYOUT", "compact")
    assert c32["holes"] == 0 and c32["band"] == 1, c32
    c8, _ = _top_geom(tmp_path, "NQ", 8, "LAYOUT", "compact")
    assert c8["holes"] == 1 and c8["band"] == 0, c8


def test_compact_ranks_on_the_die_not_on_the_rounded_ratio(tmp_path):
    """Codex P2 on #932: the chooser ranked on the utilization formatted to
    three decimals, so two candidates whose dies differ by 163,584 units of
    area both read 0.892 and the tie-breakers picked the LARGER die -- at
    `16 -NC 2 -NBANK 8 -NBANK2 1 -NIO 32` a 3-column 17488 x 17584
    (307,508,992) over the 4-column 23312 x 13184 (307,345,408).  Every
    candidate holds the same blocks, so the highest utilization is exactly
    the smallest die, and the rank is that integer area now; the ratio is
    formatted only for the report."""
    knobs = ("NQ", 16, "NC", 2, "NBANK", 8, "NBANK2", 1, "NIO", 32, "LAYOUT", "compact")
    die, _ = _top_geom(tmp_path, *knobs)
    assert (die["nc"], die["w"], die["h"]) == (4, 23312, 13184), die
    assert die["w"] * die["h"] == 307_345_408 < 307_508_992
    assert die["util"] == 0.892   # the rounded report, unchanged either way


# ── PACK slice ────────────────────────────────────────────────────────────
_PROBE_SLICE = (
    "namespace eval buda {}\n"
    "proc buda::set_die {w h} { puts \"DIE $w $h\" }\n"
    "proc buda::add_cell {c w h} { puts \"CELL $c $w $h\" }\n"
    "proc buda::add_inst_to_cell {p n c x y} { puts \"KID $p $n $c $x $y\" }\n"
    "proc buda::add_inst {n c p x y} { puts \"TOP $n $c $x $y\" }\n"
    "source [file join {%s} flow tcl soc_lib.tcl]\n"
    "soc_vehicle::configure [lrange $argv 0 end]\n"
    "foreach c [lsort [array names soc_vehicle::LEAF]] {\n"
    "    lassign $soc_vehicle::LEAF($c) vb hb\n"
    "    puts \"LEAF $c [expr {max($vb, $hb)}] [soc_vehicle::_dim [expr {max($vb, $hb)}]]\"\n"
    "}\n"
    "soc_vehicle::build_hierarchy\n")


def _slice_geom(tmp_path, *knobs):
    probe = tmp_path / "slice_probe.tcl"
    probe.write_text(_PROBE_SLICE % _ROOT)
    r = subprocess.run(["tclsh", str(probe), *map(str, knobs)],
                       capture_output=True, encoding="utf-8", cwd=tmp_path,
                       timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    die, cells, kids, leaves = None, {}, collections.defaultdict(list), {}
    for f in (ln.split() for ln in r.stdout.splitlines()):
        if f and f[0] == "DIE":
            die = (int(f[1]), int(f[2]))
        elif f and f[0] == "CELL":
            cells[f[1]] = (int(f[2]), int(f[3]))
        elif f and f[0] == "KID":
            kids[f[1]].append((f[3], int(f[4]), int(f[5])))
        elif f and f[0] == "TOP":
            kids["-"].append((f[2], int(f[3]), int(f[4])))
        elif f and f[0] == "LEAF":
            leaves[f[1]] = (int(f[2]), int(f[3]))
    return die, cells, kids, leaves


def test_a_slice_packing_is_a_legal_floorplan_and_never_worse_than_the_grid(tmp_path):
    """`PACK slice` chooses every container's packing and every leaf's shape
    together, so what has to hold is what a placer's output must: each
    child inside its parent with the margin M, no two siblings closer than
    their channel, every leaf at least its square's area, the leaf aspect
    within ASPECT and the container aspect within 2, and every face still
    hosting its bits plus FACEPAD -- the floor a stretch may not cross,
    MEASURED (see `_leaf_shapes`).  And the one reason it exists: at the
    same knobs its die is never larger than the grid packer's."""
    base = ["NQ", 8, "LAYOUT", "compact", "BITPITCH", 4.0]
    for pad, gap, aspect, cgap in [(10, 4, 1, -1), (24, 12, 1, 4),
                                   (48, 8, 4, -1), (24, 4, 3, 16)]:
        knobs = base + ["PAD", pad, "GAP", gap, "M", gap, "PACK", "slice",
                        "ASPECT", aspect, "CGAP", cgap]
        die, cells, kids, leaves = _slice_geom(tmp_path, *knobs)
        grid_die, *_ = _slice_geom(tmp_path, *(base + ["PAD", pad, "GAP", gap, "M", gap]))
        tag = (pad, gap, aspect, cgap)
        assert die[0] * die[1] <= grid_die[0] * grid_die[1], (tag, die, grid_die)
        for parent, ks in kids.items():
            pw, ph = die if parent == "-" else cells[parent]
            ch = gap if parent in ("-",) else (
                cgap if cgap >= 0 and parent in ("cluster_cell", "quad_cell") else gap)
            m = 0 if parent == "-" else gap
            boxes = []
            for c, x, y in ks:
                w, h = cells[c]
                assert m <= x and m <= y and x + w <= pw - m and y + h <= ph - m, \
                    (tag, parent, c, (x, y, w, h), (pw, ph))
                boxes.append((x, y, w, h, c))
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    apart = (a[0] + a[2] + ch <= b[0] or b[0] + b[2] + ch <= a[0]
                             or a[1] + a[3] + ch <= b[1] or b[1] + b[3] + ch <= a[1])
                    assert apart, (tag, parent, a, b)
            if parent != "-":
                assert max(pw, ph) <= 2 * min(pw, ph), (tag, parent, pw, ph)
        for c, (bits, side) in leaves.items():
            w, h = cells[c]
            assert w * h >= side * side, (tag, c, w, h, side)
            assert max(w, h) <= aspect * min(w, h) * 1.02, (tag, c, w, h)
            assert min(w, h) >= min(side, int(-(-bits * 4.0 // 1)) + 10), (tag, c, w, h)


def test_the_grid_packer_is_the_default_and_refuses_the_slice_knobs(tmp_path):
    """Every recorded table was measured on the grid packer, so it stays the
    default, and a slice-only knob handed to it is an ERROR rather than a
    silently ignored word."""
    die, *_ = _slice_geom(tmp_path, "NQ", 2)
    assert die == (4208, 2016), die
    for bad in (["ASPECT", 2], ["CGAP", 8], ["PACK", "tight"], ["ASPECT", 5]):
        probe = tmp_path / "bad.tcl"
        probe.write_text("source [file join {%s} flow tcl soc_lib.tcl]\n"
                         "soc_vehicle::configure [lrange $argv 0 end]\n" % _ROOT)
        r = subprocess.run(["tclsh", str(probe), "NQ", "2", *map(str, bad)],
                           capture_output=True, encoding="utf-8", cwd=tmp_path,
                           timeout=60)
        assert r.returncode != 0, bad


# ── one cell routed alone (soc_local.tcl) ─────────────────────────────────
_LOCAL = _ROOT / "flow" / "tcl" / "soc_local.tcl"


def test_a_fixed_plan_is_the_one_the_local_driver_lists(tmp_path):
    """`soc_local.tcl -list` enumerates a container's slicing plans and
    `-FIX {<cell> k}` builds plan k -- through the SAME path, since a fixed
    parent takes its children at their smallest shapes and those may be
    rotated from what the free search picked.  So plan k must be one
    geometry in the listing and in the configured design, or a plan
    measured alone is not the plan the chip routes."""
    knobs = ["-PAD", "10", "-GAP", "4", "-M", "4", "-PACK", "slice"]
    r = subprocess.run(["tclsh", str(_LOCAL), "cluster_cell", "-list", "-slack", "0.5", *knobs],
                       capture_output=True, encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    plans = {}
    for ln in r.stdout.splitlines():
        f = ln.split(None, 5)
        if f and f[0] == "PLAN":
            plans[f[1]] = (int(f[2]), int(f[3]),
                           [tuple(map(int, p.split())) for p in re.findall(r"\{(\d+ \d+)\}", f[5])])
    assert len(plans) > 50, len(plans)
    for k in ("0", "7", "48", "grid"):
        _die, cells, kids, _ = _slice_geom(
            tmp_path, "NQ", 8, "LAYOUT", "compact", "PAD", 10, "GAP", 4, "M", 4,
            "PACK", "slice", "FIX", "cluster_cell %s" % k, "FIXSLACK", 0.5)
        w, h, pos = plans[k]
        assert cells["cluster_cell"] == (w, h), (k, cells["cluster_cell"], (w, h))
        assert [(x, y) for _c, x, y in kids["cluster_cell"]] == pos, (k, kids["cluster_cell"], pos)


def test_the_local_driver_measures_the_connections_the_netlist_has(tmp_path):
    """One core routed alone, measured from the STORED tables by
    `tools/cell_face_demand.py`: the bits between its leaves and to its
    four ports are netlist facts (`build_buses`), so the reader must find
    exactly them -- whatever the route did with them."""
    bdb = tmp_path / "core.bdb"
    r = subprocess.run(["tclsh", str(_LOCAL), "core_cell", "-plan", "current", "-noheal",
                        "-bdb", str(bdb), "-PAD", "10", "-GAP", "4", "-M", "4"],
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=300)
    assert r.returncode == 0 and "LOCAL cell=core_cell" in r.stdout, r.stdout[-3000:] + r.stderr
    import sys
    sys.path.insert(0, str(_ROOT / "tools"))
    import cell_face_demand
    m = cell_face_demand.measure(str(bdb))
    conn = m["connections"]
    assert conn["u/alu|u/dec"] == 32 and conn["u/alu|u/regf"] == 64 \
        and conn["u/mul|u/regf"] == 32, conn
    ports = sorted(v for k, v in conn.items() if k.startswith("pt_"))
    assert ports == [16, 16, 32, 32], conn
    assert set(m["demand"]) == {"u/dec", "u/alu", "u/mul", "u/regf"}
    assert m["wl"] > 0


def test_faces_sizes_a_leaf_for_all_its_pins_and_only_the_leaf_that_needs_it(tmp_path):
    """The per-pin rule sizes a face from a leaf's heaviest pin, which
    assumes every pin gets a face to itself; `FACES` spreads a leaf's pins
    (as `build_buses` wires them) over its faces and sizes the faces for
    the heaviest one.  At the defaults exactly one leaf has more pin load
    than that: `regf_cell`, 4 x DW + AW on four faces, whose best spread
    puts DW + AW = 48 bits on one face.  FACES 4 makes it the square that
    holds 48 on every face, FACES 2 widens only its N/S pair, and every
    other leaf is left at its per-pin size -- the derivation reads the
    real buses, so a leaf it grows is a leaf the netlist overloads."""
    probe = tmp_path / "faces.tcl"
    probe.write_text(
        "source [file join {%s} flow tcl soc_lib.tcl]\n"
        "soc_vehicle::configure [lrange $argv 0 end]\n"
        "foreach c [lsort [array names soc_vehicle::LEAF]] "
        "{ puts \"SZ $c [soc_vehicle::size $c] [soc_vehicle::_dim 48]\" }\n" % _ROOT)

    def sizes(*knobs):
        r = subprocess.run(["tclsh", str(probe), "NQ", "2", *map(str, knobs)],
                           capture_output=True, encoding="utf-8", cwd=tmp_path, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        out, d48 = {}, None
        for f in (ln.split() for ln in r.stdout.splitlines()):
            if f and f[0] == "SZ":
                out[f[1]] = (int(f[2]), int(f[3]))
                d48 = int(f[4])
        return out, d48

    base, d48 = sizes()
    four, _ = sizes("FACES", 4)
    two, _ = sizes("FACES", 2)
    grown4 = {c for c in base if four[c] != base[c]}
    grown2 = {c for c in base if two[c] != base[c]}
    assert grown4 == {"regf_cell"} and grown2 == {"regf_cell"}, (grown4, grown2)
    assert four["regf_cell"] == (d48, d48), four["regf_cell"]
    assert two["regf_cell"] == (d48, base["regf_cell"][1]), two["regf_cell"]
    bad = subprocess.run(["tclsh", str(probe), "NQ", "2", "FACES", "3"],
                         capture_output=True, encoding="utf-8", cwd=tmp_path, timeout=60)
    assert bad.returncode != 0 and "FACES" in bad.stdout + bad.stderr


def test_every_local_port_sits_inside_the_die_apart(tmp_path):
    """`soc_local.tcl` puts a port per external bus in a ring round the
    cell.  When one side's ports need more length than the cell's edge
    they start a further tier out and the ring grows, rather than being
    slid past the cell's end -- which placed the io block's last west port
    beyond the die (Codex P2 on #961).  Every port inside the die, no two
    top-level blocks overlapping."""
    import sqlite3
    bdb = tmp_path / "io.bdb"
    r = subprocess.run(["tclsh", str(_LOCAL), "io_blk_cell", "-plan", "current",
                        "-noheal", "-bdb", str(bdb)],
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr
    con = sqlite3.connect(str(bdb))
    meta = dict(con.execute("SELECT key, value FROM meta WHERE key IN ('die_w','die_h')"))
    dw, dh = float(meta["die_w"]), float(meta["die_h"])
    top = list(con.execute("SELECT name, x1, y1, x2, y2 FROM component WHERE parent_id IS NULL"))
    ports = [b for b in top if b[0].startswith("pt_")]
    assert len(ports) >= 5, top
    for name, x1, y1, x2, y2 in top:
        assert 0 <= x1 and 0 <= y1 and x2 <= dw and y2 <= dh, (name, (x1, y1, x2, y2), (dw, dh))
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            assert not (a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]), (a, b)


def test_a_plan_search_matches_resamples_by_a_unique_signature(tmp_path):
    """`tools/soc_plan_search.py` re-samples a plan at perturbed knobs and
    must find the SAME arrangement there.  Pairwise child order mapped the
    cluster's 204 plans onto 156 keys, so some resamples ran another plan
    (Codex P1 on #961); `enum_plans` names each plan's slicing tree by
    child TYPE, which is unique and survives a change of sizes.  The grid
    baseline also drops the knobs the grid packer refuses (Codex P2)."""
    import sys
    sys.path.insert(0, str(_ROOT / "tools"))
    import soc_plan_search as sps
    knobs = sps.knob_list("-PAD 10 -GAP 4 -M 4 -FACES 4 -PACK slice -FIXSLACK 0.5")
    base = sps.list_plans("cluster_cell", knobs, 0.5)
    assert len(base) > 100 and len({p["sig"] for p in base}) == len(base)
    pert = sps.list_plans("cluster_cell", sps.set_knob(knobs, "PAD", 11), 0.5)
    by = {p["sig"]: p for p in pert}
    hit = [p for p in base if p["sig"] in by]
    assert len(hit) > 0.8 * len(base)
    g = sps.grid_knobs(sps.knob_list("-PAD 24 -GAP 12 -M 12 -PACK slice -ASPECT 2 -CGAP 4 -FIXSLACK 0.5"))
    r = subprocess.run(["tclsh", str(_VEHICLE), "8", "-LAYOUT", "compact", *g, "-dry"],
                       capture_output=True, encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_second_review_round_fixes(tmp_path):
    """Codex's second round on #961.  (a) FACES 2 widens regf's N/S pair,
    and the slice search must not rotate that load onto the short faces.
    (b) A plan missing a perturbed sample is ranked after every complete
    one and never healed, since its mean is over fewer runs.  (c) The local
    driver's default reference exists at NQ 1 (there is no quad_1)."""
    import sys
    die, cells, kids, leaves = _slice_geom(
        tmp_path, "NQ", 8, "LAYOUT", "compact", "PAD", 10, "GAP", 4, "M", 4,
        "PACK", "slice", "FACES", 2)
    w, h = cells["regf_cell"]
    assert w > h, cells["regf_cell"]
    sys.path.insert(0, str(_ROOT / "tools"))
    import soc_plan_search as sps
    s = lambda u: dict(unpl=u, ovl=0)
    top = [dict(k=1, area=10, samples=[s(100)]),                  # incomplete, best score
           dict(k=2, area=10, samples=[s(300), s(300), s(300)]),
           dict(k=3, area=10, samples=[s(200), s(250), s(260)])]
    sps.rank_resampled(top, 3)
    assert [p["k"] for p in top] == [3, 2, 1] and not top[-1]["complete"]
    r = subprocess.run(["tclsh", str(_LOCAL), "core_cell", "-list", "-NQ", "1"],
                       capture_output=True, encoding="utf-8", cwd=tmp_path, timeout=120)
    assert r.returncode == 0 and "PLAN grid" in r.stdout, r.stdout + r.stderr


def test_third_review_round_fixes(tmp_path, monkeypatch):
    """Codex's third round on #961.  (a) A run that exits by itself with no
    verdict is an ERROR, not a timeout, and is not cached, so a rerun tries
    again; only a run the budget cut off is a timeout.  (b) A cached run is
    keyed on the implementation it measured, so a checkout or a rebuild
    cannot replay results of other code."""
    import sys
    sys.path.insert(0, str(_ROOT / "tools"))
    import soc_plan_search as sps
    none = sps.parse("soc.tcl: something went wrong\n")
    assert sps.classify(dict(none), False, 1).get("error")
    assert not sps.classify(dict(none), False, 1).get("timeout")
    assert sps.classify(dict(none), True, -9).get("timeout")
    assert not sps.classify(sps.parse("soc.tcl: clean -- x\n"), False, 0).get("error")
    # a stand-in for btcl that dies at once without a verdict
    fake = tmp_path / "btcl"
    fake.write_text("#!/bin/sh\necho 'engine crashed'\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sps, "BTCL", fake)
    (tmp_path / "logs").mkdir()
    cache = sps.Cache(tmp_path / "runs.json")
    r = sps.chip(cache, tmp_path / "logs", ["-PAD", "10"], timeout=30)
    assert r.get("error") and r["rc"] == 3 and not r.get("timeout")
    assert cache.d == {}
    # the key carries the implementation fingerprint
    fp = sps.implementation()
    assert len(fp) == 16
    fake.write_text("#!/bin/sh\necho 'soc.tcl: clean -- done'\n")
    r = sps.chip(cache, tmp_path / "logs", ["-PAD", "10"], timeout=30)
    assert r["clean"] and len(cache.d) == 1
    assert next(iter(cache.d)).endswith(f"#impl={fp}")


def test_fourth_review_round_vehicle_fixes(tmp_path):
    """The Claude Code review of #961, the vehicle half.  (a) Under FIX the
    shared leaves keep one shape, so every listed plan builds the geometry
    it is listed with even where leaves may stretch (27 of 29 sampled
    indices built another at PAD 48 ASPECT 4).  (b) The container aspect
    cap filters BEFORE the curve is thinned, so KEEP 3 packs, and KEEP 2
    (which divided by zero) is refused.  (c) Under FACES 4 a stretch keeps
    the short face at the face load.  (d) More than 10 children under PACK
    slice is refused, not left to a 3^n enumeration.  (e) The track period
    `-at chip` keeps is every layer's pitch LCM per direction."""
    script = tmp_path / "chk.tcl"
    script.write_text(f"""
source {{{_ROOT / 'flow' / 'tcl' / 'soc_lib.tcl'}}}
set base {{NQ 8 LAYOUT compact PACK slice PAD 48 GAP 8 M 8 ASPECT 4 FIXSLACK 0.5}}
soc_vehicle::configure [dict merge $base {{FIX {{cluster_cell 0}}}}]
set plans [soc_vehicle::enum_plans cluster_cell 0.5]
set n [llength $plans]
set bad 0
foreach k [list 0 6 17 40 [expr {{$n - 1}}]] {{
    lassign [lindex $plans $k] W H pos
    soc_vehicle::configure [dict merge $base [list FIX [list cluster_cell $k]]]
    set got [list {{*}}$::soc_vehicle::SZ(cluster_cell) [lindex $::soc_vehicle::PL(cluster_cell) 1]]
    if {{$got ne [list $W $H $pos]}} {{ incr bad }}
}}
puts "PLANS $n BAD $bad"
# `configure` keeps every knob an earlier call set, so each call below
# states the ones the loop above changed
set reset {{FIX {{}} ASPECT 1 KEEP 12 FIXSLACK 0.5}}
soc_vehicle::configure [dict merge $reset {{NQ 8 LAYOUT compact PACK slice PAD 10 GAP 4 M 4 KEEP 3}}]
puts "KEEP3 $::soc_vehicle::TOP(diew)"
puts "KEEP2 [catch {{soc_vehicle::configure [dict merge $reset {{KEEP 2}}]}} m] $m"
puts "NIO [catch {{soc_vehicle::configure [dict merge $reset {{NIO 12}}]}} m] $m"
soc_vehicle::configure [dict merge $reset {{NIO 4 FACES 4 ASPECT 2}}]
puts "REGF $::soc_vehicle::SZ(regf_cell)"
puts "PERIOD [soc_vehicle::track_period H] [soc_vehicle::track_period V]"
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = dict(ln.split(" ", 1) for ln in r.stdout.splitlines() if ln)
    n, bad = out["PLANS"].split()[0], out["PLANS"].split()[2]
    assert int(n) > 100 and bad == "0", out["PLANS"]
    assert int(out["KEEP3"]) > 0
    assert out["KEEP2"].startswith("1 ") and ">= 3" in out["KEEP2"]
    assert out["NIO"].startswith("1 ") and "at most 10 children" in out["NIO"]
    w, h = map(int, out["REGF"].split())
    # FACES 4 sizes regf's every face for 48 bits: _dim(48) at PAD 10
    assert min(w, h) >= 48 * 4 + 10, out["REGF"]
    assert out["PERIOD"] == "306 11808"


def test_fourth_review_round_driver_fixes(tmp_path, monkeypatch):
    """The Claude Code review of #961, the drivers' half.  (a) The local
    driver enumerates at the vehicle's FIXSLACK (0.5) unless told otherwise,
    so plan k is plan k in `soc.tcl -FIX`, and refuses a -slack that
    disagrees with -FIXSLACK.  (b) The search's implementation fingerprint
    does not change when its own output directory appears in the tree.
    (c) The GAP resample moves M by one from ITS value; (d) the heal table
    shows no first check for a run that never reached one; (e) log names
    carry a hash of the whole command."""
    import sys
    lst = lambda *a: subprocess.run(["tclsh", str(_LOCAL), "cluster_cell", "-list", *a,
                                     "-PAD", "10", "-GAP", "4", "-M", "4"],
                                    capture_output=True, encoding="utf-8", cwd=tmp_path,
                                    timeout=300)
    plans = lambda r: [ln for ln in r.stdout.splitlines() if ln.startswith("PLAN ") and "grid" not in ln]
    r0, r5 = lst(), lst("-slack", "0.5")
    assert r0.returncode == 0 and r5.returncode == 0, r0.stderr + r5.stderr
    assert plans(r0) == plans(r5) and len(plans(r0)) > 100
    bad = lst("-slack", "0.15", "-FIXSLACK", "0.5")
    assert bad.returncode != 0 and "disagree" in bad.stderr
    sys.path.insert(0, str(_ROOT / "tools"))
    import soc_plan_search as sps
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "a"], check=True)
    monkeypatch.setattr(sps, "ROOT", repo)
    out = repo / "plan_search"
    (out / "logs").mkdir(parents=True)
    sps.implementation.cache_clear()
    before = sps.implementation(str(out))
    (out / "runs.json").write_text("{}")
    sps.implementation.cache_clear()
    assert sps.implementation(str(out)) == before
    (repo / "b.txt").write_text("y")                   # a real source change still counts
    sps.implementation.cache_clear()
    assert sps.implementation(str(out)) != before
    sps.implementation.cache_clear()
    p = sps.perturbations(sps.knob_list("-PAD 10 -GAP 4 -M 8"))
    assert sps.get_knob(p[0], "PAD") == "11"
    assert (sps.get_knob(p[1], "GAP"), sps.get_knob(p[1], "M")) == ("5", "9")
    p = sps.perturbations(sps.knob_list("-PAD 10 -GAP 4"))
    assert sps.get_knob(p[1], "M") == "17"
    assert sps.first_check(dict(unpl=None, error=True)) == "–"
    assert sps.first_check(dict(unpl=0)) == "clean"
    assert sps.first_check(dict(unpl=3, first_unpl=40, first_ovl=2)) == "40u/2o"
    k = "btcl soc.tcl 8 -PAD {} " + "-X 1 " * 40 + "-FIX 'cluster_cell 12' -noheal"
    assert sps.log_tag(k.format(10)) != sps.log_tag(k.format(11))
