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
import json
import os
import signal
import sys
import time
import types

import pytest

# mid tier: dev-TOOLING tests (qor_corpus sweep harness) — out of the fast
# inner loop; run with `bb -m`/`bb -s`.
pytestmark = pytest.mark.mid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import qor_corpus as qc  # noqa: E402


# Top-level so the ProcessPoolExecutor can pickle them by qualified name.
def _ok_run(flow):
    return {"flow": flow, "v": len(flow)}


def _boom_run(flow):
    if flow == "bad":
        raise RuntimeError("boom")
    return {"flow": flow, "v": len(flow)}


def _stamped_run(flow):
    # (pid, perf_counter_ns) is unique per CALL: two calls in one process have
    # strictly increasing ns; calls in different processes differ by pid.
    return {"flow": flow, "pid": os.getpid(), "t": time.perf_counter_ns()}


def _kill_run(flow):
    if flow == "die":
        os.kill(os.getpid(), signal.SIGKILL)    # hard crash, not an exception
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


def test_duplicate_flows_keep_both_runs():
    # A flow listed twice (--flows timing/nondeterminism check) must keep BOTH
    # runs' results — results are keyed by submission index, not flow string,
    # so the second completion cannot overwrite the first (Codex #541).
    rows = qc.sweep(_stamped_run, ["a", "a", "bb"], jobs=2)
    assert [r["flow"] for r in rows] == ["a", "a", "bb"]
    stamps = {(r["pid"], r["t"]) for r in rows}
    assert len(stamps) == 3                     # three distinct executions kept


@pytest.mark.skipif(sys.platform == "win32",
                    reason="SIGKILL does not exist on Windows.  Note the sweep "
                           "harness itself held up there: the un-killable kill "
                           "was still recorded as this flow's err row "
                           "(doc-validation run 8) -- only the POSIX kill "
                           "mechanism is untestable, not the recovery contract")
def test_hard_crash_loses_only_the_crashing_flow():
    """A worker SIGKILL breaks the whole ProcessPoolExecutor (every pending
    future raises BrokenProcessPool, not just the culprit's) — the recovery
    path re-runs the unfinished flows in throwaway single-worker pools, so
    the crashing flow alone gets the err row and its innocent neighbors still
    produce real results (Codex #541)."""
    flows = ["aa", "die", "bbb", "c"]
    rows = qc.sweep(_kill_run, flows, jobs=2)
    assert [r["flow"] for r in rows] == flows
    by = {r["flow"]: r for r in rows}
    assert "err" in by["die"] and "crash" in by["die"]["err"]
    for f in ("aa", "bbb", "c"):
        assert "err" not in by[f], by[f]
        assert by[f]["v"] == len(f)


def test_private_log_dir_only_in_workers(tmp_path, monkeypatch):
    s = types.SimpleNamespace(_log_run_dir=None)
    # Outside a worker: no-op, historical log locations kept (serial sweeps).
    monkeypatch.setattr(qc, "_IN_WORKER", False)
    assert qc.private_log_dir(s) is None
    assert s._log_run_dir is None
    # Inside a worker: a private throwaway dir, via the --log redirect field.
    monkeypatch.setattr(qc, "_IN_WORKER", True)
    d = qc.private_log_dir(s)
    try:
        assert d is not None and os.path.isdir(d)
        assert s._log_run_dir == d
    finally:
        os.rmdir(d)


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


# --- qor_table --json: the nightly's change-detection input -----------------
#
# The nightly workflow refreshes qor/qor_table.md and decides whether to open a PR
# by diffing this JSON with `sec` dropped.  Two properties make that gate work,
# and both are silent-failure modes if they break: a `sec`-only difference must
# NOT read as a change (else a PR opens every night and buries the signal), and
# the row order must not depend on the CORPUS list's order (else moving an
# entry renders as a whole-file diff).

def _isolate_sidecar(qt, tmp_path, monkeypatch=None):
    """Point the serial-times sidecar at tmp_path.

    main() chdir's to the REPO ROOT and save_serial_times() defaults to the
    checked-in qor/qor_serial_times.json, so ANY test that reaches it overwrites
    real measured data.  That is not hypothetical: a mutation run that disabled
    the --fail-on-err guard let a test fall through to save_serial_times and
    replace 37 real serial timings with {"ok.buda": 1.0, stamp: "s"}, which was
    then committed by `git add -A` and shipped.  Isolate unconditionally so no
    ordering assumption inside main() is load-bearing for test safety.
    """
    path = str(tmp_path / "serial_times.json")
    if monkeypatch is not None:
        monkeypatch.setattr(qt, "_SERIAL_PATH", path)
    else:
        qt._SERIAL_PATH = path
    return path


