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


def _is_wl_sorted(pool):
    keys = [(c.estimated_wirelength, c.type) for c in pool]
    return keys == sorted(keys)


def test_generate_more_remaps_dogleg_by_integer_key():
    """Codex #243 P2: the dogleg slot/original are keyed by the INTEGER
    original_bundle.id (as _adopt_doglegs/_reset_doglegs use it).  The re-sort must
    remap those integer-keyed entries — a str() key would miss a live dogleg and
    leave its bookkeeping pointing at pre-sort indices."""
    s = _session()
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    assert isinstance(bid, int)
    base = list(w.input.candidates)
    assert len(base) >= 2
    # Simulate an adopted dogleg recorded under the integer id.
    slot_idx, orig_idx = len(base) - 1, 0
    slot_uid, orig_uid = buda.topo_uid(base[slot_idx]), buda.topo_uid(base[orig_idx])
    s._dogleg_slot[bid] = slot_idx
    s._dogleg_originals[bid] = orig_idx

    s.do_command("generate_more_topologies d multi_trunk")
    now = list(w.input.candidates)
    assert _is_wl_sorted(now)
    # Integer-keyed entries survive and still point at the SAME candidates; no
    # stray str() key was created.
    assert bid in s._dogleg_slot and str(bid) not in s._dogleg_slot
    assert buda.topo_uid(now[s._dogleg_slot[bid]]) == slot_uid
    assert buda.topo_uid(now[s._dogleg_originals[bid]]) == orig_uid


