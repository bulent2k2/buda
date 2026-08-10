"""flow/chip/chip_tracks_mirror.buda — the mirror-symmetric H technology.

A flipped instance's routing is its reference's reflected about the instance's
own centreline, so solve-once-copy across a mirror needs every H pattern to be
invariant under that reflection.  These tests lock the three things that makes
true: the units are palindromes, the origins put an axis on the flipped
instances' centrelines, and nothing else about the technology moved.
"""
from fractions import Fraction as F
from pathlib import Path

import pytest

import buda

from slot_groups import expand_slot_groups

CHIP = Path(__file__).resolve().parents[2] / "flow" / "chip"
PLAIN = CHIP / "chip_tracks.buda"
MIRROR = CHIP / "chip_tracks_mirror.buda"

H_LAYERS = (2, 4, 6, 8)          # what a cell may use under reserve_top_layers 2

# chip_stack's three flipped instances (centre = y1 + h/2); see chip_stack.bdb.sql.
FLIPPED_CENTRES = (F(10710), F(8694), F(12222))


# --------------------------------------------------------------------------- #
# parsing + pattern geometry
# --------------------------------------------------------------------------- #
def read_patterns(path):
    """{layer_id: (origin, [(type, width, space_after), ...])} from a .buda file."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line.startswith("def_track_pattern"):
            continue
        tok = line.split()
        # Expand `( <slots> )x<N>` with the CLI's own parser: these fixtures are
        # written in the compact form, and a private triple-splitter here would
        # read `0.25)x7` as a width.
        lid, origin = int(tok[1]), F(tok[2])
        rest = expand_slot_groups("def_track_pattern", tok[3:])
        slots = [(rest[i], F(rest[i + 1]), F(rest[i + 2]))
                 for i in range(0, len(rest), 3)]
        out[lid] = (origin, slots)
    return out


def centres(slots):
    """[(centre, type, width)] within one unit, and the unit pitch."""
    pos, out = F(0), []
    for t, w, s in slots:
        out.append((pos + w / 2, t, w))
        pos += w + s
    return out, pos


def axes(slots):
    """Every reflection axis in [0, period/2) of the infinite periodic pattern."""
    tr, p = centres(slots)
    key = {(c % p, t, w) for c, t, w in tr}
    c0, t0, w0 = tr[0]
    cand = sorted({((c0 + c) / 2) % (p / 2)
                   for c, t, w in tr if (t, w) == (t0, w0)})
    return p, [a for a in cand
               if all(((2 * a - c) % p, t, w) in key for c, t, w in tr)]


def signal_density(slots):
    tr, p = centres(slots)
    return sum(w for _, t, w in tr if t == "SIGNAL") / p


# --------------------------------------------------------------------------- #
# the stack's shape: 16 signals per period, periods held
# --------------------------------------------------------------------------- #
# The periods the 504 = LCM(18,18,24,56) placement grid is derived from.  A
# change here invalidates every checked-in chip floorplan, so it is pinned.
PERIODS = {2: 18, 3: 18, 4: 18, 5: 18, 6: 24, 7: 39, 8: 56, 9: 56, 10: 74, 11: 74}


@pytest.mark.parametrize("path", [PLAIN, MIRROR])
@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_period_is_pinned(path, lid):
    _, slots = read_patterns(path)[lid]
    assert centres(slots)[1] == PERIODS[lid]


# The historical technology: 8 signals per period, these widths/spacings.  The
# current stack is exactly 2x it, which is what keeps the metal and the
# arithmetic both exact -- see test_doubling_holds_the_metal_exactly.
HISTORIC = {2: (F(1), F(1, 2)), 3: (F(1), F(1, 2)), 4: (F(1), F(1, 2)),
            5: (F(1), F(1, 2)), 6: (F(1), F(1)), 7: (F(2), F(1)),
            8: (F(3), F(3, 2)), 9: (F(3), F(3, 2)),
            10: (F(4), F(2)), 11: (F(4), F(2))}
SIGNALS_PER_PERIOD = 16


@pytest.mark.parametrize("path", [PLAIN, MIRROR])
@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_sixteen_signals_per_period(path, lid):
    """Double the historical eight."""
    tr, _ = centres(read_patterns(path)[lid][1])
    assert sum(1 for _, t, _ in tr if t == "SIGNAL") == SIGNALS_PER_PERIOD


@pytest.mark.parametrize("path", [PLAIN, MIRROR])
@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_doubling_holds_the_metal_exactly(path, lid):
    """16 = 2x8 means every signal width and spacing simply HALVES, so the
    signal metal per period is IDENTICAL to the historical technology and the
    def_layer overheads do not move at all.  This is the property that made 16
    the right jump: at 10 tracks, holding the metal needed a x8/10 scaling whose
    values are not binary fractions, and the two goals could not both be had."""
    ow, os_ = HISTORIC[lid]
    _, slots = read_patterns(path)[lid]
    sig = [(w, s) for t, w, s in slots if t == "SIGNAL"]
    assert {w for w, _ in sig} == {ow / 2}
    # spacings are s except the one before each rail, which is the gap g
    assert os_ / 2 in {s for _, s in sig}
    metal = sum(w for w, _ in sig)
    assert metal == 8 * ow, "signal metal per period must match the historical 8-track"


@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_overheads_are_the_historical_numbers(lid):
    """def_layer's declared overhead must equal 1 - density, and because the
    metal is unchanged that is the historical figure."""
    _, slots = read_patterns(PLAIN)[lid]
    ow, _ = HISTORIC[lid]
    assert signal_density(slots) == F(8 * ow, PERIODS[lid])


def test_grid_is_the_lcm_of_the_layers_cells_may_use():
    """504 must stay the LCM over the H layers a cell may use under
    `reserve_top_layers 2` -- the property the stacked floorplans rely on."""
    from math import lcm
    per = [PERIODS[lid] for lid in H_LAYERS]
    assert lcm(*per) == 504
    assert all(504 % p == 0 for p in per)


# --------------------------------------------------------------------------- #
# binary-exactness: a hard requirement, not cosmetics
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [PLAIN, MIRROR])
@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_every_value_is_binary_exact(path, lid):
    """`tracks_in_range` walks a period at a time with `pos += width +
    space_after`, so hundreds of periods of accumulated rounding decide whether
    a track sitting exactly ON a window edge falls inside it.  With values that
    are not binary fractions the edge track drops out and template alignment
    breaks (measured: "397 vs 396", both cells MISALIGNED, ZERO bits copied).

    Every width, spacing and origin must therefore be exactly representable in
    binary.  This is the guard that keeps the arithmetically-obvious-but-wrong
    choice (at ten tracks, scaling by 8/10 to hold the signal metal
    exactly: 0.8 / 0.4 / 0.7) from coming back."""
    origin, slots = read_patterns(path)[lid]
    vals = [origin] + [v for _, w, s in slots for v in (w, s)]
    for v in vals:
        assert (v * 65536).denominator == 1, f"M{lid}: {v} is not binary-exact"


def test_the_rejected_ten_track_scaling_would_not_be_binary_exact():
    """Why 16 and not 10.  At ten tracks, holding the signal metal exactly needs
    w = 0.8*w_old, and 4/5 is never a binary fraction -- so exact metal and
    exact arithmetic were mutually exclusive and the metal had to give.
    Doubling has no such tension: w/2 of a binary-exact w is binary-exact, so 16
    tracks keeps BOTH."""
    assert (F(4, 5) * 65536).denominator != 1                 # the 10-track scale
    assert all((F(1, 2) * w * 65536).denominator == 1         # the 16-track scale
               for w, _ in HISTORIC.values())


# --------------------------------------------------------------------------- #
# what the mirror technology guarantees
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [PLAIN, MIRROR])
@pytest.mark.parametrize("lid", sorted(PERIODS))
def test_every_unit_is_a_palindrome(path, lid):
    """Each rail centred in its own whitespace, on BOTH files -- the shared
    stack is symmetric now, so the mirror file inherits it and changes no
    slot."""
    _, slots = read_patterns(path)[lid]
    for i, (t, _, after) in enumerate(slots):
        if t in ("POWER", "GROUND"):
            assert slots[i - 1][2] == after, f"M{lid} rail not centred in its gap"
    assert axes(slots)[1], f"M{lid} has no reflection axis"


def test_the_two_files_differ_only_in_three_h_origins():
    """The mirror file is the shared stack plus a placement-specific origin
    choice.  If a slot ever diverges, one of them has drifted."""
    a, b = read_patterns(PLAIN), read_patterns(MIRROR)
    assert a.keys() == b.keys()
    for lid in a:
        assert a[lid][1] == b[lid][1], f"M{lid} slots differ between the files"
    moved = {lid for lid in a if a[lid][0] != b[lid][0]}
    assert moved == {4, 6, 8}
    assert {lid: b[lid][0] for lid in moved} == {4: F(-199), 6: F("-403.5"), 8: F(-802)}


@pytest.mark.parametrize("lid", [3, 5, 7, 9, 11])
def test_v_origins_are_untouched(lid):
    """A y-flip maps y -> 2d-y and leaves x alone, so vertical tracks play no
    part -- their origins must not drift between the files."""
    assert read_patterns(PLAIN)[lid][0] == read_patterns(MIRROR)[lid][0]


@pytest.mark.parametrize("lid", H_LAYERS)
def test_axis_lands_on_every_flipped_centreline(lid):
    """Symmetry creates the axes; the origin decides where they land.  All
    three flipped instances must sit on one, for each layer a cell may use."""
    origin, slots = read_patterns(MIRROR)[lid]
    p, ax = axes(slots)
    half = p / 2
    for d in FLIPPED_CENTRES:
        assert (d - origin) % half in ax, \
            f"M{lid}: flipped centre {d} is off axis (need {ax} mod {half})"


def test_m10_cannot_host_a_mirrored_template():
    """504 % (74/2) != 0, so the three flipped centres fall on three different
    residues and no single origin serves them.  That is exactly why
    `reserve_top_layers 2` caps cells below M10 -- the grid is the LCM over the
    layers the CELLS use.  Locked so a future stack change cannot quietly rely
    on M10 for cell-local routing."""
    origin, slots = read_patterns(MIRROR)[10]
    p, ax = axes(slots)
    half = p / 2
    assert 504 % half != 0
    assert len({(d - origin) % half for d in FLIPPED_CENTRES}) > 1


# --------------------------------------------------------------------------- #
# the consequence, through the real grid
# --------------------------------------------------------------------------- #
# (name, cell, y1, height) for chip_stack; instances at or above the die
# centreline 6930 are the flipped ones.
INSTANCES = [("i_big2_0", "big2", 0.0, 6300.0), ("i_big2_1", "big2", 7560.0, 6300.0),
             ("i_mix2_0", "mix2", 458.0, 2360.0), ("i_mix2_1", "mix2", 3986.0, 2360.0),
             ("i_mix2_2", "mix2", 7514.0, 2360.0), ("i_mix2_3", "mix2", 11042.0, 2360.0)]
YC = 6930.0


def _stack(patterns):
    st = buda.RoutingGridStack()
    for lid, (origin, slots) in patterns.items():
        st.define_layer(lid, buda.TrackPattern(
            float(origin),
            [buda.TrackSlot(t, "", float(w), float(s)) for t, w, s in slots]), True)
    return st


def _local_pools(st, y1, h, flipped):
    """Signal tracks in the instance window, expressed in the instance's frame."""
    out = {}
    for lid in H_LAYERS:
        ts = [c for c, _ in st.get_layer_grid(lid).signal_tracks_in(0.0, y1, y1 + h)]
        out[lid] = sorted((y1 + h - t) if flipped else (t - y1) for t in ts)
    return out


