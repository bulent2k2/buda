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

BIDIRECTIONAL is direction-agnostic: its signature is the sorted set of ALL
endpoint instances (driver + receivers), so nets that connect the same group of
blocks in any driver/receiver roles bundle together — A→B with its return B→A,
and the cyclic multi-receiver case a→b,c / b→c,a / c→b,a.

Routing is block-to-block and direction-agnostic, so the single trunk that
connects the group serves every net — unlike CONVERGENT (whose fan-in sources are
DIFFERENT blocks), this is sound.  In the visualizer such a busterm is both a
driver and a receiver, so it gets its own (green diamond) symbol.
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

# Cyclic multi-receiver: three blocks each driving the other two.
_CYCLIC = """def_layer 4 M4 H TOP 50
def_layer 5 M5 V TOP 50
add_block a 0 0 100 80
add_block b 400 0 500 80
add_block c 800 0 900 80
add_net n1 a.tx b.r0,c.r0
add_net n2 b.tx c.r1,a.r0
add_net n3 c.tx b.r1,a.r1
"""


def _session(setup):
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for line in setup.strip().splitlines():
        sess.do_command(line)
    return sess


def _netsets(sess):
    return sorted(tuple(sorted(w.input.original_bundle.get_net_names()))
                  for w in sess.bundles)


def test_strict_keeps_forward_and_reverse_separate():
    # Baseline: STRICT bundles by driver, so A→B and B→A never merge.
    sess = _session(_TRACKS + _SETUP)
    sess.do_command("run_bundler STRICT")
    assert _netsets(sess) == [
        ("fwd0", "fwd1"), ("oneway",), ("rev0", "rev1")]


def test_bidirectional_pairs_a_net_with_its_reverse():
    # BIDIRECTIONAL merges the forward bus with its return path into one bundle;
    # the one-way net (a different block pair) stays a singleton.
    sess = _session(_TRACKS + _SETUP)
    sess.do_command("run_bundler BIDIRECTIONAL")
    assert _netsets(sess) == [
        ("fwd0", "fwd1", "rev0", "rev1"), ("oneway",)]
    reasons = {tuple(sorted(w.input.original_bundle.get_net_names())):
               w.input.original_bundle.reason for w in sess.bundles}
    assert reasons[("fwd0", "fwd1", "rev0", "rev1")] == "BIDIR:A,B"
    assert reasons[("oneway",)] == "BIDIR:C,D"


def test_bidirectional_bundles_cyclic_multireceiver():
    # The generalized (sort-all-pins) signature groups a→b,c with b→c,a and
    # c→b,a: all three touch the instance set {a,b,c} regardless of direction.
    sess = _session(_CYCLIC)
    sess.do_command("run_bundler BIDIRECTIONAL")
    assert _netsets(sess) == [("n1", "n2", "n3")]
    assert sess.bundles[0].input.original_bundle.reason == "BIDIR:a,b,c"


def test_bidirectional_pipeline_routes_the_whole_group():
    # Direction-agnostic soundness: the single trunk connecting the group serves
    # every net.  The cyclic a/b/c blocks sit on one row, so a horizontal trunk
    # spans from the first block to the last, reaching all three.
    sess = _session(_CYCLIC)
    sess.do_command("run_bundler BIDIRECTIONAL")
    for cmd in ("generate_topologies", "run_planner", "run_nuts"):
        sess.do_command(cmd)
    segs = sess.nuts_result.segments
    assert segs and sess.nuts_result.num_overlaps == 0
    # A horizontal run whose span reaches from block `a` (x≈0-100) across to
    # block `c` (x≈800-900) — i.e. it connects the whole group, not just a pair.
    hspans = [(min(s.span_lo, s.span_hi), max(s.span_lo, s.span_hi))
              for s in segs if s.horiz]
    assert any(lo <= 100 and hi >= 800 for lo, hi in hspans), hspans


def test_cli_bidirectional_is_sound_and_validates(capsys):
    # BIDIRECTIONAL is direction-agnostic and routes correctly, so — unlike
    # CONVERGENT — it does NOT warn.
    sess = _session(_TRACKS + _SETUP)
    capsys.readouterr()
    sess.do_command("run_bundler BIDIRECTIONAL")
    out = capsys.readouterr().out.lower()
    assert "warning" not in out
    assert len(sess.bundles) == 2

    # A bad strategy argument is still rejected.
    bad = _session(_TRACKS + _SETUP)
    capsys.readouterr()
    bad.do_command("run_bundler SIDEWAYS")
    assert "error" in capsys.readouterr().out.lower()
