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

"""The qor tools' parallel sweep (qor_corpus.sweep + --jobs).

Flows are independent (fresh session, own worker process), so a parallel
sweep must return the SAME rows as a serial one, in INPUT order, with only
the per-flow `sec` timing affected by contention.  These tests lock in the
harness semantics (ordering, progress callback, worker-failure err rows,
scheduling weight) without running any real flow — the real-flow
equivalence was measured at introduction (6-flow subset: rows identical,
19.9s -> 10.2s at -j 4).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import qor_corpus as qc  # noqa: E402


# Top-level so the ProcessPoolExecutor can pickle them by qualified name.
def _ok_run(flow):
    return {"flow": flow, "v": len(flow)}


def _boom_run(flow):
    if flow == "bad":
        raise RuntimeError("boom")
    return {"flow": flow, "v": len(flow)}


def test_serial_sweep_preserves_order_and_progress():
    seen = []
    rows = qc.sweep(_ok_run, ["aa", "b", "cccc"], jobs=1,
                    progress=seen.append)
    assert [r["flow"] for r in rows] == ["aa", "b", "cccc"]
    assert seen == rows                       # serial: progress in input order


def test_parallel_sweep_returns_input_order():
    flows = [f"flow_{i}" for i in range(8)]
    rows = qc.sweep(_ok_run, flows, jobs=4)
    # Completion order is nondeterministic; the RESULT order must not be.
    assert [r["flow"] for r in rows] == flows
    assert all(r["v"] == len(r["flow"]) for r in rows)


def test_parallel_sweep_records_worker_failure_as_err_row():
    rows = qc.sweep(_boom_run, ["good", "bad", "fine"], jobs=2)
    assert [r["flow"] for r in rows] == ["good", "bad", "fine"]
    assert "err" in rows[1] and "boom" in rows[1]["err"]
    assert "err" not in rows[0] and "err" not in rows[2]


def test_flow_weight_counts_referenced_bdb(tmp_path):
    big = tmp_path / "big.bdb.sql"
    big.write_text("x" * 10000)
    flow = tmp_path / "f.buda"
    flow.write_text("# hdr\nopen_bdb big.bdb.sql\nrun_nuts\n")
    lone = tmp_path / "lone.buda"
    lone.write_text("run_nuts\n")
    assert qc._flow_weight(str(flow)) > 10000        # flow + referenced BDB
    assert qc._flow_weight(str(lone)) < 100          # just the file itself
    assert qc._flow_weight(str(tmp_path / "missing.buda")) == 0


def test_default_jobs_at_least_one():
    assert qc.default_jobs() >= 1