@pytest.mark.parametrize("path,expect_uniform", [(MIRROR, True), (PLAIN, False)])
def test_instances_share_their_cell_pool(path, expect_uniform):
    """The property check_template_tracks reports: on the mirror technology all
    six instances see identical tracks in their own frame; on the plain one the
    flipped ones do not."""
    pats = {lid: read_patterns(path)[lid] for lid in H_LAYERS}
    st = _stack(pats)
    uniform = True
    for cell in ("big2", "mix2"):
        ref = None
        for name, c, y1, h in INSTANCES:
            if c != cell:
                continue
            loc = _local_pools(st, y1, h, y1 >= YC)
            if ref is None:
                ref = loc
            elif any(ref[lid] != loc[lid] for lid in H_LAYERS):
                uniform = False
    assert uniform is expect_uniform


def test_mismatch_is_phase_not_supply():
    """On the plain technology the flipped pools are the same SIZE and still
    wrong -- the defect is phase, which is why a track-count comparison alone
    would have called it aligned."""
    pats = {lid: read_patterns(PLAIN)[lid] for lid in H_LAYERS}
    st = _stack(pats)
    ref = _local_pools(st, 458.0, 2360.0, False)          # i_mix2_0
    flip = _local_pools(st, 7514.0, 2360.0, True)         # i_mix2_2, flipped
    assert any(ref[lid] != flip[lid] for lid in H_LAYERS)
    assert all(len(ref[lid]) == len(flip[lid]) for lid in H_LAYERS)
