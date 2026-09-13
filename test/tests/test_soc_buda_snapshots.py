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

"""`flow/soc_small.buda` and `flow/soc_mid.buda` -- the SoC vehicle recorded
at two fixed sizes, for anyone who wants the design without a `tclsh`.

Both are GENERATED (`tools/tcl2buda.py` from `flow/tcl/soc.tcl`), which is
the hazard these tests exist for: a checked-in recording drifts the moment
its generator changes, and this repository has already paid for that shape
once -- a worked example that decayed from clean to FAILING, where the tell
is worse than a stale claim because *a stale example runs*.

So the small one is regenerated and compared command-for-command, and it is
RUN, because the numbers its header quotes are a measurement and a recorded
measurement nothing re-runs decays into a claim.

The mid one is not re-run here (~25 s), and what is pinned instead is the
one thing the two headers assert about each other: the small size is dirty
at its first detailed audit and therefore RECORDS the healer round, the mid
size is clean and records no healer at all.  That claim was wrong in the
first draft of both headers -- they shared one sentence saying the healers
run -- so it is the claim most worth a guard.
"""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SMALL = _ROOT / "flow/soc_small.buda"
_MID = _ROOT / "flow/soc_mid.buda"

_HEALERS = ("negotiate_congestion", "ripup_reroute", "refine_selection")

pytestmark = pytest.mark.mid


def _commands(text):
    """The script itself: the recording minus every comment and blank line."""
    return [ln for ln in (l.strip() for l in text.splitlines())
            if ln and not ln.startswith("#")]


def _regenerate(tmp_path, nq):
    out = tmp_path / ("soc_%d.buda" % nq)
    subprocess.run(
        ["python3", str(_ROOT / "tools/tcl2buda.py"),
         str(_ROOT / "flow/tcl/soc.tcl"), str(nq), "-o", str(out)],
        check=True, capture_output=True, timeout=900, cwd=str(_ROOT))
    return out.read_text()


@pytest.mark.parametrize("path, nq", [(_SMALL, 8), (_MID, 32)])
def test_each_header_names_the_command_that_regenerates_it(path, nq):
    """The recipe in the header is the one that produced the file."""
    head = path.read_text()
    assert ("tools/tcl2buda.py flow/tcl/soc.tcl %d -o flow/%s"
            % (nq, path.name)) in head
    # It must say it is generated, or somebody will hand-edit it.
    assert "GENERATED" in head and "Do not hand-edit" in head


def test_the_small_snapshot_is_what_the_recorder_writes(tmp_path):
    """The checked-in script is the recording of `soc.tcl 8`, command for
    command.  Comments are excluded: the header is ours, not the recorder's."""
    fresh = _regenerate(tmp_path, 8)
    assert _commands(fresh) == _commands(_SMALL.read_text())


def test_the_small_snapshot_still_routes_clean(tmp_path):
    """It RUNS, and ends where its header says it does."""
    from wrapper_select import wrapper_command
    buda = wrapper_command(_ROOT, "buda")
    r = subprocess.run([*buda, "--no-viz", str(_SMALL)],
                       capture_output=True, text=True, timeout=900,
                       cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    out = r.stdout
    # The header quotes this shape; a silent change to soc.tcl would move it.
    assert "323 bundles" in out, out[-3000:]
    # The LAST detailed audit is the endpoint, and it is clean.  Only lines
    # carrying a VERDICT count: the CLI prints the command list twice, once
    # summarized and once as a timing table, and the table has no verdict on
    # it -- reading that as "no Success here" is a fault of the reader.
    dnuts = [ln for ln in out.splitlines()
             if "check_design dnuts" in ln
             and ("Success" in ln or "violation(s)" in ln)]
    assert dnuts, out[-3000:]
    assert "Success: no violations found." in dnuts[-1], dnuts
    # ... and it got there by healing, which is the header's other claim.
    assert "violation(s)" in dnuts[0], dnuts


def test_only_the_small_snapshot_records_the_healers():
    """Each header's claim about the other, pinned.

    A recording holds only the commands that RAN, so this is a property of
    the two designs and not of the writer: NQ=8 is dirty at its first
    `check_design dnuts` and `heal_if_dirty` fires, NQ=32 is clean and it
    does not.  Both headers say so; if soc.tcl's healing changes, one of
    them becomes a lie and this fails.
    """
    small = _commands(_SMALL.read_text())
    mid = _commands(_MID.read_text())

    def healers(cmds):
        return [c for c in cmds if c.split()[0] in _HEALERS]

    assert healers(small), "soc_small.buda claims to exercise the healers"
    assert not healers(mid), "soc_mid.buda claims to record no healer: %r" % (
        healers(mid),)

    # ... and both really are the hier flow, which is the point of the vehicle.
    for cmds in (small, mid):
        assert any(c.startswith("run_planner hier") for c in cmds)
        assert any(c.startswith("run_hier_bundler") for c in cmds)
        assert any(c.startswith("run_detailed_nuts") for c in cmds)


def test_the_mid_snapshot_is_the_bigger_design():
    """The pair is a small/mid pair, not two copies of one size."""
    small = _commands(_SMALL.read_text())
    mid = _commands(_MID.read_text())
    assert len(mid) > 2 * len(small), (len(small), len(mid))
