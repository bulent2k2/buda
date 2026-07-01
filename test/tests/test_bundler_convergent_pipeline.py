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

"""Full-pipeline exercise of the bundler's CONVERGENT strategy (via the CLI).

CONVERGENT bundles nets by their *shared receiver instance* only, ignoring the
driver — so several nets from DIFFERENT drivers that fan in to one sink become a
single bundle.  These tests drive the whole flat pipeline through the CLI
(`run_bundler {STRICT|CONVERGENT}` → topology → planner → NUTS) for such a fan-in
and contrast the two strategies, to show whether CONVERGENT makes physical sense.

Finding (documented here and in docs/internal/convergent_bundling.md): it does
NOT, as currently modelled.  The topology generator represents a bundle by a
single src→dst pair, so a bundle whose nets have different drivers is routed from
ONE arbitrary driver and the others are silently left unrouted — NUTS places
horizontal runs at only one source row, vs all four under STRICT.
`check_connectivity` does not catch the omission (it checks a topology's internal
self-consistency, not fidelity to the original net drivers).  The CLI therefore
prints a warning when CONVERGENT is selected.
"""

import buda_cli

# Four source blocks at well-separated rows, each driving one net into a single
# common sink on the right.  Different drivers + shared receiver = the case where
# CONVERGENT differs from STRICT.
_TRACKS = "def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"
_SETUP = """add_block src0 0 0 100 80
add_block src1 0 200 100 280
add_block src2 0 400 100 480
add_block src3 0 600 100 680
add_block sink 800 250 950 450
add_net a0 src0.tx sink.r0
add_net a1 src1.tx sink.r1
add_net a2 src2.tx sink.r2
add_net a3 src3.tx sink.r3
"""

# Approximate row (y) of each source's horizontal run; ~200 units apart.
_SOURCE_ROWS = (40, 240, 440, 640)
_ROW_TOL = 80


def _build_session():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for line in (_TRACKS + _SETUP).strip().splitlines():
        sess.do_command(line)
    return sess


def _run_pipeline(strategy):
    """Bundle via the CLI (`run_bundler <strategy>`), then run the rest of the
    pipeline through the same session.  Returns (sess, raw_bundles)."""
    sess = _build_session()
    sess.do_command(f"run_bundler {strategy}")
    raw = [w.input.original_bundle for w in sess.bundles]
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        sess.do_command(cmd)
    return sess, raw


def _h_rows(sess):
    """Distinct horizontal-run rows (rounded) placed by NUTS — one per source row
    actually reached by routing."""
    return sorted({round(s.track_position)
                   for s in sess.nuts_result.segments if s.horiz})


def _covered(rows):
    return {t for t in _SOURCE_ROWS
            if any(abs(r - t) < _ROW_TOL for r in rows)}


def test_strict_fanin_routes_every_driver():
    # STRICT keeps the four different-driver nets in four separate bundles, each
    # routed from its own driver → every source row is reached.
    sess, raw = _run_pipeline("STRICT")
    assert len(raw) == 4
    assert sess.nuts_result.num_overlaps == 0
    rows = _h_rows(sess)
    assert len(rows) == 4
    assert _covered(rows) == set(_SOURCE_ROWS)   # all four drivers routed


def test_convergent_fanin_collapses_to_one_driver():
    # CONVERGENT groups all four nets (shared receiver `sink`) into ONE bundle.
    # The full pipeline RUNS, but the topology is modelled from a single driver,
    # so only that one source row is routed; the other three are left unrouted.
    sess, raw = _run_pipeline("CONVERGENT")
    assert len(raw) == 1
    assert raw[0].reason == "REC:sink"
    assert sorted(raw[0].get_net_names()) == ["a0", "a1", "a2", "a3"]

    rows = _h_rows(sess)
    covered = _covered(rows)
    assert len(covered) == 1, (
        f"convergent bundle should reach exactly one source row, got {covered}")
    # Three of the four drivers are silently unrouted — the crux of why the
    # option is unsound for genuinely convergent (different-driver) nets.
    assert len(set(_SOURCE_ROWS) - covered) == 3


def test_convergent_topo_check_does_not_flag_missing_drivers(capsys):
    # Gap: check_connectivity passes for the convergent bundle even though three
    # drivers are unrouted — it verifies the topology's own consistency, not that
    # the routed bundle matches the original per-net drivers.
    sess, _ = _run_pipeline("CONVERGENT")
    capsys.readouterr()                       # clear pipeline output
    sess.do_command("check_connectivity topo")
    out = capsys.readouterr().out.lower()
    assert "no opens" in out or "success" in out


def test_cli_run_bundler_honors_strategy_argument(capsys):
    # The CLI `run_bundler` command now honours its argument: STRICT yields four
    # bundles (one per driver), CONVERGENT yields the single shared-receiver
    # bundle and prints a warning about the unrouted-driver limitation.
    strict = _build_session()
    strict.do_command("run_bundler STRICT")
    assert len(strict.bundles) == 4

    conv = _build_session()
    capsys.readouterr()
    conv.do_command("run_bundler CONVERGENT")
    out = capsys.readouterr().out.lower()
    assert len(conv.bundles) == 1
    assert "warning" in out and "convergent" in out

    # A bare `run_bundler` still defaults to STRICT, and a bad argument errors.
    bare = _build_session()
    bare.do_command("run_bundler")
    assert len(bare.bundles) == 4
    bad = _build_session()
    capsys.readouterr()
    bad.do_command("run_bundler SOMETHING")
    assert "error" in capsys.readouterr().out.lower()
