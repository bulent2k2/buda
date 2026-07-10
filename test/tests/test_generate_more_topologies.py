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
re-attached by uid), the additive command MERGES knob-produced candidates into
the existing pool, deduplicated by stable content uid, then re-sorts the pool by
the same key as generation (wirelength, then type).  Raw indices may move, but
the SELECTION is preserved: the pin (and dogleg slot) are remapped to follow
their candidate across the re-sort.
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


def test_additive_merges_dedupes_resorts_and_preserves_pin(capsys):
    s = _session()
    w = s.bundles[0]
    base = list(w.input.candidates)
    assert base, "base generation produced no candidates"
    base_uids = set(buda.topo_uid(c) for c in base)

    pin_idx = min(1, len(base) - 1)
    bid = w.input.original_bundle.id
    pinned_uid = buda.topo_uid(base[pin_idx])
    _quiet(s, f"select_topology {bid} {pin_idx + 1}")

    s.do_command("generate_more_topologies d multi_trunk")
    out = capsys.readouterr().out
    assert "Added" in out, out

    now = list(w.input.candidates)
    now_uids = [buda.topo_uid(c) for c in now]
    # Merged (not appended): every base candidate survives, plus new ones, no dups.
    assert base_uids <= set(now_uids)
    assert len(now) > len(base), "multi_trunk added nothing on a 6-block fan-out"
    assert len(set(now_uids)) == len(now_uids)
    # Pool is re-sorted by generation's key (wirelength asc, then type).
    keys = [(c.estimated_wirelength, c.type) for c in now]
    assert keys == sorted(keys), keys
    # The SELECTION is preserved: the pin still points at the SAME candidate, at
    # its post-resort index (which may differ from the original pin_idx).
    assert w.input.topology_pinned
    assert buda.topo_uid(now[w.plan.selected_topology_index]) == pinned_uid

    # Idempotence: the same knobs again add zero and leave the (already-sorted)
    # pool and the selection unchanged.
    s.do_command("generate_more_topologies d multi_trunk")
    out = capsys.readouterr().out
    assert "Added 0 new" in out, out
    assert [buda.topo_uid(c) for c in w.input.candidates] == now_uids
    assert buda.topo_uid(w.input.candidates[w.plan.selected_topology_index]) == pinned_uid


def test_additive_unknown_hint_warns(capsys):
    s = _session()
    s.do_command("generate_more_topologies zzz")
    assert "Could not find bundle matching hint" in capsys.readouterr().out
