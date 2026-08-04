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
        lid, origin, rest = int(tok[1]), F(tok[2]), tok[3:]
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
# the defect the mirror file fixes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lid", [2, 4, 8, 10])
def test_plain_h_patterns_have_no_reflection_axis(lid):
    """chip_tracks' rails carry a wider gap after than before, so the unit is
    not a palindrome and NO axis exists -- at any origin.  This is why the
    mirrored stack reports MISALIGNED on the plain technology."""
    _, slots = read_patterns(PLAIN)[lid]
    rails = [(i, s) for i, (t, _, s) in enumerate(slots) if t in ("POWER", "GROUND")]
    for i, after in rails:
        assert slots[i - 1][2] != after, "expected an asymmetric gap around the rail"
    assert axes(slots)[1] == [], f"M{lid} unexpectedly has an axis"


def test_plain_m6_is_the_symmetric_exception():
    """M6 already has equal gaps, and is the one plain H layer with an axis --
    the control that says the axis really does follow from the gap symmetry."""
    _, slots = read_patterns(PLAIN)[6]
    assert axes(slots)[1] != []


# --------------------------------------------------------------------------- #
# what the mirror technology guarantees
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lid", [2, 4, 6, 8, 10])
def test_mirror_h_patterns_are_palindromes(lid):
    _, slots = read_patterns(MIRROR)[lid]
    for i, (t, _, after) in enumerate(slots):
        if t in ("POWER", "GROUND"):
            assert slots[i - 1][2] == after, f"M{lid} rail not centred in its gap"
    assert axes(slots)[1], f"M{lid} has no reflection axis"


@pytest.mark.parametrize("lid", [2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
def test_mirror_preserves_period_and_density(lid):
    """Only the whitespace moves: same period (so the 504 grid still holds) and
    the same signal density (so `def_layer` overheads stay correct)."""
    _, a = read_patterns(PLAIN)[lid]
    _, b = read_patterns(MIRROR)[lid]
    assert centres(a)[1] == centres(b)[1]
    assert signal_density(a) == signal_density(b)


@pytest.mark.parametrize("lid", [3, 5, 7, 9, 11])
def test_v_layers_are_untouched(lid):
    """A y-flip maps y -> 2d-y and leaves x alone, so vertical tracks play no
    part -- they must not drift."""
    assert read_patterns(PLAIN)[lid] == read_patterns(MIRROR)[lid]


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
