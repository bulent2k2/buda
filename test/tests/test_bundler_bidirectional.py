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

"""The bundler's BIDIRECTIONAL strategy (via the CLI `run_bundler`).

BIDIRECTIONAL pairs a net with its reverse: a receiver instance of one net is
the driver of the other and vice-versa (A→B bundled with B→A — a bus and its
return path).  Unpaired one-way nets stay singletons.

Because such a bundle spans two drivers, it hits the same single-`src→dst`
topology limitation as CONVERGENT (only one direction routes; the reverse is left
unrouted), so the CLI prints a warning.  Full analysis:
docs/internal/convergent_bundling.md.
"""

import buda_cli

_TRACKS = "def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"
# A↔B: a 2-bit forward bus (A→B) and its 2-bit return (B→A); plus a one-way C→D.
_SETUP = """add_block A 0 0 100 80
add_block B 400 0 500 80
add_block C 0 400 100 480
add_block D 400 400 500 480
add_net fwd0 A.tx B.r0
add_net rev0 B.tx A.r0
add_net fwd1 A.tx B.r1
add_net rev1 B.tx A.r1
add_net oneway C.tx D.r0
"""


def _build_session():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for line in (_TRACKS + _SETUP).strip().splitlines():
        sess.do_command(line)
    return sess


def _bundles(sess):
    return [w.input.original_bundle for w in sess.bundles]


def _netsets(sess):
    return sorted(tuple(sorted(b.get_net_names())) for b in _bundles(sess))


def test_strict_keeps_forward_and_reverse_separate():
    # Baseline: STRICT bundles by driver, so A→B and B→A never merge.
    sess = _build_session()
    sess.do_command("run_bundler STRICT")
    assert _netsets(sess) == [
        ("fwd0", "fwd1"), ("oneway",), ("rev0", "rev1")]


def test_bidirectional_pairs_a_net_with_its_reverse():
    # BIDIRECTIONAL merges the forward bus with its return path into one bundle;
    # the one-way net (no reverse partner) stays a singleton.
    sess = _build_session()
    sess.do_command("run_bundler BIDIRECTIONAL")
    assert _netsets(sess) == [
        ("fwd0", "fwd1", "rev0", "rev1"), ("oneway",)]
    # The paired bundle names both talking instances; the singleton names one.
    reasons = {tuple(sorted(b.get_net_names())): b.reason for b in _bundles(sess)}
    assert reasons[("fwd0", "fwd1", "rev0", "rev1")] == "BIDIR:A,B"
    assert reasons[("oneway",)] == "BIDIR:C"


def test_bidirectional_pipeline_runs_end_to_end():
    # The bidirectional bundle spans two drivers, so — like CONVERGENT — only one
    # direction routes; the pipeline must still complete without error.
    sess = _build_session()
    sess.do_command("run_bundler BIDIRECTIONAL")
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        sess.do_command(cmd)
    assert sess.nuts_result is not None
    assert len(sess.nuts_result.segments) > 0
    assert sess.nuts_result.num_overlaps == 0


def test_cli_run_bundler_bidirectional_warns_and_validates(capsys):
    # The CLI honours BIDIRECTIONAL and warns about the unrouted reverse path.
    sess = _build_session()
    capsys.readouterr()
    sess.do_command("run_bundler BIDIRECTIONAL")
    out = capsys.readouterr().out.lower()
    assert "warning" in out and "bidirectional" in out
    assert len(sess.bundles) == 2

    # A bad strategy argument is rejected.
    bad = _build_session()
    capsys.readouterr()
    bad.do_command("run_bundler SIDEWAYS")
    assert "error" in capsys.readouterr().out.lower()
