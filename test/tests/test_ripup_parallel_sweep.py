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

"""Parallel stall-certificate sweep (rnr runtime P1, trial_sweep.cpp).

At a screened-scan stall, ripup_reroute evaluates the deferred
stall-certificate moves on C++ worker threads (GIL released) instead of
one sequential trial per move.  The sweep's metrics implement the
sequential fast-trial semantics exactly, the first in-order improver is
REPLAYED through the normal sequential trial (the replay is the accept
basis and the committed state), and a sweep-vs-replay disagreement is a
LOUD warning with the replay verdict kept — so the parallel path must be
decision-identical to the sequential one (validated byte-identical on
the four rnr vehicle flows).

Covers: the sweep firing + deferred winner accept (all moves forced
deferred via _RR_SCREEN_TOP_N=0), parallel-vs-sequential agreement on
selections / metric / trial counts, the `no_parallel_sweep` opt-out
token, and (slow tier) end-to-end agreement of every ripup decision line
on the real mix2 bottom-up vehicle flow.
"""

import contextlib
import io
import pathlib
import re

import pytest

import buda_cli
import buda_session.ripup as ripup_mod

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RNR = _ROOT / "flow" / "rnr"


def _build_session(narrow=True):
    """The canned two-bus congestion fixture (test_ripup_reroute): two 8-bit
    buses pinned to straight I_H candidates collide in a keepout-narrowed M4
    band; an L_HV re-route clears the overlap."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_layer 7 M7 V TOP 50",
        "add_block D1 0 1000 200 1400",
        "add_block D2 400 1000 600 1400",
        "add_block R 2400 1000 2600 1400",
        "add_bus a[8] D1.p R.p",
        "add_bus b[8] D2.p R.p",
    ]
    if narrow:
        cmds += ["add_keepout 0 1220 3000 4000 4",
                 "add_keepout 0 0 3000 1180 4"]
    cmds += ["run_bundler", "generate_topologies",
             "select_topology 1 1", "select_topology 2 1",
             "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _run_ripup(s, arg=""):
    # Decision tracing on (risk_reduction_plan.md R2): identity tests
    # compare s._decision_trace records, not parsed log lines — wording
    # changes never re-baseline them.  The slow end-to-end test keeps the
    # byte-level log diff as the formatting canary.
    s._decision_trace = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"ripup_reroute {arg}".strip())
    return out.getvalue()


def _selections(s):
    return {w.input.original_bundle.id: w.plan.selected_topology_index
            for w in s.bundles}


def _done_line(out):
    m = re.search(r"\[ripup_reroute\] done: .*", out)
    assert m, out
    return m.group(0)


# ── the sweep fires and its winner commits ───────────────────────────────────

def test_parallel_sweep_wins_deferred_stall(monkeypatch):
    """With every screened move deferred (_RR_SCREEN_TOP_N=0) the first
    iteration stalls straight into the parallel sweep, which must find the
    healing move, replay it sequentially, and commit — with no
    sweep-vs-replay divergence."""
    monkeypatch.setattr(ripup_mod, "_RR_SCREEN_TOP_N", 0)
    s = _build_session()
    assert s.nuts_result.num_overlaps > 0
    out = _run_ripup(s)
    assert "deferred move(s) in parallel" in out, out
    assert ", deferred)" in out, out          # the sweep's winner committed
    assert "parallel-sweep divergence" not in out, out
    assert s.nuts_result.num_overlaps == 0, out


def test_parallel_matches_sequential_when_all_deferred(monkeypatch):
    """Decision identity: with all moves deferred, the parallel and
    sequential sweeps must pick the same move and report the same done
    line (same metric trajectory, move count, AND trial count)."""
    monkeypatch.setattr(ripup_mod, "_RR_SCREEN_TOP_N", 0)
    s_par = _build_session()
    out_par = _run_ripup(s_par)
    s_seq = _build_session()
    out_seq = _run_ripup(s_seq, "no_parallel_sweep")
    assert "in parallel" in out_par and "in parallel" not in out_seq
    assert _selections(s_par) == _selections(s_seq)
    assert _done_line(out_par) == _done_line(out_seq)
    assert s_par.nuts_result.num_overlaps == s_seq.nuts_result.num_overlaps


# ── the parallel PRIMARY scan (rnr runtime P1b) ──────────────────────────────

def test_parallel_scan_matches_sequential_defaults(monkeypatch):
    """With the DEFAULT screen (kept moves full-trialed), the primary scan
    runs on the sweep pool — and must be print- and decision-transparent:
    same selections, same done line (metric trajectory, move count AND trial
    count), same contender lines.  This fixture improves at the FIRST
    contender, so it also exercises the chunked early-exit path."""
    monkeypatch.setenv("BUDA_SWEEP_THREADS", "2")   # a real pool on any host
    s_par = _build_session()
    out_par = _run_ripup(s_par)
    s_seq = _build_session()
    out_seq = _run_ripup(s_seq, "no_parallel_sweep")
    assert _selections(s_par) == _selections(s_seq)
    assert _done_line(out_par) == _done_line(out_seq)
    # The decision RECORDS (contender heartbeats + improver + done, with
    # their structured fields incl. trial counts) are identical — the scan
    # sweep makes exactly the sequential decisions.  The stall-sweep
    # announce is the one legitimately parallel-only record.
    def trace(s):
        return [(tag, kv) for tag, kv in s._decision_trace
                if tag != "rr_stall_sweep"]
    assert trace(s_par) == trace(s_seq)
    assert not any(t == "rr_divergence" for t, _ in s_par._decision_trace)
    assert s_par.nuts_result.num_overlaps == 0


def test_parallel_scan_books_psweep_time(monkeypatch):
    """The scan sweep actually engages on the default path: the timing line
    carries psweep evaluations (the sequential path books none)."""
    monkeypatch.setenv("BUDA_SWEEP_THREADS", "2")   # a real pool on any host
    s = _build_session()
    out = _run_ripup(s)
    m = re.search(r"psweep [0-9.]+s/(\d+)", out)
    assert m and int(m.group(1)) >= 1, out


def test_zero_move_contenders_keep_their_heartbeats(monkeypatch):
    """Codex #604: a contender with NO index alternates (e.g. every
    alternate width-gated away) must still print its sequential `— no
    improvement` heartbeat on the parallel path — the sequential loop
    passes the empty list through _rr_scan_moves and prints it."""
    monkeypatch.setenv("BUDA_SWEEP_THREADS", "2")
    monkeypatch.setattr(ripup_mod.RipupMixin, "_rr_candidate_order",
                        lambda self, w, old_tidx, stage: [])
    s_par = _build_session()
    out_par = _run_ripup(s_par)
    s_seq = _build_session()
    out_seq = _run_ripup(s_seq, "no_parallel_sweep")
    def heartbeats(s):
        return [kv for tag, kv in s._decision_trace if tag == "rr_heartbeat"]
    assert heartbeats(s_par) == heartbeats(s_seq)
    assert len(heartbeats(s_par)) >= 1, out_par
    assert _done_line(out_par) == _done_line(out_seq)


def test_sequential_paths_never_parse_thread_env(monkeypatch):
    """Codex #604: an inherited nonnumeric BUDA_THREADS must not crash runs
    that never use the pool (`no_parallel_sweep`), and the parallel path
    falls back to auto instead of raising."""
    monkeypatch.setenv("BUDA_THREADS", "not-a-number")
    monkeypatch.delenv("BUDA_SWEEP_THREADS", raising=False)
    s = _build_session()
    out = _run_ripup(s, "no_parallel_sweep")
    assert "done:" in out
    assert s.nuts_result.num_overlaps == 0
    s = _build_session()          # default path: tolerant parse -> auto
    out = _run_ripup(s)
    assert "done:" in out
    assert s.nuts_result.num_overlaps == 0


# ── the opt-out token ────────────────────────────────────────────────────────

def test_no_parallel_sweep_token():
    """`no_parallel_sweep` forces the sequential deferred sweep and still
    heals the canned overlap."""
    s = _build_session()
    out = _run_ripup(s, "no_parallel_sweep")
    assert "in parallel" not in out, out
    assert s.nuts_result.num_overlaps == 0, out


# ── end-to-end agreement on the real vehicle (slow) ──────────────────────────

@pytest.mark.slow
def test_mix2_bottomup_parallel_sweep_agreement():
    """The mix2 bottom-up vehicle exercises the sweep at both stages (a and
    b, incl. the DNUTS copy-plan port).  Every ripup decision line —
    contender improvements, stall/global/class prints, and the done lines
    with their trial counts — must be identical between the parallel and
    sequential paths."""
    def run(tok):
        s = buda_cli.BudaSession()
        s.no_viz = True
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for c in [f"source {_RNR / 'mix_tracks.buda'}",
                      f"open_bdb {_RNR / 'mix2.bdb.sql'}",
                      "set_bottom_up dnuts1", "set_bottom_up dnuts2",
                      "set_bottom_up dogleg1", "set_bottom_up dogleg2",
                      "derive_busterms 2",
                      "add_blocks_from_bdb 0",
                      "add_blocks_from_bdb 1 skip",
                      "add_blocks_from_bdb 2 skip",
                      "run_hier_bundler depth 2",
                      "generate_hier_topologies",
                      "run_planner hier 5 signal_tracks",
                      "run_nuts",
                      "negotiate_congestion",
                      f"ripup_reroute {tok}".strip(),
                      "check_template_tracks on_mismatch independent",
                      "run_detailed_nuts",
                      "negotiate_congestion",
                      f"ripup_reroute {tok}".strip()]:
                s.do_command(c)
        # timing/profile lines vary run to run — keep only the decision
        # content ("in parallel" is the parallel path's stall-line suffix).
        lines = [re.sub(r" in parallel$", "", ln)
                 for ln in out.getvalue().splitlines()
                 if (ln.startswith("[ripup_reroute]")
                     and "timing:" not in ln
                     and "solve passes:" not in ln)]
        return s, lines

    s_par, lines_par = run("")
    s_seq, lines_seq = run("no_parallel_sweep")
    assert lines_par == lines_seq
    assert _selections(s_par) == _selections(s_seq)
    assert (s_par.detailed_result.num_unplaced
            == s_seq.detailed_result.num_unplaced)
    assert "parallel-sweep divergence" not in "\n".join(lines_par)