def _rows_json(tmp_path, rows, order):
    """Run qor_table.main() with --json over a fake corpus, return the file."""
    import qor_table as qt
    out = tmp_path / "rows.json"
    fake = {r["flow"]: r for r in rows}
    monkey = types.SimpleNamespace(
        sweep=lambda run_fn, flows, jobs, progress=None: [fake[f] for f in flows],
        CORPUS=order, default_jobs=lambda: 1)
    old_qc, old_argv, old_sp = qt.qc, sys.argv, qt._SERIAL_PATH
    try:
        _isolate_sidecar(qt, tmp_path)
        qt.qc = types.SimpleNamespace(**{**vars(old_qc), **vars(monkey)})
        sys.argv = ["qor_table.py", "--json", str(out), "--stamp", "fixed"]
        qt.main()
    finally:
        qt.qc, sys.argv, qt._SERIAL_PATH = old_qc, old_argv, old_sp
    return json.loads(out.read_text())


def _row(flow, sec, netS=10):
    return {"flow": flow, "bund": 1, "busS": 2, "netS": netS, "busWL": 3,
            "netWL": 4, "ovl": 0, "unpl": 0, "viol": 0, "sec": sec}


def test_table_json_is_sorted_independently_of_corpus_order(tmp_path, capsys):
    rows = [_row("z.buda", 1.0), _row("a.buda", 2.0), _row("m.buda", 3.0)]
    one = _rows_json(tmp_path, rows, ["z.buda", "a.buda", "m.buda"])
    two = _rows_json(tmp_path, rows, ["m.buda", "z.buda", "a.buda"])
    assert [r["flow"] for r in one] == ["a.buda", "m.buda", "z.buda"]
    assert one == two            # reordering the corpus list changes nothing


def test_semantic_diff_ignores_timing_only_churn():
    """A re-run differing ONLY in `sec` must not read as a change -- the exact
    predicate that stops the nightly opening a no-op PR every single night."""
    import qor_table as qt
    before = [_row("a.buda", 1.0), _row("b.buda", 2.0)]
    after = [_row("a.buda", 9.9), _row("b.buda", 8.8)]
    assert before != after                                # sec really did move
    assert qt.semantic_diff(before, after) == ([], [], [])


def test_semantic_diff_surfaces_a_real_qor_change():
    """...and a genuine metric move must NOT be masked by that same tolerance."""
    import qor_table as qt
    before = [_row("a.buda", 1.0)]
    after = [dict(_row("a.buda", 1.0), unpl=16)]
    assert qt.semantic_diff(before, after) == ([], [], [("a.buda", ["unpl"])])


def test_semantic_diff_reports_corpus_membership_changes():
    """A flow ADDED to or REMOVED from the corpus is a change: qor_corpus's own
    compare prints a new row as '(new)' without counting it, which is how an
    errored new row could once have been promoted silently."""
    import qor_table as qt
    before = [_row("a.buda", 1.0), _row("gone.buda", 1.0)]
    after = [_row("a.buda", 1.0), _row("fresh.buda", 1.0)]
    added, removed, moved = qt.semantic_diff(before, after)
    assert added == ["fresh.buda"] and removed == ["gone.buda"] and moved == []


def test_diff_cli_writes_github_output(tmp_path, monkeypatch, capsys):
    """The --diff CLI is what the workflow actually invokes: it must emit the
    step output the `if:` conditions branch on."""
    import qor_table as qt
    old = tmp_path / "old.json"
    new_ = tmp_path / "new.json"
    old.write_text(json.dumps([_row("a.buda", 1.0)]))
    new_.write_text(json.dumps([dict(_row("a.buda", 5.0), ovl=3)]))
    gh = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    monkeypatch.setattr(sys, "argv",
                        ["qor_table.py", "--diff", str(old), str(new_)])
    qt.main()                       # must NOT sweep any flow
    assert "changed=true" in gh.read_text()
    assert "ovl 0->3" in capsys.readouterr().out


def test_diff_cli_treats_a_missing_baseline_as_changed(tmp_path, monkeypatch, capsys):
    """First run, or the sidecar just added: publish rather than skip forever."""
    import qor_table as qt
    new_ = tmp_path / "new.json"
    new_.write_text(json.dumps([_row("a.buda", 1.0)]))
    gh = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    monkeypatch.setattr(sys, "argv",
                        ["qor_table.py", "--diff", str(tmp_path / "nope.json"),
                         str(new_)])
    qt.main()
    assert "changed=true" in gh.read_text()