def test_knob_memo_replay_resorts_pool(tmp_path):
    """Codex #243 P2: a persisted knob memo replayed by a later bulk
    generate_topologies (_apply_gen_knobs) must re-sort the merged pool, else a
    resumed bundle reverts to 'base candidates + unsorted knob tail'."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [f"open_bdb {tmp_path / 'k.bdb'}",
            "def_layer 4 M4 H TOP 20", "def_layer 5 M5 V TOP 20"]
    for i in range(6):
        x, y = (i % 3) * 400, (i // 3) * 600
        cmds.append(f"add_block b{i} {x} {y} {x + 200} {y + 300}")
    cmds += ["add_bus d[8] b0.p b1.q,b2.r,b3.s,b4.t,b5.u",
             "run_bundler", "generate_topologies",
             "generate_more_topologies d multi_trunk"]
    _quiet(s, *cmds)
    w = s.bundles[0]
    assert _is_wl_sorted(w.input.candidates)
    n_after_more = len(w.input.candidates)

    # Bulk regenerate: _apply_gen_knobs replays the memo — must re-sort.
    _quiet(s, "generate_topologies")
    w = s.bundles[0]
    assert len(w.input.candidates) == n_after_more, "knob memo not replayed"
    assert _is_wl_sorted(w.input.candidates), \
        "regenerated pool reverted to unsorted knob tail (Codex #243 P2)"


def test_additive_unknown_hint_warns(capsys):
    s = _session()
    s.do_command("generate_more_topologies zzz")
    assert "Could not find bundle matching hint" in capsys.readouterr().out


# ── Hier-aware accretion (opens.md: generate_more_topologies + HBundles) ──────

def _hier_session():
    """The 6-block fan-out INSIDE a cell, instantiated twice — the hier twin
    of _session(): the cell-local template gets new BITRUNK shapes from
    multi_trunk exactly as the flat fixture does."""
    db = buda.BDB(":memory:")
    db.add_cell("big_cell", 1400, 1600)
    db.add_cell("leaf_cell", 200, 300)
    for i in range(6):
        x, y = (i % 3) * 400 + 20, (i // 3) * 600 + 20
        db.add_inst_to_cell("big_cell", f"b{i}", "leaf_cell", x, y)
    db.add_inst("B1", "big_cell", "", 0, 0)
    db.add_inst("B2", "big_cell", "", 1600, 0)
    for i in range(8):
        for k in (1, 2):
            db.add_net_pins(f"d{k}_{i}", f"B{k}/b0.p",
                            [f"B{k}/b1.q", f"B{k}/b2.r", f"B{k}/b3.s",
                             f"B{k}/b4.t", f"B{k}/b5.u"])
    buda.BustermGen(db).derive(1)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    _quiet(s, "def_layer 4 M4 H TOP 20", "def_layer 5 M5 V TOP 20",
           "run_hier_bundler", "generate_hier_topologies")
    return s


def _template_of(s, prefix):
    """The (template, replica-or-None) wrappers whose first net starts with
    prefix / whose parent links to the former."""
    tw = next(w for w in s.bundles
              if w.input.original_bundle.get_net_names()[0].startswith(prefix))
    rep = next((w for w in s.bundles
                if w.input.original_bundle.parent_id
                == tw.input.original_bundle.id), None)
    return tw, rep


def test_hier_additive_on_template_by_net_hint(capsys):
    s = _hier_session()
    tw, _ = _template_of(s, "d1")
    base = list(tw.input.candidates)
    assert base, "hier base generation produced no candidates"
    base_uids = {buda.topo_uid(c) for c in base}
    pin_idx = min(1, len(base) - 1)
    pinned_uid = buda.topo_uid(base[pin_idx])
    tw.input.topology_pinned = True
    tw.plan.selected_topology_index = pin_idx

    s.do_command("generate_more_topologies d1 multi_trunk")
    out = capsys.readouterr().out
    assert "new candidate(s)" in out, out
    now = list(tw.input.candidates)
    now_uids = [buda.topo_uid(c) for c in now]
    assert base_uids <= set(now_uids)          # merged, not replaced
    assert len(now) > len(base), "multi_trunk added nothing on the fan-out"
    assert len(set(now_uids)) == len(now_uids)  # deduped
    assert _is_wl_sorted(now)
    assert tw.input.topology_pinned            # pin follows its candidate
    assert buda.topo_uid(now[tw.plan.selected_topology_index]) == pinned_uid

    # Idempotence: same knobs add zero.
    s.do_command("generate_more_topologies d1 multi_trunk")
    out = capsys.readouterr().out
    assert "+0 new candidate(s)" in out, out
    assert [buda.topo_uid(c) for c in tw.input.candidates] == now_uids


def test_hier_additive_by_bundle_id(capsys):
    s = _hier_session()
    tw, _ = _template_of(s, "d1")
    base_n = len(tw.input.candidates)
    bid = tw.input.original_bundle.id
    s.do_command(f"generate_more_topologies {bid} multi_trunk")
    out = capsys.readouterr().out
    assert "new candidate(s)" in out, out
    assert len(tw.input.candidates) > base_n


def test_hier_replica_hint_redirects_to_template(capsys):
    """A hint matching a REPLICA's nets accretes on its template — the
    bundle that actually carries the routing for every instance."""
    s = _hier_session()
    tw, rep = _template_of(s, "d1")
    assert rep is not None, "fixture must produce a replica"
    base_n = len(tw.input.candidates)
    rep_pool = list(rep.input.candidates)
    s.do_command("generate_more_topologies d2 multi_trunk")
    out = capsys.readouterr().out
    assert "is a replica — accreting on its template" in out, out
    assert len(tw.input.candidates) > base_n
    # The replica's own pool is untouched (expansion never reads it).
    assert len(rep.input.candidates) == len(rep_pool)


def test_hier_knob_memo_replays_on_bulk_regen(capsys):
    """The v15 per-bundle knob memo works for HBundles too: a bulk
    generate_hier_topologies re-applies the accretion additively, so the
    pool does not silently revert."""
    s = _hier_session()
    tw, _ = _template_of(s, "d1")
    _quiet(s, "generate_more_topologies d1 multi_trunk")
    accreted = {buda.topo_uid(c) for c in tw.input.candidates}
    s.do_command("generate_hier_topologies")
    out = capsys.readouterr().out
    assert "Re-applied knob memo 'multi_trunk'" in out, out
    tw2, _ = _template_of(s, "d1")
    assert accreted <= {buda.topo_uid(c) for c in tw2.input.candidates}


def test_hier_additive_refused_post_expansion(capsys):
    """After run_planner hier the pools live on per-instance expanded
    wrappers — accretion there would merge cell-local candidates into
    absolute-coordinate pools, so the command refuses with the recipe."""
    s = _hier_session()
    _quiet(s, "run_planner hier")
    s.do_command("generate_more_topologies d1 multi_trunk")
    out = capsys.readouterr().out
    assert "Error" in out and "run_planner hier" in out, out


def test_hier_unknown_hint_warns(capsys):
    s = _hier_session()
    s.do_command("generate_more_topologies zz multi_trunk")
    out = capsys.readouterr().out
    assert "Could not find bundle matching hint" in out, out
