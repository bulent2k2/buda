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

"""TopoEdit session commands (expert hand-editing; Phase E3b).

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda


def cmd_edit_topology(session, cmd, args, cmd_line):
    # Usage: edit_topology <bundle_id> [<cand#>|new]
    # Open a TopoEdit session (Phase E3b): a working COPY of the given
    # candidate (1-based; default = the selected candidate, else 'new')
    # — or an empty topology with 'new'.  Subsequent edit_* commands
    # mutate the copy transactionally (each prints its verdict);
    # edit_commit appends it to the bundle's pool; edit_abort discards.
    if session._edit_topo is not None:
        print("Error: an edit session is already open — edit_commit or edit_abort first")
        return
    if not args:
        print("Error: edit_topology requires a bundle id")
        return
    bid = int(args[0])
    w = next((x for x in session.bundles
              if x.input.original_bundle.id == bid), None)
    if w is None:
        print(f"Error: no bundle with id {bid}")
        return
    spec = args[1] if len(args) > 1 else None
    if spec is None:
        sel = w.plan.selected_topology_index
        spec = str(sel + 1) if 0 <= sel < len(w.input.candidates) else "new"
    if spec == "new":
        topo = buda.Topology()
        topo.type = "USER"
        src_desc = "empty topology"
    else:
        ci = int(spec) - 1
        if not (0 <= ci < len(w.input.candidates)):
            print(f"Error: candidate {spec} out of range (bundle has "
                  f"{len(w.input.candidates)})")
            return
        # candidates[] elements ALIAS the pool storage (pybind
        # reference_internal — ripup's in-place flips rely on it), so
        # the session must take an explicit deep copy: offset by (0,0)
        # clones geometry + annotations + bridges.
        topo = buda.offset_topology(w.input.candidates[ci], 0, 0)
        src_desc = f"copy of candidate {ci + 1} ({topo.type})"
    session._edit_w, session._edit_topo, session._edit_src = w, topo, src_desc
    print(f"[edit] session opened on bundle {bid}: {src_desc} "
          f"({len(topo.segments)} segment(s)). "
          f"edit_status shows the verdict; edit_commit / edit_abort ends.")


def cmd_edit_add_trunk(session, cmd, args, cmd_line):
    # Usage: edit_add_trunk <H|V> <perp_pos> [<along_lo> <along_hi>] [layer <id>]
    # Pick axis + a Hanan line; default span = the full Hanan extent.
    if session._edit_session() is None: return
    pos = list(args)
    layer = None
    if "layer" in pos:
        li = pos.index("layer")
        layer = int(pos[li + 1]); del pos[li:li + 2]
    if len(pos) < 2 or pos[0].upper() not in ("H", "V"):
        print("Error: edit_add_trunk <H|V> <perp_pos> [<lo> <hi>] [layer <id>]")
        return
    horiz = pos[0].upper() == "H"
    perp = int(pos[1])
    lo, hi = (int(pos[2]), int(pos[3])) if len(pos) >= 4 else (1, 0)
    h_def, v_def = session._edit_layers()
    v = buda.edit_add_trunk(session._edit_topo, session.fp, horiz, perp,
                            lo, hi, layer if layer is not None
                            else (h_def if horiz else v_def))
    session._edit_report(v)


def cmd_edit_add_stub(session, cmd, args, cmd_line):
    # Usage: edit_add_stub <block> <seg#> [layer <id>]  (seg# 0-based,
    # as printed by edit_status / dump_topologies --conn)
    if session._edit_session() is None: return
    pos = list(args)
    layer = None
    if "layer" in pos:
        li = pos.index("layer")
        layer = int(pos[li + 1]); del pos[li:li + 2]
    if len(pos) < 2:
        print("Error: edit_add_stub <block> <seg#> [layer <id>]")
        return
    to_seg = int(pos[1])
    if layer is None:
        h_def, v_def = session._edit_layers()
        # The stub is perpendicular to its target.
        tgt_h = (0 <= to_seg < len(session._edit_topo.segments)
                 and session._edit_topo.segments[to_seg].start.y
                     == session._edit_topo.segments[to_seg].end.y)
        layer = v_def if tgt_h else h_def
    v = buda.edit_add_stub(session._edit_topo, session.fp, pos[0], to_seg, layer)
    session._edit_report(v)


def cmd_edit_remove_segment(session, cmd, args, cmd_line):
    # Usage: edit_remove_segment <seg#>
    if session._edit_session() is None: return
    v = buda.edit_remove_segment(session._edit_topo, session.fp, int(args[0]))
    session._edit_report(v)


def cmd_edit_set_span(session, cmd, args, cmd_line):
    # Usage: edit_set_span <seg#> <along_lo> <along_hi>
    if session._edit_session() is None: return
    v = buda.edit_set_span(session._edit_topo, session.fp,
                           int(args[0]), int(args[1]), int(args[2]))
    session._edit_report(v)


def cmd_edit_set_layer(session, cmd, args, cmd_line):
    # Usage: edit_set_layer <seg#> <layer_id>
    # The scriptable equivalent of the explorer's +/- layer cycle in an edit
    # session: set the working copy's segment layer hint directly.  The
    # planner honors it via the commit's pinned overrides (GUI) or the
    # candidate's layer_hint (CLI).
    if session._edit_session() is None: return
    if len(args) < 2:
        print("Error: edit_set_layer <seg#> <layer_id>")
        return
    i, lid = int(args[0]), int(args[1])
    topo = session._edit_topo
    if not (0 <= i < len(topo.segments)):
        print(f"Error: segment {i} out of range "
              f"(topology has {len(topo.segments)})")
        return
    seg = topo.segments[i]
    horiz = seg.start.y == seg.end.y
    if session.layers is not None and session.layers.has_layer(lid):
        dir_h = (session.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL)
        if dir_h != horiz:
            print(f"  Warning: seg {i} runs {'H' if horiz else 'V'} but layer "
                  f"{lid} routes {'H' if dir_h else 'V'} — check_design will "
                  f"flag LAYER_DIR")
    seg.layer_hint = lid
    print(f"[edit] seg {i} layer -> {lid}")


def cmd_edit_connect(session, cmd, args, cmd_line):
    # Usage: edit_connect <seg_i> <seg_j>   (perpendicular pair)
    if session._edit_session() is None: return
    v = buda.edit_connect(session._edit_topo, session.fp,
                          int(args[0]), int(args[1]))
    session._edit_report(v)


def cmd_edit_disconnect(session, cmd, args, cmd_line):
    # Usage: edit_disconnect <seg_i> <seg_j> <retract_to>
    if session._edit_session() is None: return
    v = buda.edit_disconnect(session._edit_topo, session.fp,
                             int(args[0]), int(args[1]), int(args[2]))
    session._edit_report(v)


def cmd_edit_status(session, cmd, args, cmd_line):
    # Print the working topology's segments and current verdict.
    if session._edit_session() is None: return
    topo = session._edit_topo
    print(f"[edit] bundle {session._edit_w.input.original_bundle.id}: "
          f"{session._edit_src}, {len(topo.segments)} segment(s), "
          f"blocks={','.join(topo.connected_block_names) or '-'}")
    for i, sg in enumerate(topo.segments):
        d = "H" if sg.start.y == sg.end.y else "V"
        print(f"  seg {i} {d} ({sg.start.x},{sg.start.y})-"
              f"({sg.end.x},{sg.end.y}) L{sg.layer_hint}")
    if topo.segments:
        session._edit_report(buda.edit_verdict(topo, session.fp))


def cmd_edit_abort(session, cmd, args, cmd_line):
    if session._edit_session() is None: return
    print(f"[edit] session on bundle "
          f"{session._edit_w.input.original_bundle.id} discarded.")
    session._edit_w = session._edit_topo = None
    session._edit_src = ""


def cmd_edit_commit(session, cmd, args, cmd_line):
    # Usage: edit_commit [pin]
    # Append the working topology to the bundle's candidate pool
    # (uid-deduped, like generate_more_topologies) and close the
    # session; 'pin' also selects it.  A not-ok verdict is a WARNING,
    # not a rejection: the user candidate stays visible to
    # check_design, exactly like generation's never-strand rule.
    if session._edit_session() is None: return
    w, topo = session._edit_w, session._edit_topo
    if not topo.segments:
        print("Error: nothing to commit (no segments) — edit_abort to discard")
        return
    topo.type = "USER"
    topo.estimated_wirelength = (
        sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
            for s in topo.segments)
        + sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
              for s in topo.bridge_segments.values()))
    v = buda.edit_verdict(topo, session.fp)
    if not v.ok():
        session._edit_report(v)
        print("  Warning: committing a not-clean topology — "
              "check_design will report it.")
    uid = buda.topo_uid(topo)
    pool = list(w.input.candidates)
    existing = next((i for i, c in enumerate(pool)
                     if buda.topo_uid(c) == uid), None)
    if existing is not None:
        idx = existing
        print(f"[edit] identical candidate already in the pool at "
              f"index {idx + 1} — nothing appended.")
    else:
        pool.append(topo)
        idx = len(pool) - 1
        w.input.candidates = pool
        print(f"[edit] committed as candidate {idx + 1} of bundle "
              f"{w.input.original_bundle.id} (type USER, WL="
              f"{topo.estimated_wirelength}, uid {uid}).")
    if "pin" in args:
        w.plan.selected_topology_index = idx
        w.input.topology_pinned = True
        print(f"  Pinned bundle {w.input.original_bundle.id} to it.")
    session._edit_w = session._edit_topo = None
    session._edit_src = ""
    if session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


COMMANDS = {
    "edit_topology": cmd_edit_topology,
    "edit_add_trunk": cmd_edit_add_trunk,
    "edit_add_stub": cmd_edit_add_stub,
    "edit_remove_segment": cmd_edit_remove_segment,
    "edit_set_span": cmd_edit_set_span,
    "edit_set_layer": cmd_edit_set_layer,
    "edit_connect": cmd_edit_connect,
    "edit_disconnect": cmd_edit_disconnect,
    "edit_status": cmd_edit_status,
    "edit_abort": cmd_edit_abort,
    "edit_commit": cmd_edit_commit,
}
