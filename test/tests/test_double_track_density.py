"""tools/double_track_density.py — the corpus track-density transform.

Guards the three invariants the transform claims by construction (period, signal
metal, binary-exactness), the symlink hazard that bit once in practice, and the
`--audit` contract.
"""
import subprocess
import sys
from fractions import Fraction as F
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "double_track_density.py"
sys.path.insert(0, str(_ROOT / "tools"))

import double_track_density as dtd  # noqa: E402

# Every shared fixture the corpus routes on, plus the doubled twin.
FIXTURES = sorted(
    set((_ROOT / "flow" / "tracks").glob("*.buda"))
    | {_ROOT / "flow" / "rnr" / "mix_tracks.buda",
       _ROOT / "flow" / "rnr" / "mix_tracks_2x.buda",
       _ROOT / "demo" / "tracks_ariane136.buda"}
)


def _patterns(path):
    out = []
    for line in path.read_text().splitlines():
        m = dtd.PAT.match(line)
        if m and m.group(4).strip():
            out.append((m.group(2), m.group(3), dtd.parse_slots(m.group(4))))
    return out


# --------------------------------------------------------------------------- #
# the invariants the transform claims BY CONSTRUCTION
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_doubling_preserves_period_metal_and_exactness(path):
    for lid, _origin, slots in _patterns(path):
        new = dtd.double(slots)
        assert dtd.period(new) == dtd.period(slots), f"{path.name} L{lid}: period"
        assert dtd.signal_metal(new) == dtd.signal_metal(slots), \
            f"{path.name} L{lid}: signal metal"
        assert dtd.n_signals(new) == 2 * dtd.n_signals(slots), \
            f"{path.name} L{lid}: signal count"
        for _, w, s in new:
            assert dtd.exact(w) and dtd.exact(s), f"{path.name} L{lid}: {w}/{s}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_checked_in_value_is_binary_exact(path):
    """The invariant whose violation broke template alignment on the chip stack:
    `tracks_in_range` accumulates `pos += width + space_after`, so a value that
    is not a binary fraction makes an on-the-edge track's membership depend on
    rounding."""
    for lid, origin, slots in _patterns(path):
        assert dtd.exact(F(origin)), f"{path.name} L{lid}: origin {origin}"
        for t, w, s in slots:
            assert dtd.exact(w) and dtd.exact(s), f"{path.name} L{lid}: {t} {w}/{s}"


def test_rails_are_untouched():
    """Each fixture keeps its own structure — including unequal rail widths
    (tracks.buda L7 is POWER 6 / GROUND 2) and its rails' space_after."""
    for lid, _o, slots in _patterns(_ROOT / "flow" / "tracks" / "tracks.buda"):
        rails_before = [(t, w, s) for t, w, s in slots if t.upper() in dtd.RAILS]
        rails_after = [(t, w, s) for t, w, s in dtd.double(slots)
                       if t.upper() in dtd.RAILS]
        assert rails_before == rails_after, f"L{lid}: rails moved"


def test_the_2x_twin_is_the_doubling_of_its_original():
    """flow/rnr/mix_tracks_2x.buda must be exactly what the tool produces from
    mix_tracks.buda — same periods, same metal, twice the signals.  If someone
    edits one and not the other the pair stops being a controlled comparison."""
    orig = {lid: slots for lid, _o, slots in _patterns(_ROOT / "flow/rnr/mix_tracks.buda")}
    twin = {lid: slots for lid, _o, slots in _patterns(_ROOT / "flow/rnr/mix_tracks_2x.buda")}
    assert orig.keys() == twin.keys(), "layer sets diverged"
    for lid in orig:
        assert twin[lid] == dtd.double(orig[lid]), f"L{lid}: twin is not the doubling"
        assert dtd.period(twin[lid]) == dtd.period(orig[lid]), f"L{lid}: period"
        assert dtd.signal_metal(twin[lid]) == dtd.signal_metal(orig[lid]), f"L{lid}: metal"


def test_the_2x_flow_clones_differ_only_in_the_tracks_line():
    """Each `*_2x.buda` clone is its original with one line changed.  Anything
    else drifting makes the pair measure two things at once."""
    for name in ("mix2_fast_bottomup_caps", "mix2_fast_on_aligned_sql"):
        orig = (_ROOT / f"flow/rnr/{name}.buda").read_text().splitlines()
        twin = (_ROOT / f"flow/rnr/{name}_2x.buda").read_text().splitlines()
        strip = lambda ls: [l for l in ls if not l.startswith("#")]  # noqa: E731
        o, w = strip(orig), strip(twin)
        assert len(o) == len(w), f"{name}: clone has a different command count"
        diffs = [(a, b) for a, b in zip(o, w) if a != b]
        assert diffs == [("source mix_tracks.buda", "source mix_tracks_2x.buda")], \
            f"{name}: unexpected divergence {diffs}"


