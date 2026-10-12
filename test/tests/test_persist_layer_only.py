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

"""The layer-only re-persist leaves the BDB identical to the full rewrite.

After `run_nuts` the dead-span escalation moves LOW segments to TOP layers
and the session re-persists the planner output selectively: every expanded
instance whose fingerprint changed is dropped and rewritten whole, when
all that moved is `topology_segment.assigned_layer` on its selected
candidate's rows.  The layer-only path (#972) updates those rows in place
and drops the same routing rows the rewrite drops.  Its contract is that
the TABLES end identical, row for row, to the full rewrite — pinned here
on the recorded SoC (`flow/soc_small.buda`, whose escalation moves 18
segments) by running the flow through `run_nuts` both ways, dumping each
BDB as text and comparing every table's rows as a sorted multiset (rowids
are not content: an in-place UPDATE keeps them, a delete+insert does not).
`BUDA_PERSIST_FULL=1` is the control that keeps the rewrite; the path must
also be shown to have RUN in the default arm.
"""
import collections
import contextlib
import io
import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
import buda_cli  # noqa: E402
import bdb_serialize  # noqa: E402

_REPO = Path(__file__).parents[2]
_FLOW = _REPO / "flow" / "soc_small.buda"
_INSERT = re.compile(r'^INSERT INTO "([^"]+)" VALUES\((.*)\);$')


def _commands_through_run_nuts(bdb_path):
    out = []
    for raw in _FLOW.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("open_bdb"):
            line = f"open_bdb {bdb_path}"
        out.append(line)
        if line.startswith("run_nuts"):
            return out
    raise AssertionError("soc_small.buda has no run_nuts line")


def _rows_by_table(sql_path):
    rows = collections.defaultdict(list)
    for line in Path(sql_path).read_text(encoding="utf-8").splitlines():
        m = _INSERT.match(line)
        if m:
            rows[m.group(1)].append(m.group(2))
    return {t: sorted(v) for t, v in rows.items()}


def _run(tmp_path, tag, full):
    prev = os.environ.get("BUDA_PERSIST_FULL")
    if full:
        os.environ["BUDA_PERSIST_FULL"] = "1"
    else:
        os.environ.pop("BUDA_PERSIST_FULL", None)
    try:
        bdb = tmp_path / f"{tag}.bdb"
        sql = tmp_path / f"{tag}.bdb.sql"
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in _commands_through_run_nuts(bdb):
                s.do_command(c)
            s.do_command(f"save_bdb {sql}")
        text = buf.getvalue()
        m = re.search(r"dead-span escalation: moved (\d+) dead LOW", text)
        moved = int(m.group(1)) if m else 0
        return (_rows_by_table(sql), moved,
                getattr(s, "_persist_layer_only_n", 0))
    finally:
        if prev is None:
            os.environ.pop("BUDA_PERSIST_FULL", None)
        else:
            os.environ["BUDA_PERSIST_FULL"] = prev


@pytest.mark.mid
def test_layer_only_repersist_matches_the_full_rewrite(tmp_path):
    rows_full, moved_full, n_full = _run(tmp_path, "full", full=True)
    rows_fast, moved_fast, n_fast = _run(tmp_path, "fast", full=False)
    # Non-vacuous: the escalation moved segments, so expanded instances
    # changed in their layers alone, and the layer-only path served them.
    assert moved_full >= 1 and moved_full == moved_fast
    assert n_full == 0
    assert n_fast >= 1, "layer-only re-persist never engaged"
    assert set(rows_full) == set(rows_fast)
    for table in sorted(rows_full):
        assert rows_full[table] == rows_fast[table], table
