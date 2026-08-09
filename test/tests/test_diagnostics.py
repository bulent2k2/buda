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

"""Message identity and non-overwriting logs (Phase 5).

An id is only worth having if a methodology can rely on it, so what is
pinned here is the contract rather than the wording:

  * ids are unique and their SEVERITY is stable — the text is free to
    improve, which is the whole point of having the id;
  * an identified line is counted at its DECLARED severity, not by which
    words happen to appear in its detail;
  * unidentified output counts exactly as it did before, so every existing
    flow log is unchanged;
  * a re-run no longer destroys the previous flow log.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import buda_diag                                            # noqa: E402
import buda_cli                                             # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


# ── the catalogue is a contract ────────────────────────────────────────────

def test_every_id_is_well_formed_and_unique():
    seen = set()
    for mid, sev, text in buda_diag.catalogue():
        assert re.fullmatch(r"BUDA-\d{4}", mid), mid
        assert sev in ("INFO", "WARNING", "ERROR", "FATAL"), (mid, sev)
        assert text.strip() and text.rstrip().endswith("."), (mid, text)
        assert mid not in seen, f"{mid} defined twice"
        seen.add(mid)


def test_the_catalogue_is_id_ordered():
    """`dump_messages` is what a methodology reads to decide what it may
    waive; a list whose order wanders between runs is a list nobody can
    diff."""
    ids = [m for m, _s, _t in buda_diag.catalogue()]
    assert ids == sorted(ids)


def test_an_unknown_id_is_refused_at_the_call_site():
    """The registry is the definition, not a lookup table an emitter may
    bypass — an id that exists only in one `print` cannot be waived, which
    is the entire failure this module exists to prevent."""
    with pytest.raises(KeyError) as e:
        buda_diag.format("BUDA-9999", "x")
    assert "add it to" in str(e.value)


def test_an_unknown_severity_is_refused():
    with pytest.raises(ValueError):
        buda_diag.format("BUDA-1601", "x", severity="LOUD")


def test_the_line_says_its_own_severity(tmp_path):
    line = buda_diag.format("BUDA-1602", "components: imported 2 of 5")
    assert line.startswith("BUDA-1602: WARNING: ")
    assert buda_diag.severity_of_line(line) == "WARNING"
    assert buda_diag.id_of_line(line) == "BUDA-1602"
    assert buda_diag.severity_of_line("just some prose about an error") is None


def test_a_severity_override_keeps_the_id():
    """`set_unit_check warn` is a DOWNGRADE of a known fault, not a
    different fault.  Keeping the id is what lets a methodology gate on
    BUDA-1901 whether or not this design chose to accept it."""
    line = buda_diag.format("BUDA-1901", "…", severity=buda_diag.WARNING)
    assert buda_diag.id_of_line(line) == "BUDA-1901"
    assert buda_diag.severity_of_line(line) == "WARNING"
    assert buda_diag.severity_of("BUDA-1901") == "FATAL"   # registry default


# ── counting ───────────────────────────────────────────────────────────────

def test_an_identified_line_is_counted_at_its_declared_severity():
    """Both directions of the guess the id replaces.

    A FATAL contains none of the words the prose matcher looks for, so it
    used to count as nothing at all; and a detail that merely mentions the
    word `warning` used to count as one."""
    fatal = buda_diag.format("BUDA-1901", "scale is implausible")
    assert buda_cli._count_diags([fatal]) == (0, 1)

    chatty = buda_diag.format("BUDA-1605", "skipped without warning or error")
    assert buda_cli._count_diags([chatty]) == (0, 0)


def test_unidentified_output_counts_exactly_as_before():
    """The fallback path is the one every existing flow log goes through."""
    lines = ["[Planner] WARNING: band full",
             "  Error: run_nuts required first.",
             "a net called error_flag is fine",
             "[NUTS] 2 segments placed."]
    assert buda_cli._count_diags(lines) == (1, 1)


def test_an_identified_error_can_still_be_the_headline():
    """Converting a call site to an id must not demote its message out of
    the terminal summary — a silent cost that would make every conversion a
    small regression."""
    line = buda_diag.format("BUDA-1601", "no LEF footprint for 3 cells")
    assert buda_cli._is_error_line(line)
    assert buda_cli.BudaSession._extract_headline(
        ["[DEF] components: imported 2 of 2", line]) == line


# ── the ids that are actually emitted ──────────────────────────────────────

def test_dump_messages_prints_the_catalogue(capsys):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command("dump_messages")
    out = capsys.readouterr().out
    for mid, sev, _t in buda_diag.catalogue():
        assert mid in out and sev in out, mid


def test_every_registered_id_is_emitted_somewhere():
    """A catalogue nothing emits is decoration.  Each id must appear in the
    source outside its own definition, or it is a promise with no call
    site."""
    src = ""
    for p in sorted((_ROOT / "src").rglob("*.py")):
        if p.name == "buda_diag.py":
            continue
        src += p.read_text(encoding="utf-8")
    missing = [m for m, _s, _t in buda_diag.catalogue() if m not in src]
    assert not missing, f"registered but never emitted: {missing}"


# ── non-overwriting logs ───────────────────────────────────────────────────

def _run(tmp_path, nbuses=1):
    """One tiny flow.  `nbuses` changes the bundler's count line, which is a
    marker the log actually CONTAINS — silent setup commands (`add_block`,
    `def_layer`) are deliberately kept out of it."""
    body = ["def_layer 4 M4 H TOP 30", "add_block a 0 0 100 100"]
    for i in range(nbuses):
        # Distinct DESTINATIONS: STRICT bundles by endpoint signature, so
        # three buses between the same pair of blocks are one bundle.
        body.append(f"add_block b{i} 800 {600 + 200 * i} 900 {700 + 200 * i}")
        body.append(f"add_bus d{i}[2] a.o b{i}.i")
    body.append("run_bundler STRICT")
    script = tmp_path / "f.buda"
    script.write_text("\n".join(body) + "\n")
    return subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), str(script),
         "--no-viz"],
        capture_output=True, text=True, cwd=tmp_path, timeout=300)


def _log(tmp_path, suffix=""):
    return tmp_path / "log" / ("f_flow.log" + suffix)


def test_a_rerun_keeps_the_previous_flow_log(tmp_path):
    """A re-run used to destroy the log in place, so the evidence for "it
    worked an hour ago" was gone the moment you tried to reproduce it."""
    r1 = _run(tmp_path)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert _log(tmp_path).exists(), sorted((tmp_path / "log").iterdir())
    first = _log(tmp_path).read_text()

    r2 = _run(tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert _log(tmp_path, ".1").read_text() == first


def test_the_canonical_path_is_the_current_run(tmp_path):
    """The CURRENT run keeps the canonical name — every test and doc
    reference points at `log/<cell>_flow.log`, so rotating forward instead
    would have been a rename dressed up as a feature."""
    _run(tmp_path, nbuses=1)
    _run(tmp_path, nbuses=3)
    assert "3 hbundles" in _log(tmp_path).read_text()
    assert "1 hbundles" in _log(tmp_path, ".1").read_text()


def test_only_one_generation_is_kept(tmp_path):
    """Deliberately one, not N: comparing a run against the one before it is
    the case that comes up, and `--log`/`--tag` already archive every run for
    the case that does not."""
    for _ in range(3):
        _run(tmp_path)
    kept = sorted(p.name for p in (tmp_path / "log").glob("f_flow.log*"))
    assert kept == ["f_flow.log", "f_flow.log.1"], kept


def test_a_log_that_cannot_be_rotated_does_not_stop_the_run(tmp_path):
    """Losing the previous log is bad; losing the run is worse."""
    target = tmp_path / "log" / "f_flow.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    (target.parent / "f_flow.log.1").mkdir()   # os.replace onto a dir fails
    buda_cli._rotate_log(str(target))          # must not raise
    assert target.read_text() == "x"
