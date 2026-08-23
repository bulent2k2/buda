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

"""`tools/measure_guard.py` — the guards must fire, and must stay quiet.

Both halves are load-bearing and only one of them is loud.  A guard that never
fires is decoration; a guard that fires on every ordinary incremental build is
worse than none, because people learn to pass the override and the real hazard
goes unguarded.  The `_newest_extension` rule exists precisely because the
first cut got that wrong (see docs/internal/measurement_hazards.md §3).
"""
import io
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "tools"))

import measure_guard as mg  # noqa: E402


# ── build freshness ─────────────────────────────────────────────────────────

def _touch(p, when):
    os.utime(p, (when, when))


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """A miniature repo: src/*.cpp + build/*.so, with controllable mtimes."""
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()
    src = tmp_path / "src" / "engine.cpp"
    src.write_text("// c++\n")
    so = tmp_path / "build" / "buda.cpython-313-darwin.so"
    so.write_bytes(b"\0")
    monkeypatch.setattr(mg, "_ROOT", tmp_path)
    return tmp_path, src, so


def test_fresh_build_is_silent_and_passes(fake_tree):
    _, src, so = fake_tree
    _touch(src, 1000); _touch(so, 2000)
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=True, stream=buf) is True
    assert buf.getvalue() == "", "a fresh build must say nothing at all"


def test_stale_build_refuses(fake_tree):
    _, src, so = fake_tree
    _touch(so, 1000); _touch(src, 2000)
    buf = io.StringIO()
    with pytest.raises(SystemExit) as e:
        mg.check_build_fresh(strict=True, stream=buf)
    assert e.value.code == 2
    out = buf.getvalue()
    assert "STALE BUILD" in out
    assert "bin/bb" in out, "must name the remedy"


def test_stale_build_warns_without_strict(fake_tree):
    _, src, so = fake_tree
    _touch(so, 1000); _touch(src, 2000)
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=False, stream=buf) is False
    assert "STALE BUILD" in buf.getvalue()


def test_a_python_edit_does_not_trip_it(fake_tree):
    """The Python layer is imported from source, so a .py edit is live.

    Tripping on it would fire on nearly every commit in this repo."""
    root, src, so = fake_tree
    _touch(src, 1000); _touch(so, 2000)
    py = root / "src" / "buda_cli.py"
    py.write_text("# python\n")
    _touch(py, 9000)
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=True, stream=buf) is True
    assert buf.getvalue() == ""


def test_judged_by_the_NEWEST_extension_not_the_oldest(fake_tree):
    """THE regression (measurement_hazards.md §3).

    `buda` and `buda_db` are separate targets over different sources, so an
    incremental build relinks only the one that needed it.  Judging by the
    OLDEST reported a stale build straight after a successful `bin/bb`.
    """
    root, src, so = fake_tree
    other = root / "build" / "buda_db.cpython-313-darwin.so"
    other.write_bytes(b"\0")
    _touch(other, 500)      # legitimately not relinked
    _touch(src, 1000)
    _touch(so, 2000)        # the target that DID need rebuilding
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=True, stream=buf) is True, buf.getvalue()
    assert buf.getvalue() == ""


def test_no_build_found_fails_OPEN(fake_tree):
    """It must NOT refuse when it cannot find a build (Codex #829 P1).

    "I found no build" has two causes that look identical from here: there
    really is none (the flows then fail loudly on their own, a better error
    than this one), or the layout is one `_BUILD_DIRS`/`_EXT_SUFFIXES` does not
    know about.  The second is the guard author's bug, and making it fatal
    turns that bug into a CI outage on a platform they cannot run -- which is
    exactly what MSVC (`build/Release/*.pyd`) and Cygwin (`*.dll`) would have
    been.
    """
    root, src, so = fake_tree
    so.unlink()
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=True, stream=buf) is True
    assert "skipping the freshness check" in buf.getvalue()


