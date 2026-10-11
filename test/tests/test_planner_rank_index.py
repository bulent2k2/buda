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

"""Two planner shortcuts (#972 levers E and C) are decision-identical to
the full computations they replace.

E — the rip-up victim ranking reads its CANDIDATE victims off
``band_index_`` (the inverse of ``charge_log_``: which committed bundles
hold a charge on each contended band) instead of scoring every committed
plan; the VALUE each candidate is sorted by is still ``plan_band_overlap``.
Under the geometric spread modes a plan's bands at commit time are the
bands a re-derivation sees at rank time, so the candidate set is a superset
of the non-zero overlaps and the sorted (overlap, idx) list is the same.

C — the refine pass's ``keep`` probe (the best plan pinned to the OLD
candidate) is read off the unrestricted ``np`` sweep, which scores that
candidate from the same ripped-up state with the same arithmetic.

Both keep a study control: ``BUDA_RANK_SCAN=1`` restores the full scan and
``BUDA_REFINE_TWO_CALLS=1`` the second ``plan_bundle`` call.  They are read
per call, so flipping the environment between two in-process runs of the
same design exercises both paths for real.  The vehicle is the recorded SoC
(``flow/soc_small.buda``) up to its hier planner — three rip-ups and a
refine pass that moves bundles, both asserted so the comparison cannot be
vacuous.
"""
import contextlib
import io
import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_REPO = Path(__file__).parents[2]
_FLOW = _REPO / "flow" / "soc_small.buda"
_CONTROLS = ("BUDA_RANK_SCAN", "BUDA_REFINE_TWO_CALLS")


def _planner_prefix():
    """The recorded SoC's commands through its first `run_planner`."""
    out = []
    for raw in _FLOW.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(line)
        if line.startswith("run_planner"):
            return out
    raise AssertionError("soc_small.buda has no run_planner line")


def _plan(full_scan):
    prev = {k: os.environ.get(k) for k in _CONTROLS}
    for k in _CONTROLS:
        if full_scan:
            os.environ[k] = "1"
        else:
            os.environ.pop(k, None)
    try:
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in _planner_prefix():
                s.do_command(c)
        plans = [(w.input.original_bundle.id,
                  w.plan.selected_topology_index,
                  list(w.plan.seg_layers),
                  list(w.plan.seg_perp)) for w in s.bundles]
        text = buf.getvalue()
        planner_lines = [l for l in text.splitlines() if l.startswith("[Planner]")]
        return plans, planner_lines, text
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_rank_index_and_merged_probe_are_decision_identical():
    plans_fast, lines_fast, text_fast = _plan(full_scan=False)
    plans_full, lines_full, text_full = _plan(full_scan=True)
    # Non-vacuous: the vehicle reaches the rip-up ladder and the refine pass
    # changes bundles, so both shortcuts ran where they could have diverged.
    assert text_full.count("[Planner] Rip-up:") >= 1
    m = re.search(r"Refine pass 1: (\d+) of \d+ bundle", text_full)
    assert m and int(m.group(1)) >= 1, "refine pass moved nothing"
    assert plans_fast == plans_full
    assert lines_fast == lines_full