def test_the_originals_are_not_doubled():
    """The whole point of the clone scheme: the ORIGINAL fixtures keep the
    historical density, because their congestion is what the healer and
    doomed-seat vehicles exist to exercise."""
    for lid, _o, slots in _patterns(_ROOT / "flow/rnr/mix_tracks.buda"):
        assert dtd.n_signals(slots) == 8, f"mix_tracks L{lid} was doubled in place"


def test_the_irregular_fixture_survives():
    """tracks.buda L7 is the awkward one: unequal rails and 3 signals per group.
    A transform that assumed the regular 4-per-group shape would mangle it."""
    l7 = [s for lid, _o, s in _patterns(_ROOT / "flow" / "tracks" / "tracks.buda")
          if lid == "7"]
    assert l7, "tracks.buda L7 vanished"
    slots = l7[0]
    widths = {t: w for t, w, _ in slots if t.upper() in dtd.RAILS}
    assert widths.get("POWER") != widths.get("GROUND"), "L7 rails no longer unequal"
    assert dtd.period(dtd.double(slots)) == dtd.period(slots)


# --------------------------------------------------------------------------- #
# the symlink hazard — this bit once, for real
# --------------------------------------------------------------------------- #
def test_symlinked_fixture_is_not_doubled_twice(tmp_path):
    """flow/big_data_test/big2/tracks4top.buda is a SYMLINK to
    flow/tracks/tracks4top.buda.  The transform is not idempotent, so listing
    both paths doubled the target twice — silently, since each pass is
    individually valid."""
    real = tmp_path / "t.buda"
    real.write_text("def_track_pattern 2 0  POWER 2 1  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5\n")
    link = tmp_path / "link.buda"
    link.symlink_to(real)
    before = dtd.n_signals(_patterns(real)[0][2])
    r = subprocess.run([sys.executable, str(TOOL), str(real), str(link)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "skipped: same file" in r.stdout
    assert dtd.n_signals(_patterns(real)[0][2]) == 2 * before


# --------------------------------------------------------------------------- #
# --audit
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_audit_passes_on_every_checked_in_fixture(path):
    r = subprocess.run([sys.executable, str(TOOL), "--audit", str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_audit_is_not_a_doubled_check(tmp_path):
    """`--audit` must pass on BOTH densities.  The predecessor `--check` claimed
    to verify "already doubled" and, because the transform is non-idempotent,
    returned 1 for every non-empty fixture — including ones it had just
    transformed.  "Has this been doubled?" is not a property of a file: 16
    signals per period could be natively 16 or a doubled 8."""
    for name, body in (
        ("sparse.buda", "def_track_pattern 2 0  POWER 2 1  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5\n"),
        ("dense.buda", "def_track_pattern 2 0  POWER 2 1  SIGNAL 0.5 0.25  SIGNAL 0.5 0.25  "
                       "GROUND 2 1  SIGNAL 0.5 0.25  SIGNAL 0.5 0.25\n"),
    ):
        p = tmp_path / name
        p.write_text(body)
        r = subprocess.run([sys.executable, str(TOOL), "--audit", str(p)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stdout}"


def test_audit_catches_a_non_binary_exact_value(tmp_path):
    """The 8/10 scaling that broke the chip stack: 0.8 / 0.4 are not binary
    fractions.  This is what --audit exists to refuse."""
    p = tmp_path / "bad.buda"
    p.write_text("def_track_pattern 2 0  POWER 2 1  SIGNAL 0.8 0.4  GROUND 2 1  SIGNAL 0.8 0.4\n")
    r = subprocess.run([sys.executable, str(TOOL), "--audit", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "NOT BINARY-EXACT" in r.stdout


def test_audit_catches_a_malformed_slot_list(tmp_path):
    p = tmp_path / "bad.buda"
    p.write_text("def_track_pattern 2 0  POWER 2 1  SIGNAL 1\n")
    r = subprocess.run([sys.executable, str(TOOL), "--audit", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "MALFORMED" in r.stdout


def test_dry_run_does_not_write(tmp_path):
    p = tmp_path / "t.buda"
    body = "def_track_pattern 2 0  POWER 2 1  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5\n"
    p.write_text(body)
    subprocess.run([sys.executable, str(TOOL), "--dry-run", str(p)],
                   capture_output=True, text=True, check=True)
    assert p.read_text() == body
