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
import re

import buda

# Coordinate arguments of edit_* commands accept BLOCK/FACE REFERENCES in
# place of absolute numbers: `<block>.<ref>[+N|-N]` with ref one of
# left/right (x faces), bottom/top (y faces), cx/cy (centres) — resolved
# against the edit session's own floorplan (cell-local for a hier template),
# so a folded command tracks the floorplan instead of hard-coding numbers.
# The GUI's [edit-cmd] log emits the same form for block-pinned endpoints.
# The block name is parsed GREEDILY from the face suffix (the `.ref` is
# anchored at the end), because block names themselves may contain dots
# (`IO.PAD`, `u_cpu0.c0l3c0` in the bundled demos) — `IO.PAD.right-20`
# resolves block `IO.PAD`, face `right`, offset -20.
_COORD_REF = re.compile(
    r"^(.+)\.(left|right|top|bottom|cx|cy)([+-]\d+)?$")


def _coord(session, tok):
    """Resolve an edit_* coordinate argument: an integer, or a block/face
    reference like `up.bottom+20` / `lo.cx` (see _COORD_REF)."""
    m = _COORD_REF.match(tok)
    if m is None:
        return int(tok)
    name, ref, off = m.group(1), m.group(2), int(m.group(3) or 0)
    fp = session._edit_fp if session._edit_fp is not None else session.fp
    # get_block_bounds returns a zero rect for unknown names — check
    # membership explicitly so a typo fails loud, not as a (0,0) landing.
    if name not in {n for n, _ in fp.get_all_blocks()}:
        raise ValueError(f"unknown block '{name}' in coordinate '{tok}'")
    r = fp.get_block_bounds(name)
    v = {"left": r.x1, "right": r.x2, "bottom": r.y1, "top": r.y2,
         "cx": (r.x1 + r.x2) // 2, "cy": (r.y1 + r.y2) // 2}[ref]
    return v + off


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
    session._edit_slide = {}
    session._edit_layers_changed = False
    # Resolve the session's FLOORPLAN: a hier bundle's candidates live in the
    # frame they were generated in — CELL-LOCAL coordinates and block names
    # (pa_i, not proc_i1/pa_i) for a cell-level template, a custom endpoint
    # floorplan for a cross-level bundle — so every edit op's verdict, stub
    # tap, and slide range must use that floorplan, not the flat session one.
    # Same resolver as check_design / dump_topologies (flat bundles, expanded
    # per-instance wrappers, and same-level hier bundles keep session.fp).
    session._edit_fp = session._make_topo_fp_resolver()(w)
    if session._edit_fp is not session.fp:
        hb = w.input.original_bundle
        print(f"[edit] hier bundle: edits use the bundle's own floorplan "
              f"({'cell-local: ' + hb.cell_context if hb.cell_context else 'endpoint frame'}"
              f", {len(session._edit_fp.get_all_blocks())} block(s))")
    print(f"[edit] session opened on bundle {bid}: {src_desc} "
          f"({len(topo.segments)} segment(s)). "
          f"edit_status shows the verdict; edit_commit / edit_abort ends.")


def cmd_edit_add_trunk(session, cmd, args, cmd_line):
    # Usage: edit_add_trunk <H|V> <perp_pos> [<along_lo> <along_hi>] [layer <id>]
    # Pick axis + a Hanan line; default span = the full Hanan extent.
    # Coordinates accept block/face references (up.bottom+20, lo.cx).
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
    perp = _coord(session, pos[1])
    lo, hi = ((_coord(session, pos[2]), _coord(session, pos[3]))
              if len(pos) >= 4 else (1, 0))
    h_def, v_def = session._edit_layers()
    v = buda.edit_add_trunk(session._edit_topo, session._edit_fp, horiz, perp,
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
    v = buda.edit_add_stub(session._edit_topo, session._edit_fp, pos[0], to_seg, layer)
    session._edit_report(v)


def cmd_edit_remove_segment(session, cmd, args, cmd_line):
    # Usage: edit_remove_segment <seg#>
    if session._edit_session() is None: return
    si = int(args[0])
    v = buda.edit_remove_segment(session._edit_topo, session._edit_fp, si)
    session._edit_report(v)
    if v.applied:
        # Indices above si shifted down: remap the staged slide windows,
        # dropping the removed segment's own (mirrors the explorer's X).
        session._edit_slide = {
            (i - 1 if i > si else i): win
            for i, win in session._edit_slide.items() if i != si}


def cmd_edit_set_span(session, cmd, args, cmd_line):
    # Usage: edit_set_span <seg#> <along_lo> <along_hi>
    # Coordinates accept block/face references (up.bottom+20, lo.cx).
    if session._edit_session() is None: return
    v = buda.edit_set_span(session._edit_topo, session._edit_fp,
                           int(args[0]), _coord(session, args[1]),
                           _coord(session, args[2]))
    session._edit_report(v)


def cmd_edit_set_slide(session, cmd, args, cmd_line):
    # Usage: edit_set_slide <seg#> <lo> <hi>   |   edit_set_slide <seg#> clear
    # The scriptable equivalent of the explorer's 'W' (two-press slide-window
    # refine) / 'w' (clear): stage a per-segment perpendicular slide window,
    # intersected with the segment's STRUCTURAL slide range (a window outside
    # it would make the NUTS placement infeasible).  Staged windows land on
    # plan.seg_slide_lo/hi at edit_commit — revalidated there again, since a
    # later geometry edit can narrow the range.  Indices follow
    # edit_remove_segment re-keying, exactly like the GUI staging.
    if session._edit_session() is None: return
    if len(args) < 2:
        print("Error: edit_set_slide <seg#> <lo> <hi>  |  edit_set_slide <seg#> clear")
        return
    si = int(args[0])
    topo = session._edit_topo
    if not (0 <= si < len(topo.segments)):
        print(f"Error: segment {si} out of range "
              f"(topology has {len(topo.segments)})")
        return
    if args[1].lower() == "clear":
        if session._edit_slide.pop(si, None) is not None:
            print(f"[edit] seg {si} staged slide window cleared")
        else:
            print(f"[edit] seg {si} has no staged slide window")
        return
    if len(args) < 3:
        print("Error: edit_set_slide <seg#> <lo> <hi>  |  edit_set_slide <seg#> clear")
        return
    lo, hi = sorted((float(_coord(session, args[1])),
                     float(_coord(session, args[2]))))
    ct = buda.ConnTopology()
    ct.build(topo, session._edit_fp)
    cs = list(ct.segs())[si]
    s_lo, s_hi = float(cs.perp_lo), float(cs.perp_hi)
    clo, chi = max(lo, s_lo), min(hi, s_hi)
    if clo > chi:
        print(f"Error: window [{lo:.0f},{hi:.0f}] is outside seg {si}'s "
              f"slide range [{s_lo:.0f},{s_hi:.0f}]")
        return
    session._edit_slide[si] = (clo, chi)
    note = "" if (clo, chi) == (lo, hi) else " (clamped to slide range)"
    print(f"[edit] seg {si} slide window [{clo:.0f},{chi:.0f}]{note} — "
          f"applies at edit_commit")


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
    session._edit_layers_changed = True   # commit rebuilds pinned_seg_layers
    print(f"[edit] seg {i} layer -> {lid}")


def cmd_edit_connect(session, cmd, args, cmd_line):
    # Usage: edit_connect <seg_i> <seg_j>   (perpendicular pair)
    if session._edit_session() is None: return
    v = buda.edit_connect(session._edit_topo, session._edit_fp,
                          int(args[0]), int(args[1]))
    session._edit_report(v)


def cmd_edit_disconnect(session, cmd, args, cmd_line):
    # Usage: edit_disconnect <seg_i> <seg_j> <retract_to>
    # retract_to accepts a block/face reference (up.bottom+20, lo.cx).
    if session._edit_session() is None: return
    v = buda.edit_disconnect(session._edit_topo, session._edit_fp,
                             int(args[0]), int(args[1]),
                             _coord(session, args[2]))
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
        session._edit_report(buda.edit_verdict(topo, session._edit_fp))


def cmd_edit_abort(session, cmd, args, cmd_line):
    if session._edit_session() is None: return
    print(f"[edit] session on bundle "
          f"{session._edit_w.input.original_bundle.id} discarded.")
    session._edit_w = session._edit_topo = None
    session._edit_src = ""
    session._edit_slide = {}
    session._edit_layers_changed = False


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
    v = buda.edit_verdict(topo, session._edit_fp)
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
    # Per-segment overrides below are indexed by the COMMITTED topology's
    # segments and live on plan/input state that NUTS/planner consult for the
    # SELECTED candidate — writing them when the commit did not (re)select the
    # candidate would misapply them to whatever is selected (the length guard
    # can coincide).  So both blocks gate on the committed candidate being the
    # selection (a 'pin' commit, or a dedup onto the already-selected one).
    is_selected = (w.plan.selected_topology_index == idx)
    # Rebuild the pinned layer overrides when the session re-layered a segment
    # (edit_set_layer): layer_hint alone is only a suggestion to the planner —
    # wrapper.input.pinned_seg_layers is what forces the layers (GUI parity;
    # the explorer commit does the same).  No layer decision -> clear a stale
    # list rather than let it re-pin old layers onto the new topology.
    if is_selected:
        if session._edit_layers_changed:
            w.input.pinned_seg_layers = [s.layer_hint for s in topo.segments]
            print(f"  Pinned {len(topo.segments)} segment layer(s) from the "
                  f"session's edit_set_layer edits.")
        else:
            w.input.pinned_seg_layers = []
    elif session._edit_layers_changed:
        print("  Warning: edit_set_layer edits NOT pinned — the commit did "
              "not select the candidate (use 'edit_commit pin'); the planner "
              "treats bare layer_hints as suggestions only.")
    session._edit_layers_changed = False
    # Land the session's staged slide windows (edit_set_slide) as NUTS
    # overrides on the committed candidate — the same plan.seg_slide_lo/hi
    # hatch the explorer's 'W' rides.  REVALIDATED against the committed
    # topology's connectivity (a geometry edit after staging can narrow a
    # segment's structural range; NUTS honors any non-NaN override verbatim):
    # a shrunken window clamps, a now-disjoint one is dropped LOUD.
    if session._edit_slide and not is_selected:
        print(f"  Warning: {len(session._edit_slide)} staged slide window(s) "
              f"discarded — the commit did not select the candidate (use "
              f"'edit_commit pin'); plan overrides attach to the selection.")
        session._edit_slide = {}
    if session._edit_slide:
        ct = buda.ConnTopology()
        ct.build(topo, session._edit_fp)
        cs_list = list(ct.segs())
        nseg = len(topo.segments)
        slo = [float('nan')] * nseg
        shi = [float('nan')] * nseg
        applied, dropped = 0, []
        for si, (lo, hi) in sorted(session._edit_slide.items()):
            if not (0 <= si < nseg):
                dropped.append(si)
                continue
            s_lo, s_hi = float(cs_list[si].perp_lo), float(cs_list[si].perp_hi)
            clo, chi = max(float(lo), s_lo), min(float(hi), s_hi)
            if clo > chi:
                dropped.append(si)
                continue
            slo[si], shi[si] = clo, chi
            applied += 1
        w.plan.seg_slide_lo = slo
        w.plan.seg_slide_hi = shi
        if applied:
            print(f"  Applied {applied} slide window(s) to the plan "
                  f"(run_nuts honors them).")
        if dropped:
            print(f"  Warning: dropped stale slide window(s) on seg {dropped} "
                  f"— outside the segment's current slide range.")
        session._edit_slide = {}
    session._edit_w = session._edit_topo = None
    session._edit_src = ""
    if getattr(session, "_hier_expansion_map", None):
        # POST-expansion hier session: self.bundles are the per-instance
        # wrappers.  The pre-expansion _persist_topologies would rewrite them
        # as NORMAL bundle rows and clobber the planner's expanded checkpoint
        # (is_expanded rows + template linkage — a later `load_pipeline
        # expanded` would find nothing and trip the missing-block gate on the
        # mixed frames).  Persist through the planner's expanded path instead:
        # each instance's SELECTED topology is recorded, so a pinning commit
        # lands as the instance's routed shape.
        if session.bdb is not None:
            expanded_ids = {x.input.original_bundle.id
                            for ws in session._hier_expansion_map.values()
                            for x in ws}
            if w.input.original_bundle.id not in expanded_ids:
                # A NORMAL bundle (cross-block / global) edited post-expansion:
                # the planner persist below only updates its selection/layers —
                # refresh its candidate rows first so the just-committed USER
                # topology actually exists for is_selected to point at (else
                # the pin appears to save but load_pipeline reloads the old
                # pool — Codex #306).
                session._persist_bundle_candidates(w)
            session._persist_planner_output()
            print("[BDB] re-persisted expanded planner state to the open BDB.")
            if not is_selected:
                print("  Note: expanded instances persist only their SELECTED "
                      "topology — the un-pinned USER candidate is session-only.")
    elif session._persist_topologies():
        print("[BDB] re-persisted candidate topologies to the open BDB.")


COMMANDS = {
    "edit_topology": cmd_edit_topology,
    "edit_add_trunk": cmd_edit_add_trunk,
    "edit_add_stub": cmd_edit_add_stub,
    "edit_remove_segment": cmd_edit_remove_segment,
    "edit_set_span": cmd_edit_set_span,
    "edit_set_slide": cmd_edit_set_slide,
    "edit_set_layer": cmd_edit_set_layer,
    "edit_connect": cmd_edit_connect,
    "edit_disconnect": cmd_edit_disconnect,
    "edit_status": cmd_edit_status,
    "edit_abort": cmd_edit_abort,
    "edit_commit": cmd_edit_commit,
}
