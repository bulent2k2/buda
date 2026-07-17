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

"""WL-dominance candidate pruning (`set_prune_dominated`, opt-in).

A candidate whose WL envelope bottom (wl_lo) exceeds another candidate's
envelope top (wl_hi) is deterministically WL-dominated — but it is only
DROPPED when the survivor is equivalent in every non-WL respect the planner
scores or the escalation ladder exploits: same block contract / feedthru
declarations, same segment directions + layer hints, survivor slide windows
COVERING the dominated one's, survivor spans INSIDE the dominated one's.
The gate is the load-bearing part (Codex P2 on PR #313): a longer candidate
can be the only overflow-free / window-feasible option, so a false prune is
a correctness bug while a missed prune is only wasted planner work."""

import io
from contextlib import redirect_stdout

import buda


def _session(lines):
    from buda_cli import BudaSession
    s = BudaSession()
    for line in lines:
        s.do_command(line)
    return s


_SETUP = [
    "def_layer 4 M4 H TOP 0.0",
    "def_layer 5 M5 V TOP 0.0",
    "def_layer 7 M7 V TOP 0.0",
    "set_min_stub_length 0",
    # A wide+thin, B offset up-right: the far-face detour L is strictly
    # longer than the near-face L in EVERY slide configuration.
    "add_block A 0 0 50 4",
    "add_block B 60 40 70 50",
    "add_bus a[4] A B",
    "run_bundler",
    "generate_topologies",
]


def _mk_topo(s, name, segs, wl):
    """Hand-build a candidate: same corridors ConnTopology resolves for
    generated candidates (annotate_topology fills seg_busterms/seg_conns)."""
    t = buda.Topology()
    t.type = name
    built = []
    for (x1, y1, x2, y2, hint) in segs:
        g = buda.Segment()
        g.start = buda.Point(x1, y1)
        g.end = buda.Point(x2, y2)
        g.layer_hint = hint
        built.append(g)
    t.segments = built
    t.estimated_wirelength = wl
    t.connected_block_names = ["A", "B"]
    buda.annotate_topology(t, s.fp)
    return t


def _short_L(s):
    # A right face -> corner -> B bottom face.  Envelope ~[46..60].
    return _mk_topo(s, "HAND_SHORT",
                    [(50, 2, 65, 2, 4), (65, 2, 65, 40, 5)], 53)


def _long_L(s, v_layer=5):
    # Same corridors, but tapping A's FAR (left) face and B's TOP face:
    # strictly more wire on the same windows.  Envelope ~[106..120].
    return _mk_topo(s, "HAND_LONG",
                    [(0, 2, 65, 2, 4), (65, 2, 65, 50, v_layer)], 113)


def test_dominated_equivalent_pair_is_pruned_with_note():
    s = _session(_SETUP)
    w = s.bundles[0]
    w.input.candidates = [_short_L(s), _long_L(s)]
    s.do_command("set_prune_dominated on")
    buf = io.StringIO()
    with redirect_stdout(buf):
        pruned, refused = s._prune_dominated_pools([w])
    out = buf.getvalue()
    assert (pruned, refused) == (1, 0), out
    assert [c.type for c in w.input.candidates] == ["HAND_SHORT"]
    # The printed note names both candidates and the dominance.
    assert "[TopoPrune]" in out and "WL-dominated" in out, out
    assert "HAND_LONG" in out and "HAND_SHORT" in out, out


def test_dominated_but_different_layer_is_kept():
    """WL-dominated, but the dominated candidate's V segment rides M7 while
    the survivor's rides M5 — not equivalent, must be KEPT (it could be the
    only overflow-free option on its layer)."""
    s = _session(_SETUP)
    w = s.bundles[0]
    w.input.candidates = [_short_L(s), _long_L(s, v_layer=7)]
    s.do_command("set_prune_dominated on")
    buf = io.StringIO()
    with redirect_stdout(buf):
        pruned, refused = s._prune_dominated_pools([w])
    assert pruned == 0 and refused >= 1, buf.getvalue()
    assert [c.type for c in w.input.candidates] == ["HAND_SHORT", "HAND_LONG"]


def test_gate_window_containment_direction():
    """The SURVIVOR must cover the dominated candidate's slide window — a
    dominated candidate with a window the survivor does NOT cover is kept
    (it can reach bands the survivor cannot: the escalation ladder / ripup
    may need exactly that).  Exercised at the gate level so the containment
    direction is pinned explicitly."""
    from buda_cli import BudaSession
    blocks = frozenset({"A", "B"})
    none = frozenset()

    def info(win, along, lo, hi):
        return {"lo": lo, "hi": hi, "nominal": (lo + hi) / 2,
                "blocks": blocks, "feedthru": none,
                "segs": [(True, 4, win[0], win[1], along[0], along[1])]}

    gate = BudaSession._wl_prune_equivalent
    # Survivor window [0,4] does NOT cover dominated window [0,20]: refuse.
    assert not gate(info((0, 20), (0, 65), 100, 120),
                    info((0, 4), (10, 65), 40, 60))
    # Covering direction (survivor [0,20] ⊇ dominated [0,4]): equivalent.
    assert gate(info((0, 4), (0, 65), 100, 120),
                info((0, 20), (10, 65), 40, 60))
    # Survivor span must lie INSIDE the dominated one's: a survivor sticking
    # out along [10,80] over dominated [0,65] crosses cuts the dominated
    # never did — refuse.
    assert not gate(info((0, 4), (0, 65), 100, 120),
                    info((0, 4), (10, 80), 40, 60))
    # Different block contract: refuse.
    d = info((0, 4), (0, 65), 100, 120)
    d["blocks"] = frozenset({"A", "B", "C"})
    assert not gate(d, info((0, 4), (10, 65), 40, 60))


def test_off_by_default_pool_unchanged():
    """Without set_prune_dominated the pool is untouched even when a
    dominated + equivalent pair exists (bit-identical default)."""
    s = _session(_SETUP)
    w = s.bundles[0]
    w.input.candidates = [_short_L(s), _long_L(s)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        res = s._prune_dominated_pools([w])
    assert res == (0, 0)
    assert [c.type for c in w.input.candidates] == ["HAND_SHORT", "HAND_LONG"]
    assert "[TopoPrune]" not in buf.getvalue()

    # And explicitly off after on.
    s.do_command("set_prune_dominated on")
    s.do_command("set_prune_dominated off")
    with redirect_stdout(io.StringIO()):
        res = s._prune_dominated_pools([w])
    assert res == (0, 0)
    assert len(w.input.candidates) == 2


def test_generate_topologies_prints_summary_when_enabled():
    """End-to-end command wiring: with the flag on, generate_topologies runs
    the prune pass and prints the summary line (counts may be zero on this
    small pool — the gate is deliberately conservative)."""
    s = _session(["def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
                  "add_block l 0 0 100 100", "add_block r 300 0 400 100",
                  "add_bus a[4] l r", "run_bundler",
                  "set_prune_dominated on"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("generate_topologies")
    assert "[TopoPrune] pruned" in buf.getvalue(), buf.getvalue()
    # USER candidates and the selected candidate are never pruned.
    w = s.bundles[0]
    user = _mk_topo(s, "USER", [(100, 50, 300, 50, 4)], 200)
    # (block names differ in this floorplan; contract left as-is is fine —
    # a USER candidate is skipped before any gate check.)
    pool = list(w.input.candidates) + [user]
    w.input.candidates = pool
    with redirect_stdout(io.StringIO()):
        s._prune_dominated_pools([w])
    assert any(c.type == "USER" for c in w.input.candidates)
