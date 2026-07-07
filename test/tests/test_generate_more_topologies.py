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

"""Phase E2 — additive generation (`generate_more_topologies`).

Unlike `generate_topologies_for_bundle` (regenerate-and-replace, pin
re-attached by uid), the additive command APPENDS knob-produced candidates to
the existing pool, deduplicated by stable content uid, leaving existing
indices — and therefore the pin and plan state — untouched.
"""
import contextlib
import io

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _session():
    """A 6-block fan-out so multi_trunk actually contributes new BITRUNK
    shapes the base run does not produce."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 20", "def_layer 5 M5 V TOP 20"]
    for i in range(6):
        x, y = (i % 3) * 400, (i // 3) * 600
        cmds.append(f"add_block b{i} {x} {y} {x + 200} {y + 300}")
    cmds += ["add_bus d[8] b0.p b1.q,b2.r,b3.s,b4.t,b5.u",
             "run_bundler", "generate_topologies"]
    _quiet(s, *cmds)
    return s


def test_additive_appends_dedupes_and_preserves_pin(capsys):
    s = _session()
    w = s.bundles[0]
    base = list(w.input.candidates)
    assert base, "base generation produced no candidates"
    base_uids = [buda.topo_uid(c) for c in base]

    pin_idx = min(1, len(base) - 1)
    bid = w.input.original_bundle.id
    _quiet(s, f"select_topology {bid} {pin_idx + 1}")

    s.do_command("generate_more_topologies d multi_trunk")
    out = capsys.readouterr().out
    assert "Added" in out, out

    now = list(w.input.candidates)
    now_uids = [buda.topo_uid(c) for c in now]
    # Append-only: the original list is a strict prefix, so indices (and the
    # pin) are untouched.
    assert now_uids[:len(base_uids)] == base_uids
    assert len(now) > len(base), "multi_trunk added nothing on a 6-block fan-out"
    assert w.input.topology_pinned
    assert w.plan.selected_topology_index == pin_idx
    # No duplicates entered the pool.
    assert len(set(now_uids)) == len(now_uids)

    # Idempotence: the same knobs again add zero (all uid-duplicates).
    s.do_command("generate_more_topologies d multi_trunk")
    out = capsys.readouterr().out
    assert "Added 0 new" in out, out
    assert [buda.topo_uid(c) for c in w.input.candidates] == now_uids


def test_additive_unknown_hint_warns(capsys):
    s = _session()
    s.do_command("generate_more_topologies zzz")
    assert "Could not find bundle matching hint" in capsys.readouterr().out