def test_fail_on_err_refuses_to_write_a_snapshot(tmp_path, monkeypatch, capsys):
    """A flow that RAISES is recorded as an err row and rendered as `ERR: ...`
    in place of its metrics, while the sweep still exits 0.  Unattended, that
    would be published to the authoritative snapshot as if it were data -- and
    semantic_diff reads the vanished fields as a change, so it would open the
    PR too.  --fail-on-err must stop BEFORE writing anything."""
    import qor_table as qt
    out_md = tmp_path / "t.md"
    out_js = tmp_path / "t.json"
    rows = [_row("ok.buda", 1.0), {"flow": "bad.buda", "err": "RuntimeError: boom"}]
    fake = types.SimpleNamespace(
        sweep=lambda run_fn, flows, jobs, progress=None: rows,
        CORPUS=["ok.buda", "bad.buda"], default_jobs=lambda: 1)
    old_qc = qt.qc
    _isolate_sidecar(qt, tmp_path, monkeypatch)
    try:
        qt.qc = types.SimpleNamespace(**{**vars(old_qc), **vars(fake)})
        monkeypatch.setattr(sys, "argv",
                            ["qor_table.py", "--fail-on-err", "--stamp", "s",
                             "--out", str(out_md), "--json", str(out_js)])
        try:
            qt.main()
        except SystemExit as e:
            assert e.code not in (0, None), "must exit non-zero"
        else:
            raise AssertionError("--fail-on-err did not exit")
    finally:
        qt.qc = old_qc
    # Nothing half-truthful left behind for a later step to pick up.
    assert not out_md.exists() and not out_js.exists()
    assert "bad.buda" in capsys.readouterr().err


def test_err_row_would_otherwise_read_as_a_semantic_change():
    """Why the guard above matters: without it the err row reaches the gate and
    is indistinguishable from a real QoR move."""
    import qor_table as qt
    before = [_row("x.buda", 1.0)]
    after = [{"flow": "x.buda", "err": "RuntimeError: boom"}]
    added, removed, moved = qt.semantic_diff(before, after)
    assert (added, removed) == ([], [])
    assert moved and moved[0][0] == "x.buda"


def test_committed_serial_times_sidecar_is_real_data():
    """The CHECKED-IN qor/qor_serial_times.json must hold real measurements.

    This is a data-integrity guard on a file that tooling writes and `git add`
    can sweep up.  It exists because exactly that happened: a mutation run
    disabled the --fail-on-err guard, a test fell through to save_serial_times()
    against the real repo-root path, and {"stamp": "s", "times": {"ok.buda":
    1.0}} was committed -- silently blanking the sec1 column of every row in
    qor/qor_table.md and reducing the legend to 'captured s'.  Nothing failed; the
    corruption only surfaced when a nightly-generated PR was read by eye.

    Cheap, and it fires on ANY future clobber regardless of cause.
    """
    import re
    import qor_table as qt
    data = qt.load_serial_times()
    assert data is not None, f"{qt._SERIAL_PATH} missing or malformed"
    # A real stamp is "<YYYY-MM-DD> (main @ <sha>)" -- test stamps are not.
    assert re.match(r"^\d{4}-\d{2}-\d{2} \(main @ [0-9a-f]{7,40}\)$",
                    data["stamp"]), f"implausible stamp: {data['stamp']!r}"
    times = data["times"]
    assert len(times) >= 25, f"only {len(times)} timings — looks truncated"
    # Real corpus keys are flow paths, not bare fixture names like "ok.buda".
    bad = [f for f in times if not f.startswith("flow/") or not f.endswith(".buda")]
    assert not bad, f"non-corpus entries in the sidecar: {bad[:5]}"
    assert all(isinstance(v, (int, float)) and v >= 0 for v in times.values())


# --- qor_corpus --check: the errored-sweep guard shared by both CI gates -----

def test_check_passes_a_clean_sweep(tmp_path, capsys):
    import qor_corpus as q
    p = tmp_path / "ok.json"
    p.write_text(json.dumps([{"flow": "a.buda", "overlaps": 0, "unplaced": 0},
                             {"flow": "b.buda", "overlaps": 1, "unplaced": 0}]))
    assert q.cmd_check(str(p)) == 0
    assert "all 2 corpus flows" in capsys.readouterr().out


def test_check_rejects_and_names_errored_flows(tmp_path, capsys):
    """An errored sweep must not be cached, promoted, or compared as data.
    cmd_run records the row and still exits 0, so this is the only thing that
    catches it."""
    import qor_corpus as q
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"flow": "a.buda", "overlaps": 0},
                             {"flow": "b.buda", "err": "RuntimeError: boom"},
                             {"flow": "c.buda", "err": "worker crash"}]))
    assert q.cmd_check(str(p)) == 2
    out = capsys.readouterr().out
    assert "::error::" in out and "b.buda" in out and "c.buda" in out


def test_check_cli_exits_nonzero_on_err(tmp_path, monkeypatch):
    """The CLI form the workflows call: the exit CODE is what gates the cache
    save, so a bad file must not exit 0."""
    import qor_corpus as q
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"flow": "a.buda", "err": "boom"}]))
    monkeypatch.setattr(sys, "argv", ["qor_corpus.py", "--check", str(p)])
    with pytest.raises(SystemExit) as e:
        q.main()
    assert e.value.code == 1                       # non-zero => cache not saved

    good = tmp_path / "ok.json"
    good.write_text(json.dumps([{"flow": "a.buda", "overlaps": 0}]))
    monkeypatch.setattr(sys, "argv", ["qor_corpus.py", "--check", str(good)])
    with pytest.raises(SystemExit) as e:
        q.main()
    assert e.value.code == 0                       # runs NO flows, just checks