@pytest.mark.parametrize("layout,name", [
    ("build", "buda.cpython-313-darwin.so"),      # POSIX / Ninja
    ("build/Release", "buda.cp313-win_amd64.pyd"),  # MSVC multi-config
    ("build", "buda.dll"),                          # Cygwin
])
def test_every_supported_build_layout_is_found(fake_tree, layout, name):
    """A layout the probe misses reports "no build" on a machine that has one."""
    root, src, _ = fake_tree
    for p in (root / "build").rglob("*"):
        if p.is_file():
            p.unlink()
    d = root / layout
    d.mkdir(parents=True, exist_ok=True)
    art = d / name
    art.write_bytes(b"\0")
    _touch(src, 1000); _touch(art, 2000)
    buf = io.StringIO()
    assert mg.check_build_fresh(strict=True, stream=buf) is True, buf.getvalue()
    assert buf.getvalue() == "", "a found, fresh build must say nothing"
    # and it is still judged: an older artifact in that layout must refuse
    _touch(art, 500)
    buf2 = io.StringIO()
    with pytest.raises(SystemExit):
        mg.check_build_fresh(strict=True, stream=buf2)
    assert "STALE BUILD" in buf2.getvalue()


# ── identical-run advisory ──────────────────────────────────────────────────

_KEYS = ("overlaps", "unplaced", "viol_bundles", "detailed_wl")


def _rows(**over):
    base = {"flow": "a.buda", "overlaps": 0, "unplaced": 0,
            "viol_bundles": 0, "detailed_wl": 100}
    base.update(over)
    return [base]


def test_identical_sides_are_flagged():
    buf = io.StringIO()
    mg.warn_if_identical(_rows(), _rows(), _KEYS, stream=buf)
    assert "IDENTICAL" in buf.getvalue()
    # it must point at the CAUSE, not just state the fact
    assert "env override" in buf.getvalue()


def test_a_real_difference_stays_quiet():
    buf = io.StringIO()
    mg.warn_if_identical(_rows(), _rows(detailed_wl=99), _KEYS, stream=buf)
    assert buf.getvalue() == ""


def test_one_differing_flow_among_many_stays_quiet():
    """The hazard is EVERY metric identical; a single moved flow is a result."""
    a = _rows() + [{"flow": "b.buda", "overlaps": 0, "unplaced": 0,
                    "viol_bundles": 0, "detailed_wl": 7}]
    b = _rows() + [{"flow": "b.buda", "overlaps": 0, "unplaced": 0,
                    "viol_bundles": 0, "detailed_wl": 6}]
    buf = io.StringIO()
    mg.warn_if_identical(a, b, _KEYS, stream=buf)
    assert buf.getvalue() == ""


def test_no_overlap_between_runs_stays_quiet():
    buf = io.StringIO()
    mg.warn_if_identical(_rows(), [{"flow": "z.buda"}], _KEYS, stream=buf)
    assert buf.getvalue() == ""


# ── drift advisory ─────────────────────────────────────────────────────────

def test_drift_is_quiet_at_the_same_commit(monkeypatch):
    monkeypatch.setattr(mg, "git_out",
                        lambda *a: "abc1234" if a[0] == "rev-parse" else "0")
    buf = io.StringIO()
    mg.describe_drift("abc1234", stream=buf)
    assert buf.getvalue() == ""


def test_drift_names_the_span(monkeypatch):
    def fake(*a):
        return "deadbee" if a[0] == "rev-parse" else "8"
    monkeypatch.setattr(mg, "git_out", fake)
    buf = io.StringIO()
    mg.describe_drift("abc1234", stream=buf)
    out = buf.getvalue()
    assert "abc1234" in out and "deadbee" in out and "8 commit(s)" in out


def test_drift_is_quiet_without_a_stamp():
    buf = io.StringIO()
    mg.describe_drift("", stream=buf)
    assert buf.getvalue() == ""
