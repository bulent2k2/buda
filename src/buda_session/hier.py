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

"""Hierarchy-aware flow + topology-generation support.

HBundle expansion (cell-level template -> per-instance wrappers), sidecar
selection application, BDB -> Floorplan projection (add_blocks_from_bdb,
container marking, per-cell floorplans), per-HBundle topology generation,
and the shared TopologyGenerator/layer-name/endpoint-validation helpers
used by both the flat and hier generate commands.

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import contextlib
import io
import json
import math
import os
import re
import sys

import buda

import buda_diag
from bus_names import parse_bus_bit
# WHETHER a component has a placement — one definition for every frame this
# module builds, because generation validates in its frame and load_pipeline
# validates in self.fp, so two answers is a resume failure (comp_placement.py).
from comp_placement import is_placed

# ── Orientation helpers (module-level: shared pure functions) ────────────────
# Output-normalized orientation maps over a w×h box: (swap, reflect-x,
# reflect-y).  Token convention = mirror-about-X-axis FIRST, then CCW
# rotation (bdb.cpp / gds_io.cpp).
_ORIENT_MAPS = {"N":  (0, 0, 0), "S":  (0, 1, 1),
                "FN": (0, 0, 1), "FS": (0, 1, 0),
                "W":  (1, 1, 0), "E":  (1, 0, 1),
                "FW": (1, 0, 0), "FE": (1, 1, 1)}
_DIR_PRESERVING = ("N", "S", "FN", "FS")   # H stays H, V stays V

_PHASE_SCALE = 1_000_000   # µ-unit integers for exact modular phase math


def _oxf_rect(o, x1, y1, x2, y2, w, h):
    """Transform a rect through orientation `o` over a w×h box
    (normalized: the transformed box's lower-left stays at the origin)."""
    s, rx, ry = _ORIENT_MAPS[o]

    def pt(x, y):
        if s:
            x, y, bw, bh = y, x, h, w
        else:
            bw, bh = w, h
        if rx:
            x = bw - x
        if ry:
            y = bh - y
        return x, y
    ax, ay = pt(x1, y1)
    bx, by = pt(x2, y2)
    return (round(min(ax, bx), 3), round(min(ay, by), 3),
            round(max(ax, bx), 3), round(max(ay, by), 3))


def _pattern_two_sigma(pat):
    """2σ mod pitch (µ-int) of the pattern's signal-track centers, σ = a
    reflection-symmetry center of the set (T = 2σ − T mod pitch); None
    when the layout has no such symmetry.  A mirrored instance can only
    ever share tracks with an unmirrored one about such a σ."""
    p = pat.unit_pitch()
    P = max(1, int(round(p * _PHASE_SCALE)))
    ts = sorted({int(round(pos * _PHASE_SCALE)) % P
                 for pos, slot in pat.tracks_in_range(pat.origin,
                                                      pat.origin + p)
                 if slot.type == "SIGNAL"})
    if not ts:
        return None
    for t in ts:   # a symmetry must pair the first track with SOME track
        cand = (ts[0] + t) % P
        if sorted((cand - u) % P for u in ts) == ts:
            return cand
    return None


def bottom_up_congruence_index(comps):
    """One-pass component index for congruence/orientation checks over MANY
    cells: ``(insts_by_cell, by_parent)``.

    Build it once per `all_components()` read and evaluate any number of
    cells against it (the Floorplanner Cell Settings dialog path, which
    would otherwise rebuild the full index per cell —
    O(#cells × #components)).  Single-cell callers just omit `index` and
    the functions below build it themselves.
    """
    insts_by_cell, by_parent = {}, {}
    for c in comps:
        insts_by_cell.setdefault(c.cell, []).append(c)
        by_parent.setdefault(c.parent_id, []).append(c)
    return insts_by_cell, by_parent


def _default_orient_score(o, c, ref):
    """Ambiguity tiebreak when no session/routing grid is available (the
    Floorplanner path): no phase discrimination, but keep dispreferring
    direction-swapping orients exactly as _orient_phase_score does."""
    return -1 if _ORIENT_MAPS[o][0] else 0


def detect_instance_orients(comps, cell, ref_name=None, cache=None,
                            phase_score=None, index=None):
    """{instance name: orient token or None} — the 8-orientation
    transform mapping the REFERENCE instance's subtree onto each placed
    instance of `cell` (None = no orientation matches: genuinely
    non-congruent).  Detection is GEOMETRIC over the full subtree —
    rotate_comp/flip_comp on a hierarchical block rewrite the children's
    absolute bboxes and keep the root token 'N', so the token alone is
    untrustworthy — comparing per descendant: (cell type, composed
    orient, bbox relative to the instance origin, transformed).  For a
    CHILDLESS cell the geometry is orientation-blind (empty subtree), so
    the root tokens decide: O = inst.orient ∘ ref.orient⁻¹.

    ref_name picks the reference (default: first placed instance by
    name); the reference maps to 'N'.  `cache` (a caller-owned dict)
    memoizes on (cell, ref) — deliberately PER-CALL-SITE, never stored
    on the session: placement can mutate between commands (move_comp /
    rotate_comp / align), which would silently stale a session cache.

    Module-level pure core shared by the session
    (`HierMixin._detect_instance_orients`, which injects its routing-grid
    `phase_score`) and the Floorplanner (default score).  `index` accepts a
    prebuilt `bottom_up_congruence_index(comps)` so a many-cell caller pays
    the component walk once."""
    if cache is None:
        cache = {}
    if phase_score is None:
        phase_score = _default_orient_score
    if index is None:
        index = bottom_up_congruence_index(comps)
    insts_by_cell, by_parent = index
    insts = sorted((c for c in insts_by_cell.get(cell, []) if is_placed(c)),
                   key=lambda c: c.name)
    if not insts:
        return {}
    ref = next((c for c in insts if c.name == ref_name), insts[0])
    key = (cell, ref.name)
    if key in cache:
        return cache[key]

    def dims(c):
        return (round(c.x2 - c.x1, 3), round(c.y2 - c.y1, 3))

    def shape(inst):
        out, stack, prefix = {}, [inst], inst.name + "/"
        while stack:
            node = stack.pop()
            for k in by_parent.get(node.id, []):
                rel = (k.name[len(prefix):]
                       if k.name.startswith(prefix) else k.name)
                out[rel] = (k.cell, k.orient,
                            (round(k.x1 - inst.x1, 3),
                             round(k.y1 - inst.y1, 3),
                             round(k.x2 - inst.x1, 3),
                             round(k.y2 - inst.y1, 3)))
                stack.append(k)
        return out

    w, h = dims(ref)
    ref_shape = shape(ref)
    # Pre-transform the reference shape under each candidate orient.
    # TWO token conventions exist for the descendants of a transformed
    # instance: the reference's RAW tokens (BDB's flip_comp/rotate_comp
    # rewrite descendant bboxes and leave their tokens untouched) or the
    # COMPOSED tokens o∘raw (GDS-imported hierarchies carry the SREF
    # orientation per row).  A rigid transform is uniformly one or the
    # other, so the convention is chosen ONCE per candidate — accepting
    # them per-descendant would let a MIXED subtree pass (e.g. a square
    # leaf child separately rotated in place, bbox unchanged: its
    # composed token would pass while its siblings pass raw, yet no
    # single transform maps the reference — Codex #249).
    ref_under = {}
    for o in _ORIENT_MAPS:
        ref_under[o] = {
            rel: (kcell, korient, buda.orient_compose(o, korient),
                  _oxf_rect(o, *bbox, w, h))
            for rel, (kcell, korient, bbox) in ref_shape.items()}
    out = {}
    for c in insts:
        if c is ref:
            out[c.name] = "N"
            continue
        found = None
        if not ref_shape:
            # Childless: geometry is orientation-blind; trust the root
            # tokens (rotate/flip DO compose them for leaves) when the
            # outline is consistent with the implied orient.
            cand = buda.orient_compose(
                c.orient, buda.orient_inverse(ref.orient))
            exp = (h, w) if _ORIENT_MAPS[cand][0] else (w, h)
            found = cand if dims(c) == exp else None
        else:
            cshape = shape(c)
            matches = []
            for o, rshape in ref_under.items():
                exp = (h, w) if _ORIENT_MAPS[o][0] else (w, h)
                if dims(c) != exp or cshape.keys() != rshape.keys():
                    continue
                ok_geom = ok_raw = ok_comp = True
                for rel, (kcell, kraw, kcomp, bbox) in rshape.items():
                    kc, ko, bb = cshape[rel]
                    if kc != kcell or bb != bbox:
                        ok_geom = False
                        break
                    ok_raw = ok_raw and ko == kraw
                    ok_comp = ok_comp and ko == kcomp
                if ok_geom and (ok_raw or ok_comp):
                    matches.append(o)
            if "N" in matches:
                # Identity matching = the instance is (as far as the
                # geometry can prove) a plain copy; never re-interpret
                # it as a mirror of a self-symmetric layout just to
                # improve track phase — its LEAF CONTENT is unmirrored.
                found = "N"
            elif len(matches) == 1:
                found = matches[0]
            elif matches:
                # AMBIGUOUS (self-symmetric child layout, e.g. S vs
                # FS): every match instantiates the template onto the
                # same child boxes, so any is geometrically valid —
                # prefer the one whose track phases work (most axes),
                # ties broken by canonical order.
                found = max(matches,
                            key=lambda o: phase_score(o, c, ref))
        out[c.name] = found
    cache[key] = out
    return out


def bottom_up_congruence_issues(comps, cell, ref_name=None, cache=None,
                                phase_score=None, index=None,
                                only=None, allow_90=True):
    """Verify every instance of `cell` is a SUPPORTED transform of the
    reference — the precondition for bottom-up template planning.  An
    instance matching NO orientation is genuinely non-congruent and
    always an issue.  With allow_90 (the default, used at set_bottom_up /
    Floorplanner-marking time) the 90° family (E/W/FE/FW) is FINE: the
    rotation-class split at run_planner hier gives it its own clone
    template, whose members are direction-preserving among themselves.
    allow_90=False is the POST-SPLIT re-check per template: every
    instance must then be a direction-preserving transform of the group
    reference (S = 180°, FN/FS = axis mirrors), or the placement changed
    under us.

    `only` restricts the report to the given instance names (a clone
    template's members — detection covers ALL instances of the cell).

    The ONE shared definition of bottom-up eligibility: the CLI
    (`set_bottom_up` / `run_planner hier`, via the session wrapper below)
    and the Floorplanner's cell settings both call it, so the two can
    never diverge on what "congruent" means.  The phase-score tiebreak
    only picks BETWEEN geometrically valid orientations (e.g. S vs FS on
    a self-symmetric layout), so its presence/absence never changes the
    eligibility verdict.

    Returns a list of human-readable issue strings (empty = OK)."""
    orients = detect_instance_orients(comps, cell, ref_name=ref_name,
                                      cache=cache, phase_score=phase_score,
                                      index=index)
    issues = []
    for name in sorted(orients):
        if only is not None and name not in only:
            continue
        o = orients[name]
        if o is None:
            issues.append(
                f"{name}: subtree matches the reference under NO "
                f"orientation (genuinely non-congruent)")
        elif not allow_90 and o not in _DIR_PRESERVING:
            issues.append(
                f"{name}: 90°-rotated ({o}) relative to the group "
                f"reference — the rotation-class split should have "
                f"put it in the clone template (placement changed "
                f"after the split?)")
    return issues


class HierMixin:

    def _add_expanded_bundle(self, w, sel, expanded_to_template,
                             seen_busterms=None):
        """Add one expanded per-instance bundle row + its selected topology."""
        import json
        hb = w.input.original_bundle
        bid = str(hb.id)
        row = buda.BundleRow()
        row.id = bid
        row.level = hb.level
        row.strategy = self._bundler_strategy
        row.reason = hb.reason
        row.num_terminals = hb.num_terminals
        row.cell_context = hb.cell_context
        row.instances = json.dumps(list(hb.instances))
        # parent_id links the instance back to its template bundle.
        tpl = expanded_to_template.get(hb.id)
        row.parent_id = str(tpl) if tpl is not None else ""
        row.is_replicated = True                   # marks an expanded instance
        # v18: planner-expanded instance row (vs. a bundler replica, which is
        # shape-identical) — load_pipeline's expanded view keys off this.
        row.is_expanded = True
        # Bottom-up copy provenance (v18): load_pipeline restores this row as
        # a locked (pinned, never-moved) wrapper.
        row.bu_locked = bool(w.hier.locked)
        row.drv_spec_depth = hb.drv_spec_depth
        row.rcv_spec_depth = hb.rcv_spec_depth
        row.drv_spec_path = hb.drv_spec_path
        row.rcv_spec_paths = json.dumps(list(hb.rcv_spec_paths))
        self.bdb.add_bundle(row)
        for nm in hb.get_net_names():
            self.bdb.add_bundle_net(bid, nm)
        # entry/exit busterms may be cell-local (no "/"); qualify with the instance.
        inst = hb.instances[0] if hb.instances else ""
        def _qual(bt):
            return f"{inst}/{bt}" if inst and "/" not in bt else bt
        for bt in hb.entry_busterm_ids:
            self.bdb.add_bundle_busterm(bid, _qual(bt), "entry")
        for bt in hb.exit_busterm_ids:
            self.bdb.add_bundle_busterm(bid, _qual(bt), "exit")
        # Persist the SELECTED (placed) topology for the instance, with the
        # planner's assigned layers — plus any INSTANCE-LOCAL USER candidates
        # (TopoEdit follow-on #3): a hand-built candidate committed on THIS
        # instance WITHOUT a pin used to be session-only; now it persists as
        # an extra row, so both alternative shapes of an instance survive a
        # save → `load_pipeline expanded` resume.  Row growth stays bounded
        # by construction: only USER candidates committed ON the instance
        # ride — a template-level USER candidate replicated by expansion is
        # in the instance's inherited-uid set and stays template-owned
        # (Codex #358: persisting it per instance would multiply template
        # alternatives by instance count), and with no inherited record at
        # all (shouldn't happen — expansion and the expanded loader both
        # register) extras are conservatively skipped.  An un-edited
        # instance persists exactly one row as before.
        inherited = getattr(self, "_inherited_uids", {}).get(hb.id)
        seg_layers = list(w.plan.seg_layers)
        for ci, topo in enumerate(w.input.candidates):
            if ci != sel and (topo.type != "USER"
                              or inherited is None
                              or buda.topo_uid(topo) in inherited):
                continue
            tr = buda.TopoRow()
            tr.id = bid
            tr.cand_index = ci
            tr.type = topo.type
            tr.wirelength = topo.estimated_wirelength
            tr.trunk_location = topo.trunk_location
            tr.pass_through_count = topo.pass_through_count
            tr.connected_blocks = json.dumps(list(topo.connected_block_names))
            tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
            tr.is_selected = (ci == sel)
            tr.topo_uid = buda.topo_uid(topo)
            tr.source = "user" if topo.type == "USER" else "generated"
            self.bdb.add_topology(tr)
            for si, seg in enumerate(topo.segments):
                sr = buda.TopoSegRow()
                sr.id = bid
                sr.cand_index = ci
                sr.seg_index = si
                sr.x1, sr.y1 = seg.start.x, seg.start.y
                sr.x2, sr.y2 = seg.end.x, seg.end.y
                sr.layer_hint = seg.layer_hint
                sr.is_jog = seg.is_jog
                sr.edge_id = seg.edge_id
                sr.perp_clamp_lo = seg.perp_clamp_lo
                sr.perp_clamp_hi = seg.perp_clamp_hi
                # Only the selected (placed) topology carries plan layers.
                sr.assigned_layer = (int(seg_layers[si])
                                     if ci == sel and si < len(seg_layers)
                                     else -1)
                self.bdb.add_topology_segment(sr)
            # Logical seg-busterm links + TEG-over bridges, so a
            # `load_pipeline expanded` resume restores connectivity (and any
            # bridge) for every persisted candidate.
            self._persist_topology_annotations(bid, ci, topo, seen_busterms)

    def _restore_user_topos(self, data):
        """Replay sidecar-persisted TopoEdit op-logs ('user_topo': base uid +
        applied edit_* commands) so hand-built USER candidates exist again
        after regeneration.  Replays through the CLI's own edit commands —
        deterministic engine ops with explicit coordinates, so the rebuilt
        topology is content-identical (same topo_uid) as long as the floorplan
        is; a drifted floorplan surfaces as the normal unresolved-uid warning
        chain, never a silent wrong pin.  Skipped when the uid already
        resolves (e.g. the flow itself runs the edit commands)."""
        for sel in data.get('selections', []):
            ut = sel.get('user_topo')
            if not ut or not ut.get('ops'):
                continue
            hint = sel.get('bundle_hint', '')
            matching = [w for w in self.bundles
                        if w.input.original_bundle.get_net_names() and
                           w.input.original_bundle.get_net_names()[0].startswith(hint)]
            if not matching:
                continue
            w = matching[0]
            bid = w.input.original_bundle.id
            sc_uid = sel.get('topo_uid')
            # Skip the rebuild only when the USER candidate is already the
            # PINNED selection — nothing to do.  If it is in the pool but a
            # script `select_topology` (which resets plan.seg_slide_lo/hi to
            # the new candidate) displaced it, fall through and re-commit: the
            # replay dedups by uid (no duplicate) but re-pins AND re-applies
            # the session's slide windows, which the displacement had cleared.
            # Without this, the all-bundles `generate_topologies` + script
            # select ordering kept the USER topo selected (main-loop override)
            # but LOST its slide windows.
            sel_idx = w.plan.selected_topology_index
            if sc_uid and getattr(w.input, 'topology_pinned', False) \
                    and 0 <= sel_idx < len(w.input.candidates) \
                    and buda.topo_uid(w.input.candidates[sel_idx]) == sc_uid:
                continue                       # already pinned to it
            if self._edit_topo is not None:
                print(f"Warning: sidecar USER topology for bundle {bid} not "
                      f"rebuilt — an edit session is already open")
                continue
            base = ut.get('base', 'new')
            if base == 'new':
                base_arg = 'new'
            else:
                bi = next((i for i, c in enumerate(w.input.candidates)
                           if buda.topo_uid(c) == base), None)
                if bi is None:
                    print(f"Warning: sidecar USER topology for bundle {bid}: "
                          f"base candidate (uid {base}) not in the regenerated "
                          f"pool — skipped")
                    continue
                base_arg = str(bi + 1)
            ops = list(ut['ops'])
            print(f"[sidecar] rebuilding USER candidate for bundle {bid} "
                  f"({len(ops)} edit op(s), base {base_arg})")
            self.do_command(f"edit_topology {bid} {base_arg}")
            for op in ops:
                self.do_command(op)
            # Commit WITH pin — a sidecar `user_topo` entry is ALWAYS an
            # explicit GUI hand-build + pin (the explorer's edit_commit both
            # pins and stores the op-log).  It overrides a script
            # `select_topology`, which can only reference an AUTO-GENERATED
            # candidate: the hand-built topology exists only via this replay,
            # so "script pin wins" would make it permanently unreachable
            # (the persisted USER topo dropped on every re-run — the reported
            # flat-flow bug).  The pin also matters beyond selection: an
            # un-pinned commit DISCARDS the session's per-segment overrides
            # (slide windows, edit_set_layer pins) because they attach to the
            # selection.  The main selection loop below adopts the same
            # user_topo over a script pin, so the two paths agree.
            was_script_pinned = (
                getattr(w.input, 'topology_pinned', False)
                and w.plan.selected_topology_index >= 0
                and w.plan.selected_topology_index < len(w.input.candidates)
                and w.input.candidates[
                    w.plan.selected_topology_index].type != "USER")
            self.do_command("edit_commit pin")
            if was_script_pinned:
                print(f"  [sidecar] GUI-pinned USER candidate overrides the "
                      f"script's select_topology for bundle {bid}")
            if sc_uid and not any(buda.topo_uid(c) == sc_uid
                                  for c in w.input.candidates):
                print(f"Warning: rebuilt USER candidate for bundle {bid} does "
                      f"not match the sidecar uid (floorplan changed?) — the "
                      f"selection may fall back to type/WL matching")

    def _apply_selections(self):
        """Load the sidecar and apply pinned topologies and layer overrides.

        This acts as a baseline load. If a bundle is already pinned (e.g., via
        a `select_topology` script command), the script's choice is respected,
        but any matching layer overrides from the sidecar will still be applied.
        """
        path = self._sidecar_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: could not read selections sidecar: {e}")
            return

        # Rebuild sidecar-persisted USER candidates FIRST (the explorer's
        # TopoEdit commits store their op-log as 'user_topo'): regeneration
        # never produces a hand-built topology, so without the replay the uid
        # below can not resolve and the pin is lost on every re-run.
        self._restore_user_topos(data)

        for sel in data.get('selections', []):
            hint = sel['bundle_hint']
            # Hints are written as the bundle's FULL first net name
            # (sidecar._bundle_hint), so resolve by EXACT match first: the
            # old prefix-only match let a selection for bus "d" (hint
            # "d_0") land on bus "d_0x" (first net "d_0x_0") — a silent
            # wrong pin (audit P1-01). Prefix stays as a legacy fallback
            # for hand-written short hints, but an AMBIGUOUS prefix now
            # warns and skips instead of picking an arbitrary bundle.
            nets0 = [(w, w.input.original_bundle.get_net_names()[0])
                     for w in self.bundles
                     if w.input.original_bundle.get_net_names()]
            matching = [w for w, n0 in nets0 if n0 == hint]
            if not matching:
                matching = [w for w, n0 in nets0 if n0.startswith(hint)]
                if len(matching) > 1:
                    print(f"  Warning: sidecar hint '{hint}' is ambiguous "
                          f"({len(matching)} bundles match by prefix) — "
                          "skipping this selection")
                    continue
            if not matching:
                continue

            first_w = matching[0]
            bid = first_w.input.original_bundle.id

            # Super-candidate group pin ('S' / select_topology group:): the
            # entry stores the family's member `group_uids`.  Resolve them to
            # this session's candidate indices and set `pinned_group` on every
            # matching wrapper that is not already SCRIPT-pinned (baseline-load
            # precedence, mirroring the single-pin path below).
            grp_uids = sel.get('group_uids')
            if grp_uids:
                guset = set(grp_uids)
                for w in matching:
                    if (getattr(w.input, 'topology_pinned', False)
                            or getattr(w.input, 'pinned_group', [])):
                        continue                    # a script pin wins
                    members = [i for i, c in enumerate(w.input.candidates)
                               if buda.topo_uid(c) in guset]
                    if members:
                        w.input.pinned_group = members
                        w.plan.selected_topology_index = members[0]
                continue                            # group entry fully handled

            # 1. Resolve which topology the sidecar points to.  Chain
            #    (Phase E1b): stable content uid (survives regeneration and
            #    list reordering) -> type+WL -> warned index hint.
            resolved_sidecar_idx = None
            resolved_by_uid = False   # gate the USER-pin override below
            sc_uid = sel.get('topo_uid')
            if sc_uid:
                for i, cand in enumerate(first_w.input.candidates):
                    if buda.topo_uid(cand) == sc_uid:
                        resolved_sidecar_idx = i
                        resolved_by_uid = True
                        break
            if resolved_sidecar_idx is None:
                for i, cand in enumerate(first_w.input.candidates):
                    if (cand.type == sel['topo_type'] and
                            cand.estimated_wirelength == sel['topo_wl']):
                        resolved_sidecar_idx = i
                        break
            
            if resolved_sidecar_idx is None:
                idx_hint = sel.get('topo_index_hint', -1)
                if 0 <= idx_hint < len(first_w.input.candidates):
                    resolved_sidecar_idx = idx_hint
                    print(f"Warning: sidecar selection for bundle {bid} matched by index hint "
                          f"(type/WL changed?) — using topo {resolved_sidecar_idx + 1}")
                else:
                    print(f"Warning: sidecar selection for bundle {bid} could not be resolved — ignored")
                    continue

            # 2. Apply per wrapper (the candidate set is shared across instances,
            #    so resolved_sidecar_idx is common, but the pin decision is not):
            #    a script-pinned wrapper keeps its own topology; an unpinned one
            #    adopts the sidecar's.  Sidecar layer overrides are applied only
            #    when that wrapper's selected topology matches the sidecar's, so
            #    their segment count lines up.
            sidecar_layers = sel.get('seg_layers')
            # A sidecar `user_topo` entry is a GUI hand-build + pin; it
            # overrides a script select_topology (which cannot reference the
            # hand-built candidate), so the resolved USER candidate is adopted
            # even for a script-pinned wrapper — the reported flat-flow bug
            # where `select_topology` in the script dropped the persisted USER
            # topo.  A plain (generated-candidate) sidecar selection keeps the
            # old precedence: the script's pin wins.
            #
            # The override is gated on the USER candidate being ACTUALLY
            # resolved — by uid, AND the resolved candidate being type USER
            # (Codex #363): a STALE user_topo sidecar whose base uid left the
            # regenerated pool (rebuild skipped) can fall through to the
            # type/WL or index-hint match, which may point at an unrelated
            # GENERATED candidate.  Overriding a script pin with that would be
            # wrong — so a user_topo sidecar that no longer resolves to its
            # hand-built candidate keeps the script's pin (the safe default).
            is_user_pin = (
                bool(sel.get('user_topo')) and resolved_by_uid
                and 0 <= resolved_sidecar_idx < len(first_w.input.candidates)
                and first_w.input.candidates[resolved_sidecar_idx].type
                    == "USER")
            n_adopted = 0   # wrappers newly pinned from the sidecar
            n_layered = 0   # wrappers that received layer overrides
            for w in matching:
                w_pinned = getattr(w.input, 'topology_pinned', False)
                # Adopt the sidecar's topology when the wrapper is unpinned,
                # OR when this is a GUI USER pin landing on a DIFFERENT
                # (script-chosen) candidate — the USER pin outranks it.
                override = (is_user_pin and w_pinned
                            and w.plan.selected_topology_index
                                != resolved_sidecar_idx)
                target_idx = (resolved_sidecar_idx
                              if (not w_pinned or override)
                              else w.plan.selected_topology_index)

                if not w_pinned or override:
                    if override:
                        print(f"  [sidecar] GUI-pinned USER candidate "
                              f"overrides the script's select_topology for "
                              f"bundle {bid}")
                    w.plan.selected_topology_index = target_idx
                    w.input.topology_pinned = True
                    n_adopted += 1

                if (sidecar_layers is not None
                        and target_idx == resolved_sidecar_idx
                        and 0 <= target_idx < len(w.input.candidates)
                        and len(sidecar_layers) == len(w.input.candidates[target_idx].segments)):
                    w.input.pinned_seg_layers = list(sidecar_layers)
                    n_layered += 1
                else:
                    # The sidecar's layer overrides belong to its topology; when the
                    # script pinned a *different* topology they don't apply, so clear
                    # any stale overrides (e.g. left by an earlier baseline apply)
                    # rather than let the planner truncate them onto the new topo.
                    w.input.pinned_seg_layers = []

            n = len(matching)
            suffix = f" [{n} instances]" if n > 1 else ""
            if n_adopted:
                msg = (f"Pinned bundle {bid} to topology {resolved_sidecar_idx + 1} "
                       f"({sel['topo_type']}, WL={sel['topo_wl']})")
                if n_layered:
                    msg += f" with {len(sidecar_layers)} layer overrides"
                print(msg + suffix)
            elif n_layered:
                print(f"Merged sidecar layer overrides ({len(sidecar_layers)}) "
                      f"onto script-pinned bundle {bid}{suffix}")

    def _add_blocks_from_bdb(self, depth: int, mode: str = "deepest"):
        """Walk BDB hierarchy and call fp.add_block() for components at `depth`.

        mode="deepest": DFS — go down to `depth`; if a branch ends before
            reaching depth, add that branch's deepest component instead.
        mode="skip": only add components whose .depth == requested depth;
            shallower branches produce no block.
        mode="error": like deepest but prints a warning and returns early
            if any branch is shorter than the requested depth.
        Components without a placement are always skipped — `is_placed`, the
        same rule every generation frame uses.  This site used to read ANY
        negative coordinate as unplaced, which is not what the sentinel is:
        on `flow/ariane133` it discarded 496 of 504 depth-0 components that
        have real geometry (495 die ports at x1 = -70, plus the whole
        top-level `i_cache_subsystem` at y1 = -240), and the routes generated
        against those blocks then had nowhere to land on resume.
        """
        comps = self.bdb.all_components()

        # Build id → row and id → children maps
        by_id       = {r.id: r for r in comps}
        children_of = {}          # parent_id → list[ComponentRow]
        for r in comps:
            children_of.setdefault(r.parent_id, []).append(r)

        blocks_to_add = []   # (ComponentRow, fallback: bool)

        if mode == "skip":
            blocks_to_add = [(r, False) for r in comps if r.depth == depth]

        else:  # deepest or error
            shallow_names = []

            def collect(node, cur_depth):
                if cur_depth == depth:
                    blocks_to_add.append((node, False))
                    return
                children = children_of.get(node.id, [])
                if not children:
                    # Branch ended before requested depth
                    shallow_names.append(
                        f"{node.name!r} (depth {cur_depth}, requested {depth})")
                    if mode == "deepest":
                        blocks_to_add.append((node, True))
                    # error mode: record but don't add; will abort after DFS
                    return
                for child in children:
                    collect(child, cur_depth + 1)

            roots = [r for r in comps if r.parent_id == -1]
            for root in roots:
                collect(root, 0)

            if shallow_names and mode == "error":
                print(f"[BDB] Error: {len(shallow_names)} branch(es) shallower "
                      f"than depth {depth}:")
                for s in shallow_names[:10]:
                    print(f"  {s}")
                if len(shallow_names) > 10:
                    print(f"  … and {len(shallow_names)-10} more")
                return

        # Add blocks to floorplan
        added_rows = []   # (ComponentRow, is_fallback) for placed blocks
        skipped_unplaced = fallback_count = 0
        for r, is_fallback in blocks_to_add:
            if not is_placed(r):
                skipped_unplaced += 1
                continue
            self.fp.add_block(r.name,
                              int(round(r.x1)), int(round(r.y1)),
                              int(round(r.x2)), int(round(r.y2)))
            self._bdb_added_ids.add(r.id)
            added_rows.append((r, is_fallback))
            if is_fallback:
                fallback_count += 1

        # Container marking (Gap 2): a block is a hierarchy envelope only when at
        # least one of its BDB descendants was ALSO loaded into the floorplan —
        # those finer blocks supply the internal cuts that charge intra-block
        # congestion.  A node whose descendants were NOT imported (e.g.
        # `add_blocks_from_bdb 1 skip` on non-leaf depth-1 blocks) stays a solid
        # leaf cell and keeps blocking LOW layers.  Recomputed over every block
        # imported so far so a shallow block flips to container once a later
        # call loads its descendants.
        self._mark_bdb_containers(by_id, children_of)

        added = len(added_rows)
        parts = [f"[BDB] Added {added} blocks at depth {depth} (mode={mode})"]
        if fallback_count:
            parts.append(f"{fallback_count} used deepest-available fallback")
        if skipped_unplaced:
            parts.append(f"{skipped_unplaced} skipped (unplaced)")
        print("; ".join(parts))

        # Write block names to log file
        log_path = self._get_log_path('bdb_blocks.log')
        try:
            with open(log_path, 'w') as f:
                f.write(f"# add_blocks_from_bdb depth={depth} mode={mode}\n")
                f.write(f"# {added} blocks added\n")
                for r, is_fallback in added_rows:
                    tag = " [deepest-fallback]" if is_fallback else ""
                    f.write(f"{r.name}{tag}\n")
            print(f"[BDB] Block list written to {log_path}")
        except OSError as e:
            print(f"[BDB] Warning: could not write block log {log_path}: {e}")

    def _mark_bdb_containers(self, by_id, children_of):
        """Mark imported blocks container/leaf by whether a loaded descendant exists.

        A block is a container (transparent to LOW layers; intra-block congestion
        charged via descendant cuts) only if at least one of its BDB descendants
        was itself loaded into the floorplan.  Otherwise it stays a solid leaf
        cell that blocks LOW layers.  Recomputed over self._bdb_added_ids so the
        decision stays correct as deeper levels are imported in later calls.
        """
        added = self._bdb_added_ids
        memo: dict[int, bool] = {}

        def has_loaded_descendant(nid):
            if nid in memo:
                return memo[nid]
            memo[nid] = False   # guard against cycles
            res = any(ch.id in added or has_loaded_descendant(ch.id)
                      for ch in children_of.get(nid, []))
            memo[nid] = res
            return res

        for nid in added:
            row = by_id.get(nid)
            if row is not None:
                self.fp.set_container(row.name, has_loaded_descendant(nid))

    # ── Hierarchical topology helpers ─────────────────────────────────────────

    @staticmethod
    def _endpoint_depth(src, level, comps_by_name):
        """The hierarchy depth of a bundle endpoint block.

        ASKED OF THE COMPONENT, not counted off its name.  A path's
        separators usually give the depth, but not always: the boundary
        components `import_def_lef` synthesizes for DEF PINS are named
        `PIN/<port>` and sit at depth 0, so counting slashes put them one
        level down.  That built the routing frame at a depth holding
        NEITHER endpoint, which cost a bundle its candidates at generation
        and, at verify time, made both its busterms read as "outside block
        face" — a missing block has no bounds to be inside of.

        The depth is a stored fact; the name is a guess about it.  The
        count is the fallback only for a block with no component row (a
        flat-flow block).

        Existing flows are unaffected BY MEASUREMENT, not by argument: with
        the two readings computed side by side over the hier corpus
        (01/04/08/10_hbundles, rnr/mix, rnr/mix2, mix2_fast_bottomup,
        chip3_topdown) they agree on all 442 calls, so a design whose
        component names were all built by joining a path cannot notice the
        change.
        """
        if not src:
            return level
        c = comps_by_name.get(src)
        return c.depth if c is not None else src.count('/')

    def _build_bdb_floorplan(self, depth):
        """Build a Floorplan with placed components at exactly this depth from BDB."""
        fp = self._apply_fp_session_settings(buda.Floorplan())
        for c in self.bdb.all_components():
            if c.depth == depth and is_placed(c):
                fp.add_block(c.name, int(round(c.x1)), int(round(c.y1)),
                             int(round(c.x2)), int(round(c.y2)))
        return fp

    # Output-normalized 8-orientation maps over a w×h box — mirror of the
    # C++ orient_map table (topology.cpp): (swap axes, reflect x, reflect y).
    # Class aliases of the module-level orientation tables, so instance
    # code (and tests) can keep saying self._ORIENT_MAPS.
    _ORIENT_MAPS = _ORIENT_MAPS
    _DIR_PRESERVING = _DIR_PRESERVING

    def _mirror_const(self, horiz_flag, lids=None):
        """K with K ≡ 2σ_l (mod pitch_l) for every grid layer of the
        direction (CRT-combined, in layout units, mod the direction's pitch
        LCM); None when any pattern is asymmetric or the congruences are
        inconsistent.  A mirrored instance at coordinate c (extent E on the
        axis) sees the phase-φ tracks iff c ≡ K − E − φ (mod LCM).
        `lids` restricts the combination to a layer subset (a capped cell's
        band — fewer congruences can only make K exist more often)."""
        K, M = 0, 1
        for l, horiz in self._grid_layer_dirs().items():
            if horiz != horiz_flag or (lids is not None and l not in lids):
                continue
            pat = self.routing_grid.get_layer_grid(l).global_pattern()
            a = _pattern_two_sigma(pat)
            if a is None:
                return None
            m = max(1, int(round(pat.unit_pitch() * _PHASE_SCALE)))
            g = math.gcd(M, m)
            if (a - K) % g:
                return None      # inconsistent congruences
            mg = m // g
            t = ((a - K) // g * pow(M // g, -1, mg)) % mg if mg > 1 else 0
            K, M = K + M * t, M * mg
        return (K % M) / _PHASE_SCALE

    def _axis_phase_ok(self, refl, c_lo, c_hi, r_lo, horiz_flag):
        """Can an instance whose axis coordinate is c_lo (extent c_hi−c_lo,
        reflected on the axis iff refl) share the direction's signal tracks
        with a reference at r_lo?  Pure phase arithmetic — the cheap
        necessary condition (overrides/keepouts still need the real
        check_template_tracks)."""
        dirs = self._grid_layer_dirs()
        period = self._pitch_lcm(
            [self.routing_grid.get_layer_grid(l).global_pattern()
                 .unit_pitch()
             for l, h in dirs.items() if h == horiz_flag])
        if period is None:
            return True
        if not refl:
            d = (c_lo - r_lo) % period
        else:
            K = self._mirror_const(horiz_flag)
            if K is None:
                return False
            d = (c_lo + (c_hi - c_lo) + r_lo - K) % period
        return min(d, period - d) < 1e-6

    def _orient_phase_score(self, o, c, ref):
        """Detection tiebreak for AMBIGUOUS instances (a self-symmetric
        child layout matches several orientations, e.g. S vs FS): the count
        of axes whose track phase works under orient `o`.  Direction-
        swapping orients score -1 (dispreferred and out of phase-math
        scope); no routing grid → all zero (no discrimination)."""
        s_, rx, ry = self._ORIENT_MAPS[o]
        if s_:
            return -1
        if self.routing_grid is None:
            return 0
        return (int(self._axis_phase_ok(rx, c.x1, c.x2, ref.x1, False))
                + int(self._axis_phase_ok(ry, c.y1, c.y2, ref.y1, True)))

    def _detect_instance_orients(self, cell, comps=None, ref_name=None,
                                 cache=None):
        """Session wrapper over the module-level `detect_instance_orients`
        (see its docstring for the detection semantics): fills `comps` from
        the open BDB and injects the routing-grid-aware
        `_orient_phase_score` as the ambiguity tiebreak."""
        if comps is None:
            comps = self.bdb.all_components()
        return detect_instance_orients(comps, cell, ref_name=ref_name,
                                       cache=cache,
                                       phase_score=self._orient_phase_score)

    def _bottom_up_congruence_issues(self, cell, comps=None, ref_name=None,
                                     cache=None, only=None, allow_90=True,
                                     index=None):
        """Session wrapper over the shared `bottom_up_congruence_issues`
        (module level above — the Floorplanner's cell settings call the
        same function, so the GUI and CLI can never diverge on what
        "congruent" means), with the session's routing-grid phase tiebreak.
        `only` / `allow_90` as in the shared function (the post-split
        expansion re-check passes allow_90=False scoped to each
        template's own instances).  `index` accepts a prebuilt
        `bottom_up_congruence_index(comps)` so a many-cell caller
        (`set_bottom_up *`) pays the component walk once, not per cell.

        Returns a list of human-readable issue strings (empty = OK)."""
        if comps is None:
            comps = self.bdb.all_components()
        return bottom_up_congruence_issues(comps, cell, ref_name=ref_name,
                                           cache=cache, index=index,
                                           phase_score=self._orient_phase_score,
                                           only=only, allow_90=allow_90)

    def _eligible_bottom_up_cells(self, comps=None):
        """Enumerate every cell type that COULD be marked bottom-up — the
        keepout-scope generalization's unit (`set_bottom_up *`).

        Eligible = a cell with >= 1 placed instance whose instances are
        congruent (allow_90=True, since the rotation-class split handles the
        90 family).  A cell with >= 2 instances is the solve-once-COPY unit
        (plan/NUTS once, copy to siblings); a SINGLE-instance cell has
        nothing to copy but its cell-local routing is still solved once and
        frozen as a keepout for the levels above — the same
        `_bottom_up_fixed_segments` copy-to-one path — so it belongs in the
        generalization too.  Returns `(eligible_sorted, skipped)` where
        `skipped` is a list of `(cell, reason)` for cells whose >= 2
        instances are NON-congruent (cannot be frozen-and-copied) — the
        caller reports them LOUD and leaves them top-down.  (A single
        instance is trivially congruent, so single-instance cells are never
        skipped.)"""
        if comps is None:
            comps = self.bdb.all_components()
        # One component walk for the whole scan (Codex #265): pass the
        # prebuilt index into every per-cell congruence check so the
        # wildcard is O(#components + #cell_types·subtree), not
        # O(#cell_types·#components).
        index = bottom_up_congruence_index(comps)
        insts_by_cell = index[0]
        eligible, skipped = [], []
        cache = {}
        for cell in sorted(insts_by_cell):
            placed = sum(1 for c in insts_by_cell[cell] if is_placed(c))
            if placed < 1:
                continue                        # unplaced: nothing to freeze
            issues = self._bottom_up_congruence_issues(
                cell, comps, cache=cache, index=index)
            if issues:
                skipped.append((cell, issues[0]))
            else:
                eligible.append(cell)
        return eligible, skipped

    def _apply_fp_session_settings(self, fp, min_stub=False):
        """Mirror session-level Floorplan settings onto a DERIVED floorplan
        (cell-local, cross-level, depth projection): the global corner
        margin always; the min-stub-length tiers only on request.

        min_stub=True is passed by the CELL-LOCAL floorplan, whose frame
        must match the flat pipeline's semantics exactly — a bottom-up
        template is generated AND locally solved there, and without the
        declared min-stub the local solve sees different stub/slide
        windows than the flat solve would for the same geometry (found via
        the dogleg repro: the cell-local cycle never materialized).  The
        depth-projection and cross-level frames deliberately keep their
        historical margins-only behavior: retrofitting min-stub onto them
        measurably regressed tuned hier flows (flow 10: 212→224 segments,
        7→67 opens) and belongs to a golden review, not a bottom-up fix.
        Returns fp for chaining."""
        dx, dy = self._corner_margin
        if dx or dy:
            fp.set_global_corner_margin(dx, dy)
        ms = (getattr(self, "_min_stub", None) or {}) if min_stub else {}
        if ms.get("global") is not None:
            fp.set_min_stub_length(ms["global"])
        for d, v in (ms.get("dir") or {}).items():
            fp.set_min_stub_length_dir(d, v)
        for lid, v in (ms.get("layer") or {}).items():
            fp.set_min_stub_length_layer(lid, v)
        return fp

    def _build_cell_local_floorplan(self, parent_comp_name):
        """Build a Floorplan in cell-local coords for sub-components of
        parent, carrying the session's floorplan settings (corner margin,
        min stub lengths) exactly like the flat flow's fp."""
        comps = {c.name: c for c in self.bdb.all_components()}
        parent = comps.get(parent_comp_name)
        if parent is None:
            return None
        fp = self._apply_fp_session_settings(buda.Floorplan(), min_stub=True)
        for c in comps.values():
            if c.parent_id == parent.id and is_placed(c):
                local_name = c.name.rsplit('/', 1)[-1]
                lx1 = int(round(c.x1 - parent.x1))
                ly1 = int(round(c.y1 - parent.y1))
                lx2 = int(round(c.x2 - parent.x1))
                ly2 = int(round(c.y2 - parent.y1))
                fp.add_block(local_name, lx1, ly1, lx2, ly2)
        return fp

    @staticmethod
    def _parse_bundle_reason(reason):
        """Parse a bundle reason into (src, [dsts]).

        'DRV:x|REC:a,b,'  → ('x', ['a', 'b'])
        'BIDIR:a,b,c,'    → ('a', ['b', 'c'])   — direction-agnostic: any
            instance can root the trunk, so use the first and branch to the rest;
            the block-to-block topology reaches every instance either way.
        'FANOUT:d|TO:a,b,' -> ('d', ['a', 'b']) - a DIVERGENT fan-out
            bundle: rooted at the shared DRIVER d with every receiver as a
            leaf.  Same (root, leaves) shape as FANIN with the arrow
            reversed, which is why both land in one return.
        'FANIN:s|FROM:a,b,' -> ('s', ['a', 'b']) - a multi-driver hier
            fan-in bundle: rooted at the shared sink s with the drivers
            (and any receiver beyond the root) as leaves, exactly the
            flat _bundle_endpoints convention, so generation builds the
            same per-bit tapered fan-in tree.
        A '|SPLIT:k/n' suffix (the bundle bit bound's split marker) is
        stripped first.  Returns (None, []) on failure."""
        reason = reason.split('|SPLIT:')[0]
        try:
            if reason.startswith('FANIN:'):
                root_part, from_part = reason[len('FANIN:'):].split('|FROM:')
                leaves = [n for n in from_part.split(',') if n]
                return (root_part, leaves) if root_part and leaves else (None, [])
            if reason.startswith('FANOUT:'):
                root_part, to_part = reason[len('FANOUT:'):].split('|TO:')
                leaves = [n for n in to_part.split(',') if n]
                return (root_part, leaves) if root_part and leaves else (None, [])
            if reason.startswith('BIDIR:'):
                insts = [n for n in reason[len('BIDIR:'):].split(',') if n]
                return (insts[0], insts[1:]) if insts else (None, [])
            drv_part, rec_part = reason.split('|REC:')
            src = drv_part[4:]              # strip leading "DRV:"
            dsts = [n for n in rec_part.split(',') if n]
            return src, dsts
        except (ValueError, IndexError):
            return None, []

    @staticmethod
    def _fmt_index_ranges(nums):
        """Compress sorted-unique ints into compact ranges: [0,1,2,3,5,6] -> '0:3,5:6'."""
        nums = sorted(set(nums))
        out, i = [], 0
        while i < len(nums):
            j = i
            while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                j += 1
            out.append(str(nums[i]) if i == j else f"{nums[i]}:{nums[j]}")
            i = j + 1
        return ",".join(out)

    def _bundle_net_summary(self, net_names, max_len=200):
        """Group a bundle's nets into buses for a compact log line.

        A net that belongs to a bus is folded into it with a compressed bit
        range like 'bus_077[0:59]'; a net that does not is listed verbatim.
        BOTH spellings count (`bus_names.parse_bus_bit`): the declared
        '<bus>_<idx>' / '<bus>_b<idx>' that add_bus/add_net emit, and the
        imported '<bus>[<idx>]' a LEF/DEF/Verilog design keeps.  Only the
        first was recognised, so an imported design never compressed at all
        — the very case where the line is longest.  First-seen order is
        preserved; truncated past max_len."""
        groups, order, seen = {}, [], set()
        for nm in net_names:
            parsed = parse_bus_bit(nm)
            if parsed:
                bus, tag, idx = parsed
                key = (bus, tag)          # e.g. ('bus_077','b') or ('dbg','[]')
                if key not in groups:
                    groups[key] = []
                    order.append(('bus', key, bus))
                groups[key].append(idx)
            elif ('net', nm) not in seen:
                seen.add(('net', nm))
                order.append(('net', nm, nm))
        parts = [f"{disp}[{self._fmt_index_ranges(groups[key])}]" if kind == 'bus'
                 else disp
                 for kind, key, disp in order]
        summary = ", ".join(parts)
        if len(summary) > max_len:
            summary = summary[:max_len - 1].rstrip(", ") + "…"
        return summary

    def _bundle_nets_suffix(self, w):
        """'<n> nets: <bus summary>' appended to a generate_* per-bundle log line,
        so the bundle-id -> buses/nets correspondence rides on the same line."""
        nets = w.input.original_bundle.get_net_names()
        return (f"{len(nets)} net{'' if len(nets) == 1 else 's'}: "
                f"{self._bundle_net_summary(nets)}")

    def _prep_hier_topo_gen(self, w, use_center, use_double_detour,
                            fp_cache, comps_by_name, use_multi_trunk,
                            use_hanan_loci, use_spine_relays):
        """Resolve one HBundle's generation task WITHOUT generating.

        The 3-case frame dispatch (`_hier_topo_gen_cases`) plus the ENDPOINT
        GUARD — a block the resolved frame does not have is refused here
        rather than routed to.

        The guard is the hier half of a check the flat flow has always had:
        `Floorplan::get_block_bounds` silently returns a degenerate
        {0,0,0,0} for an unknown name (`topology.h` says so and points at
        `has_block`), so `add_net`/`add_bus` run `_validate_endpoint_blocks`
        — "would route from the chip origin instead of the intended block,
        with no error".  Hier generation never did, and the sentence
        describes what it produced: on `flow/ariane133`, 27 of the 128 block
        names in the persisted topologies are components with no placement,
        and bundle 41 (`DRV:i_cache_subsystem|REC:i_perf_counters`) came out
        with a selected topology ending at literally (0,0) and placed metal
        at y = -840 — off the die — while the flow reported success.

        Returns (tg, src, dsts, meta) or None when the bundle is skipped."""
        prep = self._hier_topo_gen_cases(w, use_center, use_double_detour,
                                         fp_cache, comps_by_name,
                                         use_multi_trunk, use_hanan_loci,
                                         use_spine_relays)
        if prep is None:
            return None
        tg, src, dsts, meta = prep
        fp = meta['fp']
        absent = [d for d in dsts if not fp.has_block(d)]
        src_absent = src is None or not fp.has_block(src)
        if not absent and not src_absent:
            return prep                       # the ordinary case: unchanged
        bid = w.input.original_bundle.id
        named = ', '.join(sorted(set(absent + ([src] if src_absent and src
                                               else []))))
        kept = [d for d in dsts if d not in set(absent)]
        # No root, or nothing left to reach: there is no routing interface,
        # and inventing one at the origin is what this guard exists to stop.
        # The nets stay unrouted, which is the true state of the design —
        # a floorplan that places none of these blocks cannot route them.
        if src_absent or not kept:
            buda_diag.emit(
                "BUDA-1211",
                f"bundle {bid} ({meta['label']}): endpoint block(s) {named} "
                f"have no placement, leaving no placed endpoint pair — "
                f"generating no candidates.  Place them (a container needs "
                f"`derive_container_bboxes`, which needs a placed "
                f"descendant) or bundle at a depth whose blocks are placed.")
            return None
        buda_diag.emit(
            "BUDA-1210",
            f"bundle {bid} ({meta['label']}): endpoint block(s) {named} have "
            f"no placement and were dropped from its routing interface; the "
            f"remaining {len(kept)} endpoint(s) still route.  Their nets are "
            f"open at the dropped end.")
        return tg, src, kept, meta

    def _hier_topo_gen_cases(self, w, use_center, use_double_detour,
                             fp_cache, comps_by_name, use_multi_trunk,
                             use_hanan_loci, use_spine_relays):
        """The 3-case frame/endpoint dispatch behind `_prep_hier_topo_gen`.

        Returns (tg, src, dsts, meta) — a configured TopologyGenerator plus
        the endpoints and the install/report context — or None when the
        bundle is skipped (the skip warning prints here, exactly the
        sequential text).  Extracted from _generate_hier_topo_one so the
        parallel batch path (generate_candidates_batch) shares every line
        of the 3-case dispatch with the sequential loop."""
        b = w.input.original_bundle
        nets_suffix = self._bundle_nets_suffix(w)   # rides on each per-bundle log line

        if b.cell_context and b.entry_busterm_ids:
            # Case (a): cell-local floorplan
            parent_name = b.instances[0] if b.instances else None
            if parent_name is None:
                print(f"  Warning: bundle {b.id} has cell_context but no instances — skipping")
                return None
            cache_key = ('cell', parent_name)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_cell_local_floorplan(parent_name)
            cell_fp = fp_cache[cache_key]
            if cell_fp is None:
                print(f"  Warning: could not build cell-local fp for {parent_name!r} — skipping")
                return None
            tg = self._make_topo_gen(cell_fp, use_center, use_double_detour,
                                     use_multi_trunk, use_hanan_loci,
                                     use_spine_relays)
            entries = [e.removeprefix('bt:').rsplit('/', 1)[-1]
                       for e in b.entry_busterm_ids]
            exits = [e.removeprefix('bt:').rsplit('/', 1)[-1]
                     for e in b.exit_busterm_ids]
            if len(entries) > 1:
                # Cell-local FAN-IN template (multi-driver CONVERGENT/
                # COMBINED group inside one cell): root at the shared sink,
                # drivers (+ extra receivers) as leaves — the flat fan-in
                # convention, per-bit tapered below.  The root is filtered
                # out of the leaves and the list deduped (mirroring the C++
                # FANIN reason builder and the flat _bundle_endpoints):
                # generate_npin doesn't dedupe, so a self-destination would
                # instantiate the root block as a Busterm twice.
                src_local = exits[0]
                seen = {src_local}
                dsts_local = []
                for x in entries + exits[1:]:
                    if x not in seen:
                        seen.add(x)
                        dsts_local.append(x)
                fanin_a = True
            else:
                src_local = entries[0]
                dsts_local = exits
                fanin_a = False
            label = (f"[{','.join(dsts_local)}]→{src_local} fan-in" if fanin_a
                     else f"{src_local}→{dsts_local[0]}")
            meta = dict(fp=cell_fp, fanin_local=True, label=label,
                        tag=f"[cell:{b.cell_context}]", pad="  ",
                        nets_suffix=nets_suffix)
            return tg, src_local, dsts_local, meta

        elif b.drv_spec_depth >= 0:
            # Case (c): cross-level — custom floorplan from actual endpoint blocks.
            # A MULTI-DRIVER cross-level group (CONVERGENT/COMBINED) is a fan-in
            # bundle: root at the shared sink with every driver as a leaf, and
            # per-bit tapered from HBundle.net_drivers/net_receivers — the same
            # treatment as the same-level fan-in case (b), extended across the
            # hierarchy boundary.
            fanin_c, root_c, leaves_c = self._xlevel_fanin_endpoints(b)
            if fanin_c:
                src = root_c
                dsts = leaves_c
            else:
                src = b.drv_spec_path
                dsts = list(b.rcv_spec_paths)
            fp = self._apply_fp_session_settings(buda.Floorplan())
            for blk in [src, *dsts]:
                c = comps_by_name.get(blk)
                if c is None:
                    print(f"  Warning: endpoint comp {blk!r} not found — "
                          f"skipping bundle {b.id}")
                    return None
                # An unplaced endpoint has no extent to route to; leaving it
                # OUT of the frame is what makes the shared endpoint guard
                # (below) see it, instead of `get_block_bounds` handing the
                # generator a phantom {0,0,0,0} block at the die origin.
                if not is_placed(c):
                    continue
                fp.add_block(blk, int(round(c.x1)), int(round(c.y1)),
                             int(round(c.x2)), int(round(c.y2)))
            tg = self._make_topo_gen(fp, use_center, use_double_detour,
                                     use_multi_trunk, use_hanan_loci,
                                     use_spine_relays)
            if fanin_c:
                label = f"[{','.join(dsts)}]→{src} fan-in"
            else:
                label = (f"{src}→{dsts[0]}" if len(dsts) == 1
                         else f"{src}→[{','.join(dsts)}]")
            tag = (f"[cross-level D{b.drv_spec_depth}→D{b.rcv_spec_depth}"
                   f"{' fan-in' if fanin_c else ''}]")
            # Per-bit taper only for the fan-in form (no-op / conservative
            # full width on a resumed session whose net_drivers were not
            # persisted).
            meta = dict(fp=fp, fanin_local=False if fanin_c else None,
                        label=label, tag=tag, pad="  ",
                        nets_suffix=nets_suffix)
            return tg, src, dsts, meta

        else:
            # Case (b): same-level cross-block — BDB floorplan at the
            # endpoints' depth.  b.level is the routing-context depth (the
            # endpoints' common ancestor), which can be shallower than the
            # endpoint paths themselves; the blocks to route between live at
            # the endpoint depth.
            #
            # That depth is ASKED OF THE COMPONENT, not counted off its name.
            # A path's separators usually give it, but not always: the
            # boundary components `import_def_lef` synthesizes for DEF PINS
            # are named `PIN/<port>` and sit at depth 0, so counting slashes
            # put them one level down, built the floorplan at a depth
            # containing NEITHER endpoint, and the bundle came out with zero
            # candidates — unroutable, reported only as a warning among the
            # per-bundle lines.  The depth is a stored fact; the name is a
            # guess about it.  Fall back to the count only for a block with
            # no component row at all (a flat-flow block).
            src, dsts = self._parse_bundle_reason(b.reason)
            ep_depth = self._endpoint_depth(src, b.level, comps_by_name)
            cache_key = ('depth', ep_depth)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_bdb_floorplan(ep_depth)
            depth_fp = fp_cache[cache_key]
            tg = self._make_topo_gen(depth_fp, use_center, use_double_detour,
                                     use_multi_trunk, use_hanan_loci,
                                     use_spine_relays)
            if src is None:
                print(f"  Warning: could not parse reason for bundle {b.id}: {b.reason!r}")
                return None
            if b.reason.startswith('FANOUT:'):
                label = f"{src}→[{','.join(dsts)}] fan-out"
            elif b.reason.startswith('FANIN:'):
                label = f"[{','.join(dsts)}]→{src} fan-in"
            else:
                label = (f"{src}→{dsts[0]}" if len(dsts) == 1
                         else f"{src}→[{','.join(dsts)}]")
            meta = dict(fp=depth_fp, fanin_local=False, label=label,
                        tag="", pad=" ", nets_suffix=nets_suffix)
            return tg, src, dsts, meta

    def _install_hier_topo_result(self, w, fresh, meta, additive=False):
        """Install one HBundle's freshly generated pool and print the
        per-bundle report line — the shared tail of the sequential and
        batch generation paths.  Returns the candidate count (new-candidate
        count under additive)."""
        b = w.input.original_bundle
        if additive:
            added, dups = self._merge_more_candidates(w, fresh)
            n, detail = added, (f"+{added} new candidate(s), {dups} "
                                f"duplicate(s) skipped, pool now "
                                f"{len(w.input.candidates)}")
        else:
            old_pin_uid = self._pinned_uid(w)
            kept_user = self._user_candidates(w)
            w.input.candidates = fresh
            self._reset_plan_for_regen(w, old_pin_uid, kept_user)
            n = len(w.input.candidates)
            detail = f"{n} candidates"
        if meta['fanin_local'] is not None:
            self._derive_hier_fanin_bits(w, meta['fp'],
                                         local=meta['fanin_local'])
        label, tag, pad = meta['label'], meta['tag'], meta['pad']
        suffix = (f"{tag} " if tag else "") + meta['nets_suffix']
        if n == 0 and not w.input.candidates:
            print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                  f"0 candidates — bundle will be unrouted!{pad}{suffix}")
        else:
            print(f"HierTopo D{b.level}: bundle {b.id} ({label}) "
                  f"{detail}{pad}{suffix}")
        return n

    def _generate_hier_topo_one(self, w, use_center, use_double_detour,
                                fp_cache, comps_by_name, use_multi_trunk=False,
                                additive=False, use_hanan_loci=True,
                                use_spine_relays=False):
        """Generate topology candidates for a single HBundle wrapper.

        Updates w.input.candidates in place. Returns candidate count.
        fp_cache is a dict shared across calls; pass {} for a fresh cache.
        comps_by_name is {name: ComponentRow} from bdb.all_components().
        use_multi_trunk adds two-level BITRUNK_HVH/VHV datapath trees (opt-in),
        as in the flat generate_topologies.
        use_hanan_loci samples trunk loci ON in-bbox Hanan lines as well as at
        channel midpoints (DEFAULT-ON since the hanan_loci default flip; pass
        False — the `no_hanan_loci` command flag / knob-memo opt-out — for a
        midpoint-only pool), as in the flat generate_topologies.

        additive=True is the generate_more_topologies contract: the fresh
        candidates are MERGED into the existing pool (topo_uid dedup + WL
        re-sort with the selection/dogleg refs remapped) instead of
        replacing it — existing candidates, the pin, and plan state are
        untouched.  Returns the number of NEW candidates then.
        """
        prep = self._prep_hier_topo_gen(w, use_center, use_double_detour,
                                        fp_cache, comps_by_name,
                                        use_multi_trunk, use_hanan_loci,
                                        use_spine_relays)
        if prep is None:
            return 0
        tg, src, dsts, meta = prep
        fresh = tg.generate_candidates(src, dsts)
        return self._install_hier_topo_result(w, fresh, meta,
                                              additive=additive)

    def _apply_hier_gen_knobs(self, w, fp_cache, comps_by_name,
                              old_pin_uid=None):
        """Hier twin of `_apply_gen_knobs` (edit.py): honor the bundle's
        persisted generation-knob memo (v15) by re-running the 3-case hier
        generator ADDITIVELY after a bulk regeneration, so an HBundle pool
        accreted with generate_more_topologies does not silently revert on
        the next generate_hier_topologies.  A `no_hanan_loci` memo token
        (the default-flip opt-out) is replayed by REGENERATING the bundle's
        base pool midpoint-only first — an additive merge can only add, so
        the opt-out round-trips via replacement, exactly mirroring how the
        opt-ins round-trip via accretion.  Re-attempts the uid pin reattach
        among the appended extras."""
        if self.bdb is None:
            return
        knobs = self.bdb.bundle_gen_knobs(str(w.input.original_bundle.id))
        if not knobs:
            return
        ks = set(knobs.split())
        # Loci polarity (see the memo-encoding note at _record_gen_knob_memo,
        # topologies_cmds.py): a memo token wins for this bundle — either
        # polarity — else the bulk command's effective setting applies.
        bulk = getattr(self, "_hier_gen_knobs",
                       (False, False, False, True, False))
        if "no_hanan_loci" in ks:
            replay_loci = False
        elif "hanan_loci" in ks:
            replay_loci = True
        else:
            replay_loci = bulk[3]
        if "no_hanan_loci" in ks and bulk[3]:
            # The opt-OUT cannot be replayed additively (an additive merge
            # can only ADD candidates) — REGENERATE this bundle's base pool
            # midpoint-only (bulk's other knobs kept; pin re-attached by uid,
            # USER candidates kept by the non-additive install).
            self._generate_hier_topo_one(
                w, bulk[0], bulk[1], fp_cache, comps_by_name, bulk[2],
                additive=False, use_hanan_loci=False,
                use_spine_relays=bulk[4])
            print(f"  Re-applied knob memo '{knobs}' for bundle "
                  f"{w.input.original_bundle.id}: regenerated midpoint-only "
                  f"(no_hanan_loci).")
        # A memo with NO additive opt-in tokens has nothing left to replay:
        # the base pool already honors the bulk knobs and the loci polarity
        # (regenerated above for an opt-out under a loci-on bulk).  Running
        # the additive generator anyway — all non-memo knobs off — would
        # append a PLAIN midpoint pool into e.g. a center_mode bulk regen,
        # polluting the requested candidate set.  The one additive job left
        # for a pure-polarity memo is a loci OPT-IN against a loci-off base.
        base_loci = False if "no_hanan_loci" in ks else bulk[3]
        if (not (ks - {"hanan_loci", "no_hanan_loci"})
                and not (replay_loci and not base_loci)):
            return
        added = self._generate_hier_topo_one(
            w, "center_mode" in ks, "double_detour" in ks, fp_cache,
            comps_by_name, "multi_trunk" in ks, additive=True,
            use_hanan_loci=replay_loci, use_spine_relays="spine_relays" in ks)
        if added:
            print(f"  Re-applied knob memo '{knobs}' for bundle "
                  f"{w.input.original_bundle.id}: +{added} candidate(s).")
            if old_pin_uid and not w.input.topology_pinned:
                for i, c in enumerate(w.input.candidates):
                    if buda.topo_uid(c) == old_pin_uid:
                        w.plan.selected_topology_index = i
                        w.input.topology_pinned = True
                        print(f"  Pin re-attached by topo_uid to candidate "
                              f"{i + 1}.")
                        break

    def _floorplan_for_hbundle(self, b, fp_cache, comps_by_name):
        """Return the Floorplan an HBundle's candidates were generated in.

        Mirrors the 3-case floorplan selection in _generate_hier_topo_one so a
        connectivity check evaluates each topology in the same coordinate /
        block-name space its candidates were built in.  Returns None when the
        floorplan can't be reconstructed (caller falls back to self.fp).

        This resolves the PRE-expansion floorplan for a cell-level template
        (cell-local coords, cell-local block names).  Per-instance wrappers
        produced by _expand_hier_bundles carry absolute coords with dropped
        seg_busterms and must be checked against self.fp — callers exclude them.
        """
        if b.cell_context and b.entry_busterm_ids:
            # Case (a): cell-local floorplan.
            parent_name = b.instances[0] if b.instances else None
            if parent_name is None:
                return None
            cache_key = ('cell', parent_name)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_cell_local_floorplan(parent_name)
            return fp_cache[cache_key]

        if b.drv_spec_depth >= 0:
            # Case (c): cross-level custom floorplan from the endpoint blocks.
            # A fan-in bundle spans every driver + the shared sink (the same
            # block set generation used), so check_design / the explorer /
            # dump_topologies see the whole tree, not just the first driver.
            cache_key = ('xlevel', b.id)
            if cache_key not in fp_cache:
                fanin_c, root_c, leaves_c = self._xlevel_fanin_endpoints(b)
                blocks = ([root_c, *leaves_c] if fanin_c
                          else [b.drv_spec_path, *b.rcv_spec_paths])
                fp = self._apply_fp_session_settings(buda.Floorplan())
                for blk in blocks:
                    c = comps_by_name.get(blk)
                    if c is None:
                        fp = None
                        break
                    # An UNPLACED endpoint is left out, exactly as generation
                    # leaves it out (_prep_hier_topo_gen's case (c)) — this
                    # frame's job is to be the one candidates were built in.
                    # Adding it put a block at (-1,-1,-1,-1) here, which made
                    # this frame accept a block contract no other frame could.
                    if not is_placed(c):
                        continue
                    fp.add_block(blk, int(round(c.x1)), int(round(c.y1)),
                                 int(round(c.x2)), int(round(c.y2)))
                fp_cache[cache_key] = fp
            return fp_cache[cache_key]

        # Case (b): same-level cross-block — BDB floorplan at the endpoint depth.
        src, _ = self._parse_bundle_reason(b.reason)
        ep_depth = self._endpoint_depth(src, b.level, comps_by_name)
        cache_key = ('depth', ep_depth)
        if cache_key not in fp_cache:
            fp_cache[cache_key] = self._build_bdb_floorplan(ep_depth)
        return fp_cache[cache_key]

    def _make_topo_fp_resolver(self):
        """Per-call resolver: bundle wrapper → the Floorplan its candidates were
        generated against, for `dump_topologies`' slide/envelope columns —
        `self.fp` for everything except the two hier generation cases whose
        floorplan genuinely differs from it:

          (a) a pre-expansion CELL-LOCAL template (`cell_context` +
              `entry_busterm_ids` set), generated in cell-local coordinates;
          (c) a CROSS-LEVEL bundle (`drv_spec_depth >= 0`), generated in a
              custom endpoint floorplan whose spec-path block names are not
              in `self.fp`.

        Both markers are set only by the hierarchical bundler, so a FLAT
        bundle — which is also a `buda.HBundle`, and whose candidates were
        generated against `session.fp` even when a BDB is open (Codex #231) —
        always keeps `self.fp`, as do the expanded per-instance wrappers
        (absolute coords) and same-level cross-block hier bundles (generated
        against the BDB depth projection that `add_blocks_from_bdb` mirrors
        into `self.fp`).  Resolution via the same `_floorplan_for_hbundle`
        that `check_design` uses, so a cell-level template shows real
        finite slides BEFORE `run_planner hier` instead of the
        unbounded-sentinel `free`."""
        if self.bdb is None:
            return lambda w: self.fp
        fp_cache = {}
        comps_by_name = {c.name: c for c in self.bdb.all_components()}
        expanded_ids = {id(w)
                        for ws in (self._hier_expansion_map or {}).values()
                        for w in ws}

        def resolve(w):
            b = w.input.original_bundle
            if (isinstance(b, buda.HBundle) and id(w) not in expanded_ids
                    and ((b.cell_context and b.entry_busterm_ids)
                         or b.drv_spec_depth >= 0)):
                fp = self._floorplan_for_hbundle(b, fp_cache, comps_by_name)
                if fp is not None:
                    return fp
            return self.fp
        return resolve

    @staticmethod
    def _clone_hbundle_with_id(b, new_id):
        """Return a shallow clone of HBundle b with id replaced by new_id."""
        nb = buda.HBundle()
        nb.id                = new_id
        nb.net_names         = b.net_names
        nb.reason            = b.reason
        nb.num_terminals     = b.num_terminals
        nb.level             = b.level
        nb.cell_context      = b.cell_context
        nb.instances         = b.instances
        nb.parent_id         = b.parent_id
        nb.child_ids         = b.child_ids
        nb.entry_busterm_ids = b.entry_busterm_ids
        nb.exit_busterm_ids  = b.exit_busterm_ids
        nb.drv_spec_depth    = b.drv_spec_depth
        nb.rcv_spec_depth    = b.rcv_spec_depth
        nb.drv_spec_path     = b.drv_spec_path
        nb.rcv_spec_paths    = b.rcv_spec_paths
        # Fan-in taper metadata must ride clone templates (90° rotation
        # classes) too, or their instances silently lose the per-bit taper.
        nb.net_drivers       = b.net_drivers
        nb.net_receivers     = b.net_receivers
        return nb

    def _apply_layer_policies(self, wrappers=None):
        """Resolve per-cell layer policies onto wrapper masks (Phase 1 of
        docs/internal/hier_layer_caps.md).

        For every wrapper whose bundle has a cell_context, look up the cell's
        band [floor..cap] — explicit policy first, then the '*' default — and
        set input.allowed_layers to the declared layers inside the band.
        Rotation-class clone templates (<cell>90) inherit the base cell's
        policy via _bu_cell_of.  cell_context "" (top-level) stays
        unrestricted in Phase 1.  Idempotent and cheap, so the hier command
        path calls it at each wrapper-set transition (templates before the
        bottom-up solves; the EXPANDED per-instance wrappers before
        optimize_topologies plans them) — expansion builds fresh BundleInput
        objects, so masks must be re-resolved, never assumed to ride along.
        `wrappers` overrides the target set (default self.bundles): the hier
        path passes the expanded vec BEFORE it becomes session.bundles, so
        the global planner never sees an unmasked capped instance.

        No policy declared anywhere => wrappers carry empty masks and every
        consumer short-circuits: the byte-identity guarantee.  A previously
        governed wrapper is actively CLEARED on that path, so
        `set_cell_layer_cap * off` + re-plan really lifts the caps instead
        of leaving stale masks behind."""
        if wrappers is None:
            wrappers = self.bundles
        pol = getattr(self, "_cell_layer_policy", None) or {}
        shares = getattr(self, "_cell_layer_shares", None) or {}

        def _clear(w):
            if w.input.allowed_layers or w.input.layer_cap >= 0 \
                    or w.input.layer_floor >= 0 or w.input.layer_shares \
                    or w.input.share_group:
                w.input.allowed_layers = []
                w.input.layer_cap = -1
                w.input.layer_floor = -1
                w.input.layer_shares = []
                w.input.share_group = ""
                w.input.share_budgets = []
                return True
            return False

        if not pol and not shares:
            n_cleared = sum(1 for w in wrappers if _clear(w))
            if n_cleared:
                print(f"[LayerCap] cleared stale masks on {n_cleared} "
                      f"bundle(s) (no cell layer policies declared)")
            # This resolver OWNS allowed_layers on hier wrappers, so a
            # governed rule's layer restriction is re-applied after every
            # resolution (incl. this clearing path) — see R2d in
            # docs/internal/ndr_architecture.md.
            from buda_cmds import ndr_cmds
            ndr_cmds.reapply_ndr_layer_restrictions(self, wrappers)
            return
        all_lids = sorted(
            list(self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL)) +
            list(self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)))
        bu_cells = set(self.bdb.bottom_up_cells()) if self.bdb else set()
        comps = ({c.name: c for c in self.bdb.all_components()}
                 if (self.bdb is not None and shares) else {})
        n_gov = n_shared = 0

        for w in wrappers:
            ctx = self._bu_cell_of(w.input.original_bundle.cell_context)
            if not ctx:
                _clear(w)
                continue
            entry = pol.get(ctx, pol.get("*"))
            cell_shares = {lid: s for (c, lid), s in shares.items()
                           if c == ctx and s < 1.0}
            if entry is None and not cell_shares:
                _clear(w)
                continue
            if entry is not None:
                floor, cap = entry
                lo = floor if floor >= 0 else -(10 ** 9)
                # The band, PLUS any shared layer — a share overrides the
                # band in either direction (§4 resolution: thin inside it,
                # or grant a bounded slice above the cap).
                w.input.allowed_layers = sorted(
                    {l for l in all_lids if lo <= l <= cap}
                    | set(cell_shares))
                w.input.layer_cap = cap
                w.input.layer_floor = floor
                n_gov += 1
            else:
                # Share-only cell: unrestricted band (empty mask), the
                # shared layers still fractionally leased.
                w.input.allowed_layers = []
                w.input.layer_cap = -1
                w.input.layer_floor = -1
            w.input.layer_shares = sorted(cell_shares.items())
            if cell_shares:
                n_shared += 1
            # Tier-2 collective-budget identity (Q3 resolved): only for
            # bundles the GLOBAL planner plans — a bottom-up cell's
            # templates get the Tier-1 thinned views instead (their local
            # width model uses the derived bit pitch, whose units the
            # scalar budget's global-basis books would not match).
            w.input.share_group = ""
            w.input.share_budgets = []
            insts = list(w.input.original_bundle.instances)
            if (cell_shares and ctx not in bu_cells and insts
                    and self.routing_grid is not None):
                comp = comps.get(insts[0])
                if comp is not None and is_placed(comp):
                    budgets = []
                    for lid, s in sorted(cell_shares.items()):
                        if not self.routing_grid.has_layer(lid):
                            continue
                        g = self.routing_grid.get_layer_grid(lid)
                        horiz = (self.layers.get_layer_dir(lid)
                                 == buda.LayerDir.HORIZONTAL)
                        # An H layer's tracks stack along y; V along x.
                        if horiz:
                            trks = g.signal_tracks_in(
                                0.5 * (comp.x1 + comp.x2), comp.y1, comp.y2)
                        else:
                            trks = g.signal_tracks_in(
                                0.5 * (comp.y1 + comp.y2), comp.x1, comp.x2)
                        bit_pitch = self.layers.eff_bus_width(1, 1.0, lid)
                        budgets.append((lid, s * len(trks) * bit_pitch))
                    if budgets:
                        w.input.share_group = f"{ctx}@{insts[0]}"
                        w.input.share_budgets = budgets
        if n_gov or n_shared:
            msg = f"[LayerCap] {n_gov} bundle(s) governed by cell layer " \
                  f"policies ({len(pol)} polic{'y' if len(pol) == 1 else 'ies'})"
            if n_shared:
                msg += (f"; {n_shared} carrying fractional shares "
                        f"({len(shares)} share(s))")
            print(msg)
        # NDR layer restrictions re-applied AFTER the band resolution (this
        # resolver owns allowed_layers on hier wrappers): the effective mask
        # is the band ∩ the governing rule's layers — an empty intersection
        # hard-errors there (R2d).
        from buda_cmds import ndr_cmds
        ndr_cmds.reapply_ndr_layer_restrictions(self, wrappers)
        # This resolver is the moment a hier wrapper's reachable layer set
        # becomes final, and the NDR no-op verdict is a statement ABOUT that
        # set — so it waits for this flag rather than judging a capped bundle
        # as able to reach every layer (Codex P2 on #737).
        self._layer_masks_resolved = True

    def _restore_layer_policies(self):
        """Rebuild _cell_layer_policy from the open BDB (v20): per-cell bands
        from cell.layer_floor/layer_cap, the '*' default from
        meta.layer_cap_default.  Entries the user TYPED this session win —
        a user who sets a band before/after open_bdb keeps what they typed
        (set_cell_layer_cap writes through, so the two stay in sync anyway).
        Entries a PREVIOUS restore added are NOT session-typed: switching to
        another BDB must not carry the old design's bands along (Codex #546),
        so restored keys are tracked in _cell_layer_policy_restored and
        dropped before this BDB's policies merge in.  Returns the number of
        entries restored."""
        if self.bdb is None:
            return 0
        pol = getattr(self, "_cell_layer_policy", None) or {}
        prev = getattr(self, "_cell_layer_policy_restored", None) or set()
        dropped = {k for k in prev if k in pol}
        for k in dropped:
            del pol[k]
        if dropped:
            self._cell_layer_policy = pol
            print(f"[LayerCap] dropped {len(dropped)} polic"
                  f"{'y' if len(dropped) == 1 else 'ies'} restored from the "
                  f"previously open BDB: {', '.join(sorted(dropped))}")
        restored = {}
        for c in self.bdb.layer_capped_cells():
            if c not in pol:
                floor, cap = self.bdb.cell_layer_band(c)
                restored[c] = (floor, cap)
        dflt = self.bdb.meta_get("layer_cap_default", "")
        if dflt and "*" not in pol:
            try:
                floor_s, cap_s = dflt.split(":")
                restored["*"] = (int(floor_s), int(cap_s))
            except ValueError:
                print(f"WARNING: malformed meta.layer_cap_default "
                      f"{dflt!r} — ignored")
        self._cell_layer_policy_restored = set(restored)
        # By-depth PROVENANCE (Codex #551): a persisted band alone cannot say
        # whether it was declared explicitly or by set_layer_caps_by_depth,
        # and that difference decides precedence (explicit always outranks)
        # and what `set_layer_caps_by_depth off` may clear.  The memo rides in
        # meta; session-typed by-depth entries survive a BDB switch like any
        # other typed entry, restored ones do not (they left `pol` above).
        bd = getattr(self, "_cell_layer_policy_by_depth", None) or set()
        bd -= dropped
        memo = self.bdb.meta_get("layer_caps_by_depth", "")
        bd |= {c for c in memo.split(",") if c and (c in pol or c in restored)}
        self._cell_layer_policy_by_depth = bd
        if restored:
            pol.update(restored)
            self._cell_layer_policy = pol
            print(f"[LayerCap] restored {len(restored)} persisted cell layer "
                  f"polic{'y' if len(restored) == 1 else 'ies'}: "
                  + ", ".join(
                      f"{c}=[{f if f >= 0 else 'min'}..{cp}]"
                      for c, (f, cp) in sorted(restored.items())))
        # Fractional shares (v20 cell_layer_share, Phase 3): same contract —
        # typed entries win, a previous BDB's restored entries drop first.
        shr = getattr(self, "_cell_layer_shares", None) or {}
        prev_s = getattr(self, "_cell_layer_shares_restored", None) or set()
        dropped_s = {k for k in prev_s if k in shr}
        for k in dropped_s:
            del shr[k]
        restored_s = {}
        for c in self.bdb.layer_share_cells():
            for lid, s in self.bdb.cell_layer_shares(c):
                if (c, lid) not in shr:
                    restored_s[(c, lid)] = s
        self._cell_layer_shares_restored = set(restored_s)
        if restored_s or dropped_s:
            shr.update(restored_s)
            self._cell_layer_shares = shr
        if restored_s:
            print(f"[LayerShare] restored {len(restored_s)} persisted "
                  f"share(s): "
                  + ", ".join(f"{c}:L{lid}={s:g}"
                              for (c, lid), s in sorted(restored_s.items())))
        return len(restored) + len(restored_s)

    def _cell_levels(self):
        """Intrinsic bottom-anchored LEVEL of every cell type in the open BDB.

        The level a cell is CAPPED BY (`set_layer_caps_by_depth`,
        docs/internal/hier_layer_caps.md §13 Phase 5 Q1) is a property of the
        cell's own subtree, not of where it happens to be instantiated —
        counting DEEPEST-FIRST so a cell instantiated at several hierarchy
        depths still has one well-defined level:

        * a childless NON-container cell is **level 1** (the deepest leaves,
          capped by the first argument);
        * a childless CONTAINER cell is **level 2** — it has no children yet
          but is declared to acquire them, so it carries one level of
          reserved headroom;
        * otherwise **level = 1 + max over child cells' levels** — the
          tallest subtree governs, so a cell is never capped below what its
          deepest content needs.

        Container-ness comes from the design itself: a component of the cell
        marked non-leaf in the BDB (`import_verilog` marks every DEFINED
        module non-leaf, childless or not) or a block of that cell marked a
        container in the session floorplan.  The child graph unions the
        `cell_children` cell-type edges with the elaborated component tree,
        so both construction paths (add_inst_to_cell, import_verilog) level
        identically.

        Returns `(levels, containers)`: {cell: level} over every cell the BDB
        knows, and the set of cells judged containers.
        """
        if self.bdb is None:
            return {}, set()
        children: dict[str, set] = {}
        cells: set[str] = set()
        for parent, child in self.bdb.cell_child_edges():
            children.setdefault(parent, set()).add(child)
            cells.add(parent)
            cells.add(child)
        comps = self.bdb.all_components()
        by_id = {c.id: c for c in comps}
        containers: set[str] = set()
        for c in comps:
            cells.add(c.cell)
            if not c.is_leaf:
                containers.add(c.cell)
            par = by_id.get(c.parent_id)
            if par is not None and par.cell != c.cell:
                children.setdefault(par.cell, set()).add(c.cell)
        fp = getattr(self, "fp", None)
        if fp is not None:
            for c in comps:
                if c.cell not in containers and fp.is_container(c.name):
                    containers.add(c.cell)
        for row in self.bdb.all_cells():
            cells.add(row.name)

        memo: dict[str, int] = {}

        def level_of(cell, stack):
            if cell in memo:
                return memo[cell]
            if cell in stack:            # malformed cyclic hierarchy
                return 1
            stack.add(cell)
            kids = [k for k in children.get(cell, ()) if k != cell]
            if kids:
                lv = 1 + max(level_of(k, stack) for k in kids)
            else:
                lv = 2 if cell in containers else 1
            stack.discard(cell)
            memo[cell] = lv
            return lv

        return ({c: level_of(c, set()) for c in sorted(cells)}, containers)

    def _cell_share_layers(self, group):
        """{layer_id: share} of `group`'s cell (clone names resolve to the
        base cell); empty when the cell has no fractional shares."""
        shares = getattr(self, "_cell_layer_shares", None) or {}
        cell = self._bu_cell_of(group)
        return {lid: s for (c, lid), s in shares.items()
                if c == cell and s < 1.0}

    def _thinned_pattern(self, pat, share):
        """The derived THINNED TrackPattern of hier_layer_caps.md §6: keep
        the FIRST floor(share x n_signal) SIGNAL slots of the period
        (Q1 resolved: contiguous-first for every cell type), re-type the
        rest CUSTOM (non-routable).  Same origin, widths and spacings —
        unit_pitch and phase congruence are untouched, so align_bottom_up
        and the instance phase machinery never notice."""
        n_sig = sum(1 for s in pat.slots if s.type == "SIGNAL")
        keep = int(share * n_sig + 1e-9)              # floor: budget rounding
        slots, kept = [], 0
        for s in pat.slots:
            t, lbl = s.type, s.label
            if t == "SIGNAL":
                if kept < keep:
                    kept += 1
                else:
                    t, lbl = "CUSTOM", "share_cut"
            slots.append(buda.TrackSlot(type=t, label=lbl,
                                        width=s.width,
                                        space_after=s.space_after))
        return buda.TrackPattern(origin=pat.origin, slots=slots)

    def _cell_share_views(self, group):
        """Tier-1 view pair for a shared cell's cell-local solves
        (hier_layer_caps.md §6): (layers_view, {lid: thinned_pattern}).
        layers_view is a LayerStack clone whose shared layers carry
        bit_pitch = unit_pitch / n_kept — the honest per-bit channel cost
        under the thinned supply (a 30%-declared share on an 8-slot period
        keeps 2, making each bit 4x wider: the economic pressure that keeps
        the cell on its own layers).  No shares => (self.layers, {}) — the
        byte-identity short-circuit."""
        cell_shares = self._cell_share_layers(group)
        if not cell_shares or self.routing_grid is None:
            return self.layers, {}
        layers_view = self.layers.clone()
        thinned = {}
        for lid, s in sorted(cell_shares.items()):
            if not self.routing_grid.has_layer(lid):
                continue
            pat = self.routing_grid.get_layer_grid(lid).global_pattern()
            if not pat.slots:
                continue
            tp = self._thinned_pattern(pat, s)
            n_kept = sum(1 for sl in tp.slots if sl.type == "SIGNAL")
            if n_kept <= 0:
                continue          # declaration-time validation prevents this
            thinned[lid] = tp
            layers_view.set_bit_pitch(lid, pat.unit_pitch() / n_kept)
            # The THINNED view has its own geometry: fewer signal slots per
            # period, so a governed cell resolves its rule against what it
            # was actually leased, not the parent's full grid.
            layers_view.set_ndr_geom(lid, pat.ndr_geom())
        return (layers_view, thinned) if thinned else (self.layers, {})

    def _layer_policy_advisories(self, stage):
        """The LAYER_CAP / LAYER_SHARE advisory of hier_layer_caps.md
        Phase 4 (defense-in-depth, like the keepout audit): report every
        segment whose IN-EFFECT layer lies outside its bundle's band —
        split into UNPINNED violations (should be impossible: the mask is
        enforced in the planner core and every healer path; a nonzero
        count is a bug or a hand-forced state) and PINNED exceptions (the
        documented pins-override-the-mask contract, surfaced so the
        exception is visible, never silent) — plus the share groups over
        their collective lease (§9.7 re-audited at check time).

        The in-effect layer source is stage-aware: the topo stage reads
        the plan's assigned seg_layers; nuts/dnuts read the PLACED
        TrackSegments, so a post-plan escalation (dead-span) is judged by
        where the metal actually ended up.  Returns (unpinned list,
        pinned list, n_share_over); each list entry is a printable
        string.  Ungoverned sessions return empty everything — the
        no-policy print silence the corpus guards."""
        unpinned, pinned = [], []
        # Governed = masked OR share-only (a share without a band leaves
        # allowed_layers empty by design — §4 resolution — but its
        # collective lease must still be re-audited; Codex #549).  The
        # band loop below naturally no-ops for share-only wrappers
        # (allows_layer is vacuously true on an empty mask).
        governed = [w for w in self.bundles
                    if (w.input.allowed_layers or w.input.layer_shares)
                    and w.input.candidates]
        if not governed:
            return [], [], 0
        placed = {}
        if stage in ("nuts", "dnuts") and self.nuts_result is not None:
            for ts in self.nuts_result.segments:
                placed[(ts.bundle_id, ts.seg_idx)] = ts.layer
        for w in governed:
            inp = w.input
            sel = w.plan.selected_topology_index
            if not (0 <= sel < len(inp.candidates)):
                continue
            bid = inp.original_bundle.id
            pins = list(inp.pinned_seg_layers) if inp.pinned_seg_layers else []
            n_segs = len(inp.candidates[sel].segments)
            for si in range(n_segs):
                lid = placed.get((bid, si))
                if lid is None and si < len(w.plan.seg_layers):
                    lid = w.plan.seg_layers[si]
                if lid is None or lid < 0 or inp.allows_layer(lid):
                    continue
                band = (f"[{inp.layer_floor if inp.layer_floor >= 0 else 'min'}"
                        f"..{inp.layer_cap}]")
                msg = (f"Bundle {bid}: Seg {si} on layer L{lid} outside the "
                       f"cell band {band}")
                if si < len(pins) and pins[si] == lid:
                    pinned.append(msg + " (pinned exception — honored)")
                else:
                    unpinned.append(msg)
        with contextlib.redirect_stdout(io.StringIO()):
            n_share_over = self._audit_share_budgets(governed)
        return unpinned, pinned, n_share_over

    def _bu_share_dnuts_overrides(self, ref_ids):
        """Tier-1 DNUTS view (Phase 3): for every bottom-up REFERENCE
        instance whose cell holds fractional shares, the shared layers'
        thinned patterns as instance-bbox override specs
        [(lid, x1, y1, x2, y2, pattern)].  The reference DNUTS engine
        installs them on a grid CLONE, so the reference bits can only land
        on kept slots — the copies then inherit legality (phase-aligned
        siblings share the kept-slot positions) — while every other bundle
        (and the parent over the cell) keeps the full pattern: the share is
        a budget on the CELL, never a reservation against the parent."""
        out = []
        if self.bdb is None or self.routing_grid is None:
            return out
        comps = {c.name: c for c in self.bdb.all_components()}
        seen = set()
        for w in self.bundles:
            b = w.input.original_bundle
            if b.id not in ref_ids or not b.instances:
                continue
            cell_shares = self._cell_share_layers(b.cell_context)
            if not cell_shares:
                continue
            comp = comps.get(b.instances[0])
            if comp is None or not is_placed(comp):
                continue
            for lid, s in sorted(cell_shares.items()):
                key = (b.instances[0], lid)
                if key in seen or not self.routing_grid.has_layer(lid):
                    continue
                seen.add(key)
                pat = self.routing_grid.get_layer_grid(lid).global_pattern()
                if not pat.slots:
                    continue
                out.append((lid, int(round(comp.x1)), int(round(comp.y1)),
                            int(round(comp.x2)), int(round(comp.y2)),
                            self._thinned_pattern(pat, s)))
        return out

    def _audit_share_budgets(self, wrappers):
        """§9.7 defense-in-depth: after planning, sum each share group's
        COMMITTED usage per shared layer (global-basis eff widths — the
        budget's own units) and WARN on any excess.  The STRICT gate makes
        this unreachable on the strict path; ALLOW_OVERFLOW/BEST_EFFORT
        commits can legally exceed and must be visible, never silent."""
        used, budgets = {}, {}
        for w in wrappers:
            inp = w.input
            if not inp.share_group or not inp.layer_shares:
                continue
            sel = w.plan.selected_topology_index
            if not (0 <= sel < len(inp.candidates)) or not w.plan.seg_layers:
                continue
            for lid, b in inp.share_budgets:
                budgets[(inp.share_group, lid)] = b
            shared = {lid for lid, _s in inp.layer_shares}
            t = inp.candidates[sel]
            nbits = len(inp.original_bundle.net_names)
            for si, lid in enumerate(w.plan.seg_layers):
                if lid not in shared or si >= len(t.segments):
                    continue
                n = nbits
                if t.seg_bits and si < len(t.seg_bits) and t.seg_bits[si]:
                    n = len(t.seg_bits[si])
                frac = (n / nbits) if nbits else 1.0
                eff = self.layers.eff_bus_width(
                    n, inp.width * frac, lid)
                key = (inp.share_group, lid)
                used[key] = used.get(key, 0.0) + eff
        n_over = 0
        for key, u in sorted(used.items()):
            b = budgets.get(key, -1.0)
            if b >= 0.0 and u > b + 1e-6:
                grp, lid = key
                print(f"WARNING: [LayerShare] {grp}: committed usage on "
                      f"L{lid} = {u:.1f} exceeds the collective budget "
                      f"{b:.1f} (share x supply in the instance bbox) — a "
                      f"non-STRICT commit spent past the lease; re-plan or "
                      f"raise the share")
                n_over += 1
        return n_over

    def _audit_restored_layer_caps(self):
        """Failure mode 5 of docs/internal/hier_layer_caps.md §9: a cap
        tighter than already-persisted routing (band declared or tightened
        after the checkpoint was routed).  For every wrapper whose mask is
        set, any restored assigned layer OUTSIDE the mask voids that
        bundle's restored plan — reported LOUD per segment and the selection
        cleared, so continuing REQUIRES an explicit re-plan; the persisted
        rows are untouched (a re-run of run_planner rewrites them).
        Returns the set of voided bundle ids."""
        voided = set()
        for w in self.bundles:
            inp = w.input
            if not inp.allowed_layers or w.plan.selected_topology_index < 0:
                continue
            # An above-cap layer the user explicitly PINNED (edit_set_layer +
            # edit_commit pin / restored pinned_seg_layers) is the documented
            # exception — pins override the mask, so a pinned assignment is
            # not stale illegal routing and must resume faithfully
            # (Codex #546).  Only unpinned violations void the plan.
            pins = list(inp.pinned_seg_layers) if inp.pinned_seg_layers else []
            bad, pinned_kept = [], []
            for i, l in enumerate(w.plan.seg_layers):
                if l < 0 or inp.allows_layer(l):
                    continue
                if i < len(pins) and pins[i] == l:
                    pinned_kept.append((i, l))
                else:
                    bad.append((i, l))
            if pinned_kept and not bad:
                b = inp.original_bundle
                det = ", ".join(f"seg{i}=L{l}" for i, l in pinned_kept)
                print(f"[LayerCap] bundle {b.id}: above-cap layer(s) {det} "
                      f"kept — explicitly pinned (pins override the mask)")
            if not bad:
                continue
            b = inp.original_bundle
            name = b.net_names[0] if b.net_names else "?"
            det = ", ".join(f"seg{i}=L{l}" for i, l in bad)
            print(f"WARNING: [LayerCap] bundle {b.id} ({name}): persisted "
                  f"routing violates the cell band [..{inp.layer_cap}] "
                  f"({det}) — restored plan VOIDED; re-run the planner "
                  f"(the cap was added/tightened after this checkpoint "
                  f"was routed)")
            w.plan.selected_topology_index = -1
            w.plan.seg_layers = []
            inp.assigned_h_layer = -1
            inp.assigned_v_layer = -1
            voided.add(int(b.id))
        return voided

    def _bu_cell_of(self, cell_context):
        """Real cell type behind a bundle cell_context — identity unless the
        context is a rotation-class CLONE name (e.g. 'alu90'), in which case
        the marked cell it was cloned for is returned.  Every bottom-up gate
        that asks 'is this template's cell marked?' resolves through here."""
        m = getattr(self, "_bu_clone_cells", None) or {}
        return m.get(cell_context, cell_context)

    def _bu_clone_name(self, cell):
        """Virtual template name for `cell`'s 90°-rotation class: `<cell>90`,
        uniquified with `_1`, `_2`, … against real cell names and every other
        bundle cell_context (live + persisted).  A clone this session (or a
        prior persisted run) already created for `cell` keeps its name."""
        live = getattr(self, "_bu_clone_cells", None) or {}
        for cname, real in live.items():
            if real == cell:
                return cname
        taken, by_id = set(), {}
        rows = self.bdb.all_bundles() if self.bdb is not None else []
        for r in rows:
            by_id[r.id] = r
        for r in rows:
            if r.cloned_from:
                origin = by_id.get(r.cloned_from)
                if origin is not None and origin.cell_context == cell:
                    return r.cell_context     # persisted clone: reuse
                continue                      # other clones get re-created
            if r.cell_context:
                taken.add(r.cell_context)
        if self.bdb is not None:
            taken |= {c.name for c in self.bdb.all_cells()}
        for w in self.bundles:
            if w.input.original_bundle.cell_context:
                taken.add(w.input.original_bundle.cell_context)
        name, i = f"{cell}90", 0
        while name in taken:
            i += 1
            name = f"{cell}90_{i}"
        return name

    def _clone_gen_knobs(self, base, bundle_id):
        """Overlay a bundle's persisted v15 generation-knob memo on `base`
        (the bulk/default (center, double_detour, multi_trunk, hanan_loci,
        spine_relays) tuple), returning the effective clone-generation knobs.
        On the load_pipeline resume path `_hier_gen_knobs` is unset and `base`
        is the all-default fallback, so without this the clone candidates would
        be generated coarser than the pool they mirror (audit P2-02)."""
        if self.bdb is None:
            return base
        memo = self.bdb.bundle_gen_knobs(str(bundle_id))
        if not memo:
            return base
        ks = set(memo.split())
        loci = base[3]
        if "no_hanan_loci" in ks:
            loci = False
        elif "hanan_loci" in ks:
            loci = True
        return (base[0] or "center_mode" in ks,
                base[1] or "double_detour" in ks,
                base[2] or "multi_trunk" in ks,
                loci,
                base[4] or "spine_relays" in ks)

    def _split_bottom_up_rotation_classes(self):
        """Give each marked cell's 90°-rotated instance class its OWN
        template — the rotation-class CLONE.  Within the 90° family
        (W/E/FW/FE) any two instances differ by a direction-preserving
        transform, so once the class has its own reference instance the
        whole existing copy machinery (local solve → transform copies)
        applies unchanged; the H↔V 'layer pairing' question disappears
        because the clone's candidates are generated FROM the rotated
        reference's actual cell-local floorplan and planned with real
        per-direction layer costs.

        Runs at `run_planner hier` time (marks can arrive any time before
        planning).  Class membership is decided against ONE per-cell
        reference (the first template's first instance) — never against
        each template's own first instance, which would leave a
        rotated-only bundle, or one whose instance list happens to start
        with a rotated instance, in the wrong class (Codex #253).  Per
        template of the cell: instances of the other class move to a new
        clone HBundle (cell_context = the virtual clone name from
        _bu_clone_name, provenance in self._bu_clone_cells /
        _bu_clone_from and persisted as bundle.cloned_from, v19), donor
        replicas are re-keyed, and candidates are (re)generated from the
        owning side's reference with the last hier-generation knobs; a
        template whose instances ALL belong to the other class is
        re-contexted wholesale (same id, nets, and donors).  Idempotent: a
        second run finds no cross-class instances on any template.

        The clone is a routing-template identity only — cell/component/pin
        tables never see it, so GDS/DEF/Verilog interchange is unaffected.
        Returns the number of clone-class templates created/reassigned."""
        if self.bdb is None or not self.bundles:
            return 0
        bu = set(self.bdb.bottom_up_cells())
        if not bu:
            return 0
        if getattr(self, "_bu_clone_cells", None) is None:
            self._bu_clone_cells = {}
        if getattr(self, "_bu_clone_from", None) is None:
            self._bu_clone_from = {}
        comps = list(self.bdb.all_components())
        comps_by_name = {c.name: c for c in comps}
        cell_ids = {w.input.original_bundle.id for w in self.bundles
                    if w.input.original_bundle.cell_context}
        replicas = {}          # template id → [replica wrappers]
        for w in self.bundles:
            b = w.input.original_bundle
            if b.cell_context and b.parent_id in cell_ids and b.instances:
                replicas.setdefault(b.parent_id, []).append(w)
        next_id = max((w.input.original_bundle.id
                       for w in self.bundles), default=-1) + 1
        # Generation knobs remembered from generate_hier_topologies; the
        # fallback keeps hanan_loci ON (its post-flip default) and spine
        # relays OFF (its opt-in default).
        knobs = getattr(self, "_hier_gen_knobs",
                        (False, False, False, True, False))
        ocache, fp_cache = {}, {}

        # Pass 1 — classify per cell_context against the per-cell
        # canonical reference.  plans: template id → action tuple.
        by_ctx = {}
        for w in self.bundles:
            b = w.input.original_bundle
            if (b.cell_context
                    and self._bu_cell_of(b.cell_context) in bu
                    and b.parent_id not in cell_ids       # not a replica
                    and b.instances):
                by_ctx.setdefault(b.cell_context, []).append(w)
        clone_names = {}       # real cell → clone name (one per cell)
        plans = {}             # template id → (keep, rot, cname, bref, cell,
                               #                origin_tid)
        for ctx, tws in by_ctx.items():
            cell = self._bu_cell_of(ctx)
            cell_ref = tws[0].input.original_bundle.instances[0]
            orients = self._detect_instance_orients(
                cell, comps, ref_name=cell_ref, cache=ocache)

            def is_rot(i, _o=orients):
                o = _o.get(i)
                return o is not None and o not in _DIR_PRESERVING

            rot_all = sorted({i for tw in tws
                              for i in tw.input.original_bundle.instances
                              if is_rot(i)})
            if not rot_all:
                continue
            cname = clone_names.get(cell)
            if cname is None:
                cname = clone_names[cell] = self._bu_clone_name(cell)
                self._bu_clone_cells[cname] = cell
            bref = rot_all[0]      # clone-class canonical reference
            # cloned_from must point at a row that STAYS under the real
            # context (the loader resolves clone → origin → real cell);
            # the first template always keeps cell_ref, so it qualifies.
            origin_tid = next(
                tw.input.original_bundle.id for tw in tws
                if any(not is_rot(i)
                       for i in tw.input.original_bundle.instances))
            for tw in tws:
                b = tw.input.original_bundle
                rot = [i for i in b.instances if is_rot(i)]
                if rot:
                    plans[b.id] = ([i for i in b.instances if i not in rot],
                                   rot, cname, bref, cell, origin_tid)

        # Pass 2 — apply, preserving self.bundles order (clones inserted
        # right after their template).
        result, made = [], 0
        for w in self.bundles:
            b = w.input.original_bundle
            result.append(w)
            plan = plans.get(b.id) if b.parent_id not in cell_ids else None
            if plan is None or not b.cell_context:
                continue
            keep, rot, cname, bref, cell, origin_tid = plan
            # Per-template generation knobs: overlay this template's persisted
            # v15 memo on the bulk/default base, so a load_pipeline resume (no
            # _hier_gen_knobs set — generate_hier_topologies never ran this
            # session) still generates the clone candidates with the same
            # knobs the original pool used, instead of silently falling back
            # to all-defaults (audit P2-02).
            bk = self._clone_gen_knobs(knobs, b.id)
            # Clone-side instance order: the class reference first (when
            # this bundle has it), so the group solves share one frame.
            order_rot = ([bref] + sorted(i for i in rot if i != bref)
                         if bref in rot else sorted(rot))
            if keep:
                clone = self._clone_hbundle_with_id(b, next_id)
                next_id += 1
                clone.cell_context = cname
                clone.instances = order_rot
                ref_moved = b.instances[0] in rot
                b.instances = keep
                # Move the clone-class donors (replica wrappers); the
                # clone's own nets are its reference instance's donor nets.
                for r in replicas.get(b.id, []):
                    rb = r.input.original_bundle
                    if rb.instances and rb.instances[0] in rot:
                        rb.parent_id = clone.id
                        rb.cell_context = cname
                        if rb.instances[0] == order_rot[0]:
                            clone.net_names = list(rb.net_names)
                self._bu_clone_from[clone.id] = b.id
                nw = buda.BundleWrapper()
                nw.input.original_bundle = clone
                nw.input.width = w.input.width
                # A governed template's rotation-class clone keeps the rule
                # (R2d — same nets, same class, different orientation).
                nw.input.ndr = w.input.ndr
                result.append(nw)
                made += 1
                print(f"[BottomUp] cell '{cell}': 90°-rotated instance "
                      f"class ({', '.join(order_rot)}) split into clone "
                      f"template '{cname}' (bundle {clone.id}, "
                      f"ref {order_rot[0]})")
                # Candidates generated from the owning side's reference —
                # real per-direction layer costs on the class's actual
                # cell-local floorplan.
                self._generate_hier_topo_one(nw, bk[0], bk[1],
                                             fp_cache, comps_by_name,
                                             bk[2],
                                             use_hanan_loci=bk[3],
                                             use_spine_relays=bk[4])
                if ref_moved:
                    # The kept side lost the instance its candidates were
                    # generated from — regenerate in its new frame.
                    self._generate_hier_topo_one(w, bk[0], bk[1],
                                                 fp_cache, comps_by_name,
                                                 bk[2],
                                                 use_hanan_loci=bk[3],
                                                 use_spine_relays=bk[4])
            else:
                # Rotated-only template: the WHOLE bundle belongs to the
                # clone class — re-context in place (same id, nets, and
                # donors), anchoring on the class reference when present.
                old_ref = b.instances[0]
                b.cell_context = cname
                b.instances = order_rot
                for r in replicas.get(b.id, []):
                    r.input.original_bundle.cell_context = cname
                self._bu_clone_from[b.id] = origin_tid
                made += 1
                print(f"[BottomUp] cell '{cell}': rotated-only bundle "
                      f"{b.id} reassigned to clone template '{cname}'")
                if order_rot[0] != old_ref:
                    # Its candidates were generated from old_ref's frame —
                    # regenerate from the class reference.
                    self._generate_hier_topo_one(w, bk[0], bk[1],
                                                 fp_cache, comps_by_name,
                                                 bk[2],
                                                 use_hanan_loci=bk[3],
                                                 use_spine_relays=bk[4])
        if not made:
            return 0
        # P2: C++-backed container; _hier_bundles_orig then holds element
        # REFERENCES into it, so the template solve's pin write-back and the
        # snapshot stay one storage.
        self.bundles = buda.BundleWrapperVec(result)
        self._hier_bundles_orig = list(self.bundles)
        # Re-persist bundles + candidates: _persist_topologies rewrites the
        # bundle rows from self.bundles (clone rows carry cloned_from via
        # _bu_clone_from, re-keyed replicas their new parent, templates
        # their shrunken instance lists) and then the candidate pools.
        nt = self._persist_topologies()
        if nt:
            print(f"[BDB] persisted the clone template(s) + "
                  f"{nt} candidate topolog{'y' if nt == 1 else 'ies'}.")
        return made

    def _apply_hier_refine_default(self, planner):
        """HIER planning defaults `refine_passes` to 1 (the level-ordering
        synthesis, docs/internal/refine_passes_default.md — decision
        2026-07-12): the hier corpus measured only wins or exact no-ops
        (hbundles/01 WL −21%, 02 −32%, 05 opens 47→32, 10 heals to 0/0;
        mix heals 1/0 → 0/0 and its heal loops converge 9× faster), while
        the FLAT path stays at the C++ default 0 — big2-class plain flows
        regressed under refinement (the pre-charge-horizon class).  An
        explicit `set_planner_param refine_passes <n>` (including 0)
        always wins.  Applied at EVERY hier planner construction site
        (run_planner hier, ripup's _rr_replan_hier fallback, the
        bottom-up cell-local template planner) so ripup trials and
        template solves run the same configuration that produced the
        committed state — the configuration the corpus was measured
        with."""
        if "refine_passes" not in self._planner_params:
            planner.set_planner_param("refine_passes", 1.0)

    def _check_bottom_up_frame(self, wrappers, comps, cell):
        """Guard the single-frame assumption of a bottom-up cell's group solve
        (audit P1-03).  Each template's candidates are generated in its OWN
        instances[0] cell-local frame, but the group solve builds ONE floorplan
        from wrappers[0].instances[0] and orientation-transforms every copy
        relative to it.  A template whose reference has a DIFFERENT orientation
        (a bus present only in a mirrored/rotated instance subset — S/FN/FS are
        NOT split into rotation-class clones) would therefore be charged and
        placed in the wrong frame and DOUBLE-transformed on copy, landing its
        fixed segments at wrong absolute coordinates (a silent open).  Same
        orientation but a different instance is fine — the origin-normalized
        cell-local floorplan is identical — so the check keys on ORIENTATION,
        not instance identity.  Fail LOUD rather than emit misplaced copies."""
        if len(wrappers) < 2:
            return
        ref_inst = wrappers[0].input.original_bundle.instances[0]
        orients = self._detect_instance_orients(
            self._bu_cell_of(cell), comps, ref_name=ref_inst)
        ref_o = orients.get(ref_inst, "N")
        for w in wrappers[1:]:
            wi = w.input.original_bundle.instances[0]
            wo = orients.get(wi, "N")
            if wo != ref_o:
                raise RuntimeError(
                    f"bottom-up cell '{cell}': template bundle "
                    f"{w.input.original_bundle.id} generated its candidates from "
                    f"instance '{wi}' (orient {wo!r}), a different cell-local "
                    f"frame than the group reference '{ref_inst}' (orient "
                    f"{ref_o!r}).  Solving them together would misplace the "
                    f"copied routing (audit P1-03).  Give the cell's internal "
                    f"buses a uniform reference orientation, or unset "
                    f"set_bottom_up for this cell to route it top-down.")

    def _plan_bottom_up_templates(self, iterations):
        """Stage (a) of bottom-up template planning: solve each marked cell's
        cell-local template bundles ONCE in a dedicated cell-local planner,
        then pin the winning candidate index + per-segment layers on the
        template wrapper.  _expand_hier_bundles propagates the pin to every
        instance (and marks them hier.locked), so all instances carry one
        uniform, locally-optimal assignment decided by intra-cell congestion
        only — independent of any instance's surroundings.

        Runs BEFORE expansion, on the pre-expansion template wrappers whose
        candidates are still in cell-local coordinates.  A template the user
        already pinned keeps its pin (the local solve only assigns layers).
        Uses the WIDTH capacity model regardless of the global run's
        signal_tracks opt-in: the local frame's coordinates do not align
        with the absolute-coordinate routing grid, so track counting there
        would sample the wrong windows.  Deepest cells solve first (nested
        bottom-up cells resolve before their parents).

        See docs/internal/hier_bottom_up_planning.md §3.
        """
        # A re-plan invalidates any cached bottom-up local NUTS solve and
        # the track-alignment verdict derived from it; adopted template
        # doglegs are dropped so the local planner re-decides from
        # pristine candidates (instance slots are cleared by the
        # planner-time _reset_doglegs).  The resumed-routing preference is
        # over too: this re-plan's local solve is authoritative now.
        self._bu_fixed_cache = None
        self._template_track_verdict = None
        self._bu_dnuts_plan_cache = None
        self._bu_fixed_from_resume = False
        self._reset_bottom_up_doglegs()
        bu_cells = set(self.bdb.bottom_up_cells()) if self.bdb else set()
        if not bu_cells or not self.bundles:
            return
        # Templates the USER pinned before the local solve (the solve keeps
        # those pins and only assigns layers).  Healer template-class moves
        # must never override an explicit user choice — record provenance
        # here, before the solve below sets topology_pinned on everything.
        self._bu_user_pinned = {w.input.original_bundle.id
                                for w in self.bundles
                                if w.input.topology_pinned}
        # The local-solve iteration budget, reused by a healer class trial's
        # per-cell re-plan so the trial runs under the same objective.
        self._bu_local_iterations = iterations
        # Same replica detection as _expand_hier_bundles: a replica's routing
        # is carried by its template's per-instance expansion, so only the
        # template participates in the local solve.
        cell_ids = {w.input.original_bundle.id for w in self.bundles
                    if w.input.original_bundle.cell_context}
        by_cell = {}
        for w in self.bundles:
            b = w.input.original_bundle
            if (not b.cell_context or not b.instances
                    or self._bu_cell_of(b.cell_context) not in bu_cells
                    or b.parent_id in cell_ids       # replica
                    or not w.input.candidates):
                continue
            by_cell.setdefault(b.cell_context, []).append(w)
        deepest_first = sorted(
            by_cell,
            key=lambda c: -max(w.input.original_bundle.level
                               for w in by_cell[c]))
        for cell in deepest_first:
            self._plan_bottom_up_cell(cell, by_cell[cell], iterations)

    def _plan_bottom_up_cell(self, cell, wrappers, iterations, persist=True,
                             inject=()):
        """One cell's local template solve — the per-cell body of
        _plan_bottom_up_templates, factored so a healer template-class trial
        (ripup class moves) can re-plan a single cell with a forced pin and
        WITHOUT the BDB persist (persist=False; the accept path persists via
        _persist_bottom_up_cell_decision).  A wrapper whose topology_pinned
        is set keeps its pin — the planner only assigns layers for it.

        `inject` (negotiate v2 price translation): CELL-FRAME measured-
        congestion demand records `(layer_id, span_lo, span_hi, perp_lo,
        perp_hi, amount)` — each instance's injected global band demand
        translated into cell coordinates by _translate_injections_to_cell
        and summed here across instances — applied to the local planner
        before the solve, so an UNPINNED template's selection is steered by
        the aggregated multi-instance congestion field instead of intra-cell
        congestion alone.  The grid is pre-extended from the candidates
        first (extend_grid_for) because the optimizer's own extension
        rebuilds the cuts and would wipe the injections."""
        self._check_bottom_up_frame(wrappers, None, cell)
        fp = self._build_cell_local_floorplan(
            wrappers[0].input.original_bundle.instances[0])
        if fp is None:
            print(f"WARNING: bottom-up cell '{cell}': no placed instance "
                  f"to derive the cell-local floorplan — skipped")
            return
        # Tier-1 share view (Phase 3): a shared cell's local solve prices
        # its shared layers at the THINNED bit pitch (unit_pitch / n_kept)
        # via a derived LayerStack — the width model then charges the honest
        # per-bit channel cost of the leased slice.  layers_view must
        # outlive the planner (it holds a reference) — both are locals of
        # this call.  No shares => self.layers, byte-identical.
        layers_view, thinned = self._cell_share_views(cell)
        if thinned:
            print(f"[LayerShare] cell '{cell}': local solve under thinned "
                  f"view on layer(s) {sorted(thinned)}")
        planner = buda.CongestionPlanner(fp, layers_view)
        for pname, pval in self._planner_params.items():
            planner.set_planner_param(pname, pval)
        # Same healer-gate declaration as the global planner (Codex
        # #342): the cell-local template solve must run under the SAME
        # objective, or expansion locks in selections a suppressed env
        # default made differently.
        self._apply_healers_ahead(planner)
        self._apply_hier_refine_default(planner)
        planner.set_track_pitch(self._nuts_pitch)
        planner.build_congestion_map()
        if inject:
            planner.extend_grid_for(wrappers)
            for (lid, s_lo, s_hi, p_lo, p_hi, amt) in inject:
                planner.inject_band_demand(lid, s_lo, s_hi, p_lo, p_hi, amt)
        # Opt-in kWLSpread: the local planner is seeded from
        # _planner_params above, so it WILL apply the spread term —
        # stamp the templates' envelopes against the cell-local
        # floorplan it plans in, or every candidate would fall back to
        # the nominal here and expansion would lock that choice in.
        if self._planner_params.get("kWLSpread", -1.0) >= 0.0:
            self._annotate_wl_envelopes(wrappers, fp=fp)
        assignments = planner.optimize_topologies(wrappers, iterations)
        # The local NUTS solve must see the same candidate-extended
        # Hanan grid the local planner charged (exactly as run_nuts
        # hands the global planner's grid to the global engine) — the
        # grid defines each segment's interval constraint, and a
        # coarser one lets contending trunks drift apart instead of
        # surfacing the overlap the dogleg pass classifies.
        if getattr(self, "_bu_planner_grids", None) is None:
            self._bu_planner_grids = {}
        self._bu_planner_grids[cell] = (list(planner.get_x_grid()),
                                        list(planner.get_y_grid()))
        bid_to_w = {w.input.original_bundle.id: w for w in wrappers}
        for asn in assignments:
            w = bid_to_w.get(asn.bundle_id)
            if w is None:
                continue
            w.plan.selected_topology_index = asn.topo_index
            w.plan.seg_layers = list(asn.seg_layers)
            # Mirror the flat planner handoff (planner_cmds): the local
            # NUTS honors the local planner's charged-band centres, so
            # contention materializes as raw overlaps (the dogleg pass's
            # cycle detection needs them) instead of trunks scattering.
            w.plan.seg_perp = list(asn.seg_perp)
            w.input.assigned_v_layer = asn.v_layer_id
            w.input.assigned_h_layer = asn.h_layer_id
            w.input.topology_pinned = True
            w.input.pinned_seg_layers = list(asn.seg_layers)
        if persist:
            self._persist_bottom_up_cell_decision(wrappers)
        n_inst = len(wrappers[0].input.original_bundle.instances)
        print(f"[BottomUp] cell '{cell}': {len(assignments)} template "
              f"bundle(s) planned locally; decision pinned for "
              f"{n_inst} instance(s)")

    def _persist_bottom_up_cell_decision(self, wrappers):
        """Persist the templates' own decisions (is_selected + assigned
        layers on the TEMPLATE bundle rows) — the canonical record of the
        local solve, so a resumed session re-enters bottom-up with the same
        pinned assignment instead of re-deciding.  Called by the local solve
        (persist=True) and by an ACCEPTED healer template-class move (whose
        trial re-plan ran with persist=False)."""
        if self.bdb is None:
            return
        persisted_ids = {b.id for b in self.bdb.all_bundles()}
        for w in wrappers:
            bid = str(w.input.original_bundle.id)
            sel = w.plan.selected_topology_index
            if bid in persisted_ids and sel >= 0:
                self.bdb.set_topology_selected(bid, sel)
                self.bdb.reset_assigned_layers(bid)
                self._persist_assigned_layers(bid, sel, w)

    def _bottom_up_fixed_segments(self):
        """Stage (b) of bottom-up template planning: solve each marked cell's
        template bundles ONCE with a cell-local NUTSEngine (cell-local
        coordinates, same track pitch) and return the per-instance translated
        copies as already-placed TrackSegments.  The caller registers them
        with the global engine via add_fixed_segments, which (a) skips those
        bundles in extraction, (b) blocks every solver pass with each copy's
        physical extent, and (c) appends the copies to the result — so all
        instances carry one uniform, locally-optimal track layout and every
        other bundle detours it.

        Cached on the session (ripup/negotiate re-run NUTS many times; the
        local solve depends only on the pinned template plan, which locked
        wrappers never change).  _plan_bottom_up_templates resets the cache
        on every re-plan.

        A template whose local solve required a DOGLEG split is copied like
        any other: the split is adopted on the template and every locked
        instance (_adopt_bottom_up_doglegs), so copies and selected
        candidates agree segment-for-segment.
        See docs/internal/hier_bottom_up_planning.md §4.
        """
        cache = getattr(self, "_bu_fixed_cache", None)
        if cache is not None:
            return cache
        # Publish the cache only on SUCCESS (audit P2-01): an exception during
        # the local NUTS solve / dogleg-adoption loop below must not leave a
        # permanently-cached PARTIAL (or empty) list that every later call
        # returns silently.  Compute into a local, cache at the end / on
        # exception reset to None.
        try:
            fixed = self._bottom_up_fixed_segments_compute()
        except BaseException:
            self._bu_fixed_cache = None
            raise
        self._bu_fixed_cache = fixed
        return fixed

    def _bottom_up_fixed_segments_compute(self):
        """The uncached body of _bottom_up_fixed_segments (audit P2-01)."""
        fixed = []
        if not getattr(self, "_planner_is_hier", False) or self.bdb is None:
            return fixed
        bu_cells = set(self.bdb.bottom_up_cells())
        templates = getattr(self, "_hier_bundles_orig", None) or []
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        if not bu_cells or not exp_map:
            return fixed
        if getattr(self, "_bu_fixed_from_resume", False):
            # Resumed session (load_pipeline expanded), no re-plan since:
            # when the checkpoint was taken AFTER run_nuts, the persisted
            # NUTS result already CONTAINS the exact fixed copies — prefer
            # them over a fresh local solve (which could legally differ:
            # the local planner's band centres and extended grid are not
            # persisted).  A PRE-run_nuts checkpoint has no routing rows —
            # fall through to the local solve on the RESTORED template
            # wrappers (_restore_bottom_up_templates), which is the whole
            # point of restoring them: uniform copies instead of the old
            # per-instance fallback.
            locked_ids = {w.input.original_bundle.id for w in self.bundles
                          if w.hier.locked}
            if locked_ids and self.nuts_result is not None:
                fixed.extend(ts for ts in self.nuts_result.segments
                             if ts.bundle_id in locked_ids and ts.placed)
            if fixed:
                print(f"[BottomUp] resume: {len(fixed)} fixed segment(s) "
                      f"restored from the persisted NUTS routing")
                return fixed
            fixed = []
        if not templates:
            locked_ids = {w.input.original_bundle.id for w in self.bundles
                          if w.hier.locked}
            if not locked_ids:
                return fixed
            print("WARNING: bottom-up locked instances present but no "
                  "template wrappers and no persisted NUTS routing — "
                  "uniform NUTS copies are unavailable on this resume; "
                  "re-run from run_planner hier in a full session")
            return fixed
        comps = {c.name: c for c in self.bdb.all_components()}
        cell_ids = {w.input.original_bundle.id for w in templates
                    if w.input.original_bundle.cell_context}
        by_cell = {}
        for w in templates:
            b = w.input.original_bundle
            if (not b.cell_context or not b.instances
                    or self._bu_cell_of(b.cell_context) not in bu_cells
                    or b.parent_id in cell_ids            # replica
                    or not w.input.topology_pinned
                    or not w.input.pinned_seg_layers
                    or not w.input.candidates
                    or not (0 <= w.plan.selected_topology_index
                            < len(w.input.candidates))):
                continue
            by_cell.setdefault(b.cell_context, []).append(w)
        for cell in sorted(by_cell):
            wrappers = by_cell[cell]
            self._check_bottom_up_frame(wrappers, comps.values(), cell)
            fp = self._build_cell_local_floorplan(
                wrappers[0].input.original_bundle.instances[0])
            if fp is None:
                continue
            # Tier-1 share view: the local NUTS widths must match what the
            # local planner charged (same derived LayerStack; locals keep
            # it alive through the run).
            layers_view, _thin = self._cell_share_views(cell)
            nuts = buda.NUTSEngine(fp, layers_view)
            nuts.set_track_pitch(self._nuts_pitch)
            # The local planner's candidate-extended Hanan grid (mirrors
            # run_nuts handing the global planner's grid to the global
            # engine — the grid defines interval constraints).
            gx, gy = (getattr(self, "_bu_planner_grids", None)
                      or {}).get(cell, ([], []))
            if gx or gy:
                nuts.set_extra_grid_points(gx, gy)
            with buda.ostream_redirect():
                local = nuts.run(wrappers)
            # Per-instance orientations relative to the template's reference
            # (whose cell-local layout the local solve just placed).  `cell`
            # may be a rotation-class clone name — detection needs the REAL
            # cell type (the clone's instances are dir-preserving relative
            # to the clone reference, so the copies transform as usual).
            ref_inst = wrappers[0].input.original_bundle.instances[0]
            orients = self._detect_instance_orients(
                self._bu_cell_of(cell), comps.values(), ref_name=ref_inst)
            ref_c = comps.get(ref_inst)
            cw = int(round(ref_c.x2 - ref_c.x1)) if ref_c else 0
            ch = int(round(ref_c.y2 - ref_c.y1)) if ref_c else 0
            if local.dogleg_topologies:
                # The local solve needed a dogleg split: adopt the split
                # topology on the TEMPLATE (cell-local frame) and on every
                # locked instance (orientation-transformed) so the copied
                # routing below — which already carries the split geometry —
                # matches every wrapper's selected candidate downstream
                # (DNUTS handoff, verification, persistence).
                self._adopt_bottom_up_doglegs(cell, wrappers, local,
                                              exp_map, comps, orients,
                                              cw, ch)
            by_tid = {}
            for ts in local.segments:
                by_tid.setdefault(ts.bundle_id, []).append(ts)
            n_copied = 0
            for w in wrappers:
                tid = w.input.original_bundle.id
                for iw in exp_map.get(tid, []):
                    if not iw.hier.locked:
                        continue
                    inst = iw.input.original_bundle.instances[0]
                    parent = comps.get(inst)
                    if parent is None:
                        continue
                    oi = orients.get(inst) or "N"
                    dx = int(round(parent.x1))
                    dy = int(round(parent.y1))
                    bid = iw.input.original_bundle.id
                    for ts in by_tid.get(tid, []):
                        # Cell frame (src 0,0) → instance frame, through the
                        # instance's orientation (locked cells are guarded to
                        # the direction-preserving set at expansion).
                        fixed.append(buda.transform_track_segment(
                            ts, oi, cw, ch, 0, 0, dx, dy, bid))
                        n_copied += 1
            print(f"[BottomUp] cell '{cell}': local NUTS placed "
                  f"{len(local.segments)} segment(s), "
                  f"{local.num_overlaps} overlap(s); copied {n_copied} "
                  f"fixed segment(s) to instances")
        return fixed

    def _adopt_bottom_up_doglegs(self, cell, wrappers, local, exp_map,
                                 comps, orients, cw, ch):
        """Adopt the LOCAL solve's dogleg splits so doglegged templates are
        copied like any other (the old behavior unlocked the cell and fell
        back to per-instance global NUTS).  Mirrors `_adopt_doglegs` (the
        flat run_nuts adoption), sourced from the cell-local NUTSResult:

        - TEMPLATE wrapper (cell-local frame): the split topology is
          appended as a new candidate (or overwrites the same slot on a
          re-solve — `_bu_dogleg_slot` bookkeeping, reset on every re-plan
          by `_reset_bottom_up_doglegs`), selected, and fully pinned with
          the split's per-segment layers + placement overrides
          (net_pull/perp/slides) so a re-solve reproduces the split
          stably.
        - Every LOCKED instance wrapper: the split topology transformed
          through the instance's orientation (the same
          `transform_topology` the expansion copies use), appended +
          selected + fully pinned with the split's layers.  Slot
          bookkeeping rides the session's `_dogleg_slot`/`_dogleg_originals`
          (integer bundle ids are unique across expansion), so the
          planner-time `_reset_doglegs` cleans instance slots exactly like
          flat adoptions; the placement-steering overrides stay empty —
          the instance's geometry is fixed (add_fixed_segments), never
          re-placed.

        The fixed copies the caller then transforms from `local.segments`
        already carry the split geometry, so copies and candidates agree
        segment-for-segment — the invariant the DNUTS handoff
        (make_bus_segments' per-segment analysis) relies on."""
        if getattr(self, "_bu_dogleg_slot", None) is None:
            self._bu_dogleg_slot = {}
            self._bu_dogleg_originals = {}
        dl = local.dogleg_topologies
        for w in wrappers:
            tid = w.input.original_bundle.id
            if tid not in dl:
                continue
            layers = list(local.dogleg_seg_layers[tid])
            # Template adoption (cell-local frame).
            cands = w.input.candidates            # pybind copy
            slot = self._bu_dogleg_slot.get(tid)
            if slot is not None and 0 <= slot < len(cands):
                cands[slot] = dl[tid]             # re-solve: same slot
            else:
                self._bu_dogleg_originals[tid] = \
                    w.plan.selected_topology_index
                cands.append(dl[tid])
                slot = len(cands) - 1
                self._bu_dogleg_slot[tid] = slot
            w.input.candidates = cands
            w.plan.selected_topology_index = slot
            w.plan.seg_layers = layers
            w.input.topology_pinned = True
            w.input.pinned_seg_layers = list(layers)
            np_ = local.dogleg_seg_net_pull
            if tid in np_:
                w.plan.seg_net_pull = list(np_[tid])
            sp = local.dogleg_seg_perp
            if tid in sp:
                w.plan.seg_perp = list(sp[tid])
            slo, shi = local.dogleg_seg_slide_lo, local.dogleg_seg_slide_hi
            if tid in slo:
                w.plan.seg_slide_lo = list(slo[tid])
                w.plan.seg_slide_hi = list(shi[tid])
            # Per-instance adoption (transformed frames).
            n_inst = 0
            for iw in exp_map.get(tid, []):
                if not iw.hier.locked:
                    continue
                inst = iw.input.original_bundle.instances[0]
                parent = comps.get(inst)
                if parent is None:
                    continue
                oi = orients.get(inst) or "N"
                dx = int(round(parent.x1))
                dy = int(round(parent.y1))
                t = (buda.transform_topology(dl[tid], oi, cw, ch, dx, dy,
                                             inst)
                     if oi != "N"
                     else buda.offset_topology(dl[tid], dx, dy, inst))
                bid = iw.input.original_bundle.id
                icands = iw.input.candidates
                islot = self._dogleg_slot.get(bid)
                if islot is not None and 0 <= islot < len(icands):
                    icands[islot] = t
                else:
                    self._dogleg_originals[bid] = \
                        iw.plan.selected_topology_index
                    icands.append(t)
                    islot = len(icands) - 1
                    self._dogleg_slot[bid] = islot
                iw.input.candidates = icands
                iw.plan.selected_topology_index = islot
                iw.plan.seg_layers = list(layers)
                iw.input.topology_pinned = True
                iw.input.pinned_seg_layers = list(layers)
                n_inst += 1
                # Persist the instance's adopted split as its selected
                # topology (run_planner hier persisted the PRE-split
                # selection; the run_nuts bus rows will carry the split's
                # segment indices, and a load_pipeline-expanded resume
                # must restore matching candidates — Codex #256).
                # NEVER inside a healer trial: a class-move trial recomputes
                # the fixed cache (and hence may re-adopt) mid-trial, and a
                # rejected trial's rows must not reach the BDB — the accept
                # path recomputes the cache OUTSIDE the trial flag, so the
                # committed adoption persists then (idempotent upserts).
                if self.bdb is not None \
                        and not getattr(self, '_rr_in_trial', False):
                    self._add_expanded_bundle(iw, islot, {bid: tid})
                    self.bdb.set_topology_selected(str(bid), islot)
            # Same for the TEMPLATE row (the canonical local-solve record
            # a pre-expansion resume re-enters bottom-up with) — same
            # trial guard as above.
            if self.bdb is not None \
                    and not getattr(self, '_rr_in_trial', False):
                self._persist_template_dogleg(w, tid, slot, layers)
            print(f"[BottomUp] cell '{cell}': local NUTS split template "
                  f"bundle {tid} with a dogleg — split candidate adopted "
                  f"on the template and {n_inst} locked instance(s)")

    def _translate_injections_to_cell(self, cell, wrappers, inj_recs):
        """Negotiate v2 price translation (docs/internal/
        bottomup_healer_templates.md item D): map GLOBAL-frame injected
        band-demand records `(layer_id, span_lo, span_hi, perp_lo, perp_hi,
        amount)` into the CELL-LOCAL frame the template plans in, one
        translated record per (instance x intersecting record) — injecting
        them all into one local planner SUMS the demand across instances,
        so the local solve prices the aggregated multi-instance congestion
        field.

        Per instance: the record's geometric rectangle (layer direction
        decides which window is x and which is y) is clipped to the
        instance bbox (demand outside the cell footprint cannot be felt by
        cell-local geometry), then mapped through the INVERSE instance
        transform.  Locked classes are direction-preserving by the
        expansion guard, so the orientation maps are the involutions
        N/S/FN/FS over the reference box (`orient_map` in topology.cpp:
        S = (w-x, h-y), FN = (x, h-y), FS = (w-x, y)) — a 90-degree family
        plans via its own rotation-class clone template whose instance
        orients are again direction-preserving relative to the clone
        reference.  Layer ids are frame-independent."""
        if not inj_recs or self.bdb is None or not wrappers:
            return []
        comps = {c.name: c for c in self.bdb.all_components()}
        # De-duplicated UNION of every template's instance list — cell-local
        # templates normally share one list, but a template whose occurrences
        # are a different subset must still have contention around ITS
        # instances priced (Codex #478: first-wrapper-only would silently
        # skip an omitted occurrence's surroundings).  Order-preserving so
        # the reference instance (wrappers[0]'s first — the frame
        # _build_cell_local_floorplan derives from) stays first.
        insts, seen = [], set()
        for w in wrappers:
            for inst in w.input.original_bundle.instances:
                if inst not in seen:
                    seen.add(inst)
                    insts.append(inst)
        if not insts:
            return []
        ref_inst = insts[0]
        ref_c = comps.get(ref_inst)
        if ref_c is None:
            return []
        orients = self._detect_instance_orients(
            self._bu_cell_of(cell), comps.values(), ref_name=ref_inst)
        cw = int(round(ref_c.x2 - ref_c.x1))
        ch = int(round(ref_c.y2 - ref_c.y1))
        h_layers = set(self.layers.get_layer_ids_by_dir(
            buda.LayerDir.HORIZONTAL))
        # Coalesce identical translated windows (review #478 suggestion 1):
        # congruent instances frequently map contention at the same
        # relative position to the SAME cell-frame rectangle — the summing
        # intent — so merging duplicate (layer, windows) keys into one
        # record with the summed amount is arithmetically identical to
        # injecting each separately (add_usage is linear) and cuts the
        # per-record for_each_band walks, the pass's dominant cost on a
        # many-open stage-b iteration.  Order-preserving for determinism.
        agg, order = {}, []
        for inst in insts:
            c = comps.get(inst)
            if c is None:
                continue
            oi = orients.get(inst) or "N"
            if oi not in ("N", "S", "FN", "FS"):
                continue          # guarded away at expansion; belt-and-braces
            dx, dy = int(round(c.x1)), int(round(c.y1))
            for (lid, s_lo, s_hi, p_lo, p_hi, amt) in inj_recs:
                horiz = lid in h_layers
                gx1, gx2 = (s_lo, s_hi) if horiz else (p_lo, p_hi)
                gy1, gy2 = (p_lo, p_hi) if horiz else (s_lo, s_hi)
                # Clip to the instance bbox — the REFERENCE dims are every
                # instance's dims: a locked class is congruent by the
                # expansion guard (direction-preserving orients over the
                # same outline), which is also what makes the reflection
                # below use cw/ch soundly for all of them.
                cx1, cx2 = max(gx1, dx), min(gx2, dx + cw)
                cy1, cy2 = max(gy1, dy), min(gy2, dy + ch)
                if cx1 > cx2 or cy1 > cy2:
                    continue
                # Instance frame -> cell frame (involutions: N/S/FN/FS).
                lx1, lx2 = cx1 - dx, cx2 - dx
                ly1, ly2 = cy1 - dy, cy2 - dy
                if oi in ("S", "FS"):
                    lx1, lx2 = cw - lx2, cw - lx1
                if oi in ("S", "FN"):
                    ly1, ly2 = ch - ly2, ch - ly1
                key = ((lid, lx1, lx2, ly1, ly2) if horiz
                       else (lid, ly1, ly2, lx1, lx2))
                if key not in agg:
                    agg[key] = 0.0
                    order.append(key)
                agg[key] += amt
        return [key + (agg[key],) for key in order]

    def _persist_bottom_up_dogleg_adoptions(self):
        """Replay the BDB persistence `_adopt_bottom_up_doglegs` deferred
        under `_rr_in_trial` — the accept path of a healer template-class
        move: for every template with an adopted dogleg slot, upsert the
        template's split row and each locked instance's expanded-bundle row
        + selection.  Idempotent (upserts), so pre-trial adoptions
        re-persist identical rows.  Replaying from the LIVE state instead
        of re-running the cell-local solve keeps the accepted trial's
        nuts_result and fixed-copy cache authoritative: re-solving an
        already-split pinned template could re-dogleg into a
        split-of-a-split and silently diverge from the accepted routing
        (review #472 / issue #473) — and it saves a cell-local NUTS solve
        per accepted move."""
        if self.bdb is None:
            return
        slots = getattr(self, "_bu_dogleg_slot", None) or {}
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        for tid, slot in slots.items():
            w = self._rr_template_wrapper(tid)
            if w is None or not (0 <= slot < len(w.input.candidates)):
                continue
            self._persist_template_dogleg(w, tid, slot,
                                          list(w.plan.seg_layers))
            for iw in exp_map.get(tid, []):
                if not iw.hier.locked:
                    continue
                bid = iw.input.original_bundle.id
                islot = self._dogleg_slot.get(bid)
                if islot is None \
                        or not (0 <= islot < len(iw.input.candidates)):
                    continue
                self._add_expanded_bundle(iw, islot, {bid: tid})
                self.bdb.set_topology_selected(str(bid), islot)

    def _persist_template_dogleg(self, w, tid, slot, layers):
        """Persist a template's adopted dogleg split as a candidate row at
        its live slot (source='dogleg', selected, per-segment assigned
        layers + logical annotations) — the resume counterpart of the live
        adoption: a checkpoint taken after run_nuts must restore candidates
        whose segment indices match the persisted bus rows.  Upsert-safe (a
        re-solve overwrites the same cand_index); _reset_bottom_up_doglegs
        deletes the row when a re-plan drops the adoption."""
        import json
        bid = str(tid)
        if not any(b.id == bid for b in self.bdb.all_bundles()):
            return
        topo = w.input.candidates[slot]
        tr = buda.TopoRow()
        tr.id = bid
        tr.cand_index = slot
        tr.type = topo.type
        tr.wirelength = topo.estimated_wirelength
        tr.trunk_location = topo.trunk_location
        tr.pass_through_count = topo.pass_through_count
        tr.connected_blocks = json.dumps(list(topo.connected_block_names))
        tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
        tr.is_selected = True
        tr.topo_uid = buda.topo_uid(topo)
        tr.source = "dogleg"
        self.bdb.add_topology(tr)
        for si, seg in enumerate(topo.segments):
            sr = buda.TopoSegRow()
            sr.id = bid
            sr.cand_index = slot
            sr.seg_index = si
            sr.x1, sr.y1 = seg.start.x, seg.start.y
            sr.x2, sr.y2 = seg.end.x, seg.end.y
            sr.layer_hint = seg.layer_hint
            sr.is_jog = seg.is_jog
            sr.edge_id = seg.edge_id
            sr.perp_clamp_lo = seg.perp_clamp_lo
            sr.perp_clamp_hi = seg.perp_clamp_hi
            sr.assigned_layer = (int(layers[si]) if si < len(layers)
                                 else -1)
            self.bdb.add_topology_segment(sr)
        self._persist_topology_annotations(bid, slot, topo)
        self.bdb.set_topology_selected(bid, slot)

    def _reset_bottom_up_doglegs(self):
        """Drop template-level dogleg adoptions before a re-plan (the
        bottom-up twin of `_reset_doglegs`): the appended split candidate
        is removed from each template's pool and the pre-split selection
        restored, so the local planner re-decides from pristine candidates
        and the next local NUTS re-detects cycles from scratch.  Instance
        wrappers are covered by the planner-time `_reset_doglegs` (their
        slots live in the shared `_dogleg_slot`)."""
        slots = getattr(self, "_bu_dogleg_slot", None)
        if not slots:
            return
        templates = getattr(self, "_hier_bundles_orig", None) or []
        for w in templates:
            tid = w.input.original_bundle.id
            slot = slots.get(tid)
            if slot is None:
                continue
            cands = w.input.candidates
            if 0 <= slot < len(cands):
                del cands[slot]
                w.input.candidates = cands
                if self.bdb is not None:
                    # Drop the persisted split row too (the re-plan's own
                    # persist re-selects among the pristine candidates).
                    self.bdb.delete_topology(str(tid), slot)
            orig = self._bu_dogleg_originals.get(tid, 0)
            w.plan.selected_topology_index = \
                orig if 0 <= orig < len(w.input.candidates) else 0
            w.plan.seg_net_pull = []
            w.plan.seg_slide_lo = []
            w.plan.seg_slide_hi = []
            w.plan.seg_perp = []
        self._bu_dogleg_slot = {}
        self._bu_dogleg_originals = {}

    def _inject_bottom_up_fixed(self, nuts_engine):
        """Register the bottom-up fixed copies with a NUTS engine (no-op when
        there are none).  Must be called on EVERY engine that solves
        self.bundles — run_nuts, post_nuts re-run, run_nuts_on_layer, and the
        ripup/negotiate internal re-runs — or the fixed instances would be
        re-solved as free bundles."""
        fixed = self._bottom_up_fixed_segments()
        if fixed:
            nuts_engine.add_fixed_segments(fixed)

    def _bottom_up_instance_groups(self):
        """(cell, template id, [locked instance wrappers]) triples for every
        bottom-up template that produced locked instances — the shared walk
        under check_template_tracks and the DNUTS copy plan.

        Derived from the expansion map ALONE, so it works both in a live
        session and on a load_pipeline-expanded resume (where the template
        wrappers are gone and the map was rebuilt from parent_id links).
        Replica entries — which alias a subset of their template's wrappers —
        are dropped by subset dedup."""
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        bu_cells = set(self.bdb.bottom_up_cells()) if self.bdb else set()
        cands = []
        for tid, iws in exp_map.items():
            locked = [iw for iw in iws
                      if iw.hier.locked
                      and self._bu_cell_of(
                          iw.input.original_bundle.cell_context) in bu_cells]
            if locked:
                cands.append((tid, locked, frozenset(id(x) for x in locked)))
        out, seen = [], set()
        for tid, iws, s in sorted(cands, key=lambda c: c[0]):
            if s in seen or any(s < s2 for _, _, s2 in cands):
                continue          # replica alias of a (larger) template group
            seen.add(s)
            out.append((iws[0].input.original_bundle.cell_context, tid, iws))
        return out

    def _check_template_tracks(self, verbose=True):
        """Stage (c) verification of bottom-up template planning: do all
        instances of each marked cell see the SAME signal tracks?

        Ground truth, per fixed (copied) segment: the SPAN-AWARE track pool
        (`signal_tracks_in_span` over the segment's span × interval window —
        the pool DNUTS itself prefers, so keepouts crossing only part of the
        wire are honored), enumerated in each instance's translated window
        and normalized by the instance origin.  Instances agree exactly when
        their placement offset is a multiple of the layer's track pitch AND
        no absolute-rect override or keepout cuts their windows differently.

        Returns AND caches the verdict:
          {cell: {'ref': inst, 'aligned': [inst...],
                  'misaligned': {inst: [issue strings]}}}
        consumed by run_detailed_nuts (copy aligned instances; the mismatch
        policy — self._bu_mismatch_policy 'stop'|'independent' — governs the
        rest).  A layer with no track pattern is skipped (DNUTS skips it
        too); with no routing grid at all the check is vacuous.

        Before any routing exists (e.g. right after open_bdb +
        set_bottom_up, before derive_busterms), the check falls back to the
        PLACEMENT-STAGE mode: whole-instance windows per grid layer instead
        of per-routed-segment windows.  That verdict is advisory (printed,
        cached separately, feeds align_bottom_up) — it never gates DNUTS,
        which re-checks against the real routed windows.
        """
        fixed = self._bottom_up_fixed_segments()
        if not fixed:
            # Post-planner with no fixed routing = no marked cell produced a
            # copyable local solve (templates unplannable / no instances):
            # the placement-stage report below could read ALIGNED while
            # DNUTS will in fact solve per-instance — say so rather than
            # switch modes silently.
            if (verbose and getattr(self, "_planner_is_hier", False)
                    and self.nuts_result is not None
                    and self.bdb is not None and self.bdb.bottom_up_cells()):
                print("check_template_tracks: no bottom-up fixed routing "
                      "exists after run_nuts (no marked cell produced a "
                      "copyable local solve) — DNUTS will solve "
                      "per-instance; the placement-stage report below does "
                      "not gate it.")
            return self._check_template_tracks_placement(verbose=verbose)
        verdict = {}
        # Cache published only after the fill completes (audit P2-01): an
        # exception mid-fill must not leave a partial verdict cached — which
        # would then silently DISABLE the bottom-up track gate on the next call.
        if self.routing_grid is None and verbose:
            print("check_template_tracks: no routing grid defined "
                  "(def_track_pattern) — nothing to compare yet; alignment "
                  "will be trivially reported ALIGNED.")
        segs_by_bid = {}
        for ts in fixed:
            segs_by_bid.setdefault(ts.bundle_id, {})[ts.seg_idx] = ts
        comps = {c.name: c for c in self.bdb.all_components()}

        def rel_tracks(ts, inst_name, refl=False, extent=0.0):
            # Track pool of ts's window, relative to the instance origin.
            # For a mirrored sibling (refl) the pool is reflected back into
            # the REFERENCE frame (rel' = extent - rel, order reversed) so
            # physically identical tracks compare equal position-by-position.
            if self.routing_grid is None \
                    or not self.routing_grid.has_layer(ts.layer):
                return None
            g = self.routing_grid.get_layer_grid(ts.layer)
            lo, hi = sorted((ts.span_lo, ts.span_hi))
            tracks = g.signal_tracks_in_span(lo, hi,
                                             ts.interval_lo, ts.interval_hi)
            c = comps[inst_name]
            off = c.y1 if ts.horiz else c.x1
            rel = [(round(p - off, 6), round(slot.width, 6))
                   for p, slot in tracks]
            if refl:
                rel = [(round(extent - p, 6), w) for p, w in reversed(rel)]
            return rel

        cells = {}
        for cell, tid, iws in self._bottom_up_instance_groups():
            cells.setdefault(cell, []).append(iws)
        for cell in sorted(cells):
            ref_name = cells[cell][0][0].input.original_bundle.instances[0]
            orients = self._detect_instance_orients(
                self._bu_cell_of(cell), comps.values(), ref_name=ref_name)
            rc = comps.get(ref_name)
            rcw = round(rc.x2 - rc.x1, 6) if rc else 0.0
            rch = round(rc.y2 - rc.y1, 6) if rc else 0.0
            aligned, misaligned = [ref_name], {}
            n_windows = 0
            for iws in cells[cell]:
                ref_iw = next((iw for iw in iws
                               if iw.input.original_bundle.instances[0]
                               == ref_name), None)
                if ref_iw is None:
                    # This template's bus exists only in a subset of the
                    # cell's occurrences that EXCLUDES the cell-wide
                    # reference (bundler groups cell-local bundles per bus,
                    # so subsets are legal). There is no sound comparison
                    # base against the cell reference, so treat the group's
                    # instances as misaligned — LOUD and conservative: the
                    # stop policy refuses DNUTS with this report,
                    # `independent` solves them individually. The bare
                    # next() here used to raise StopIteration, and the
                    # partially-built verdict then silently DISABLED the
                    # whole bottom-up track gate (audit P1-02).
                    for iw in iws:
                        inst = iw.input.original_bundle.instances[0]
                        misaligned.setdefault(inst, []).append(
                            "template covers an instance subset that "
                            f"excludes reference '{ref_name}' — no "
                            "comparison base; treating as misaligned")
                    continue
                ref_bid = ref_iw.input.original_bundle.id
                for iw in iws:
                    inst = iw.input.original_bundle.instances[0]
                    if inst == ref_name:
                        continue
                    bid = iw.input.original_bundle.id
                    oi = orients.get(inst) or "N"
                    _, rx, ry = self._ORIENT_MAPS.get(oi, (0, 0, 0))
                    issues = misaligned.get(inst, [])
                    for si, rts in sorted(segs_by_bid.get(ref_bid,
                                                          {}).items()):
                        sts = segs_by_bid.get(bid, {}).get(si)
                        if sts is None:
                            continue
                        a = rel_tracks(rts, ref_name)
                        # A mirrored instance's perp axis: y for an H wire
                        # (reflected iff ry), x for a V wire (iff rx).
                        b = rel_tracks(
                            sts, inst,
                            refl=bool(ry if sts.horiz else rx),
                            extent=rch if sts.horiz else rcw)
                        if a is None or b is None:
                            continue
                        n_windows += 1
                        if len(a) != len(b):
                            issues.append(
                                f"L{rts.layer} seg{si}: {len(b)} track(s) "
                                f"vs {len(a)} at reference")
                            continue
                        for (pa, wa), (pb, wb) in zip(a, b):
                            if abs(pa - pb) > 1e-6 or abs(wa - wb) > 1e-6:
                                issues.append(
                                    f"L{rts.layer} seg{si}: track at rel "
                                    f"{pb:+.3f} vs reference {pa:+.3f}")
                                break
                    if issues:
                        misaligned[inst] = issues
                    elif inst not in aligned and inst not in misaligned:
                        aligned.append(inst)
            # An instance flagged by ANY template window is misaligned.
            aligned = [i for i in aligned if i not in misaligned]
            verdict[cell] = {'ref': ref_name, 'aligned': aligned,
                             'misaligned': misaligned}
            if verbose:
                if misaligned:
                    detail = "; ".join(
                        f"{i}: {v[0]}" + (f" (+{len(v)-1} more)"
                                          if len(v) > 1 else "")
                        for i, v in sorted(misaligned.items()))
                    print(f"[TemplateTracks] cell '{cell}': MISALIGNED — "
                          f"{detail}")
                else:
                    print(f"[TemplateTracks] cell '{cell}': ALIGNED — "
                          f"{len(aligned)} instance(s) see identical signal "
                          f"tracks (ref {ref_name}, {n_windows} window(s) "
                          f"compared)")
        self._template_track_verdict = verdict   # publish on success (P2-01)
        self._bu_dnuts_plan_cache = None         # verdict changed → plan stale
        return verdict

    def _grid_layer_dirs(self):
        """{layer_id: horiz?} for every layer that has a track pattern."""
        out = {}
        if self.routing_grid is None:
            return out
        for lid in self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL):
            if self.routing_grid.has_layer(lid):
                out[lid] = True
        for lid in self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL):
            if self.routing_grid.has_layer(lid):
                out[lid] = False
        return out

    def _cell_band_grid_layers(self, group, dirs):
        """`dirs` ({layer_id: horiz?}, from _grid_layer_dirs) restricted to
        `group`'s cell layer band (docs/internal/hier_layer_caps.md Phase 2).
        `group` may be a rotation-class clone name — resolved to the real
        cell via _bu_cell_of; explicit policy first, then the '*' default.
        No policy => `dirs` unchanged (the byte-identity guarantee).  A
        capped cell's copied routing can only land inside its band, so the
        alignment LCM and the placement-stage pool comparison must range
        over the band's layers only — a coarser full-stack phase would
        demand agreement (and nudges) the cell's routing cannot observe."""
        pol = getattr(self, "_cell_layer_policy", None)
        if not pol:
            return dirs
        cell = self._bu_cell_of(group)
        entry = pol.get(cell, pol.get("*"))
        if entry is None:
            return dirs
        floor, cap = entry
        lo = floor if floor >= 0 else -(10 ** 9)
        return {l: h for l, h in dirs.items() if lo <= l <= cap}

    def _bottom_up_placed_instances(self):
        """{group: [placed ComponentRows]} for every marked cell with >= 2
        placed instances — the placement-stage unit of work (no bundler,
        busterms, or routing required).  PARTITIONED by rotation class:
        direction-preserving instances group under the real cell name, the
        90° family under the cell's clone name (_bu_clone_name — the same
        deterministic name the planner-time split will use, registered in
        _bu_clone_cells so _bu_cell_of resolves it here too).  A solo class
        (one instance) has nothing to compare or align and is dropped, like
        a single-instance cell before."""
        out = {}
        if self.bdb is None:
            return out
        bu = set(self.bdb.bottom_up_cells())
        if not bu:
            return out
        comps = self.bdb.all_components()
        by_cell = {}
        for c in comps:
            if c.cell in bu and is_placed(c):
                by_cell.setdefault(c.cell, []).append(c)
        ocache = {}
        for cell, insts in by_cell.items():
            insts.sort(key=lambda c: c.name)
            if len(insts) < 2:
                continue
            orients = self._detect_instance_orients(
                cell, comps, ref_name=insts[0].name, cache=ocache)
            rot = [c for c in insts
                   if orients.get(c.name) is not None
                   and orients[c.name] not in self._DIR_PRESERVING]
            keep = [c for c in insts if c not in rot]
            if len(keep) >= 2:
                out[cell] = keep
            if len(rot) >= 2:
                cname = self._bu_clone_name(cell)
                if getattr(self, "_bu_clone_cells", None) is None:
                    self._bu_clone_cells = {}
                self._bu_clone_cells[cname] = cell
                out[cname] = rot
        return out

    def _check_template_tracks_placement(self, verbose=True):
        """Placement-stage track-alignment check: no routing needed, so it
        runs right after open_bdb + set_bottom_up (before derive_busterms).

        Compares the span-aware signal-track pool over each instance's
        WHOLE window per grid layer (span = the instance's extent along the
        layer direction, interval = its perpendicular extent), normalized by
        the instance origin.  Whole-window agreement covers pattern phase,
        overrides, and keepouts for the full footprint; it is advisory — the
        routed check (per fixed-segment window, run after run_nuts) remains
        the DNUTS gate, because a keepout at different *relative* positions
        can agree on the whole window yet differ on a sub-window.

        Verdict cached on self._template_track_verdict_placement (feeds
        align_bottom_up), same shape as the routed verdict.
        """
        verdict = {}
        self._template_track_verdict_placement = verdict
        by_cell = self._bottom_up_placed_instances()
        if not by_cell:
            if verbose:
                print("check_template_tracks: nothing to check — mark cells "
                      "with set_bottom_up (each needs >= 2 placed instances) "
                      "in an open BDB first.")
            return verdict
        dirs = self._grid_layer_dirs()
        if not dirs:
            if verbose:
                print("check_template_tracks: no routing grid defined "
                      "(def_track_pattern) — nothing to compare yet.")
            return verdict

        def rel_tracks(inst, lid, horiz, refl=False):
            g = self.routing_grid.get_layer_grid(lid)
            if horiz:
                tracks = g.signal_tracks_in_span(inst.x1, inst.x2,
                                                 inst.y1, inst.y2)
                off, extent = inst.y1, inst.y2 - inst.y1
            else:
                tracks = g.signal_tracks_in_span(inst.y1, inst.y2,
                                                 inst.x1, inst.x2)
                off, extent = inst.x1, inst.x2 - inst.x1
            rel = [(round(p - off, 6), round(slot.width, 6))
                   for p, slot in tracks]
            if refl:
                # Mirrored instance: reflect the pool back into the
                # reference frame so identical tracks compare equal.
                rel = [(round(extent - p, 6), w) for p, w in reversed(rel)]
            return rel

        for cell in sorted(by_cell):
            insts = by_cell[cell]
            ref = insts[0]
            # `cell` may be a rotation-class group key (clone name) —
            # detection needs the real cell type; within the group all
            # relative orients are direction-preserving.
            orients = self._detect_instance_orients(
                self._bu_cell_of(cell), self.bdb.all_components(),
                ref_name=ref.name)
            # Band-scoped comparison (layer caps, Phase 2): a capped cell's
            # routing only ever lands inside its band, so a phase mismatch
            # on an out-of-band layer is invisible to its copies — comparing
            # it would contradict the band-scoped align_bottom_up.
            dirs_c = self._cell_band_grid_layers(cell, dirs)
            scoped = (f" (band-scoped to {sorted(dirs_c)})"
                      if dirs_c is not dirs else "")
            aligned, misaligned = [ref.name], {}
            for c in insts[1:]:
                issues = []
                oi = orients.get(c.name) or "N"
                _, rx, ry = self._ORIENT_MAPS.get(oi, (0, 0, 0))
                for lid, horiz in sorted(dirs_c.items()):
                    a = rel_tracks(ref, lid, horiz)
                    b = rel_tracks(c, lid, horiz,
                                   refl=bool(ry if horiz else rx))
                    if len(a) != len(b):
                        issues.append(f"L{lid}: {len(b)} track(s) vs "
                                      f"{len(a)} at reference")
                        continue
                    for (pa, wa), (pb, wb) in zip(a, b):
                        if abs(pa - pb) > 1e-6 or abs(wa - wb) > 1e-6:
                            issues.append(f"L{lid}: track at rel {pb:+.3f} "
                                          f"vs reference {pa:+.3f}")
                            break
                if issues:
                    misaligned[c.name] = issues
                else:
                    aligned.append(c.name)
            verdict[cell] = {'ref': ref.name, 'aligned': aligned,
                             'misaligned': misaligned}
            if verbose:
                if misaligned:
                    detail = "; ".join(
                        f"{i}: {v[0]}" + (f" (+{len(v)-1} more)"
                                          if len(v) > 1 else "")
                        for i, v in sorted(misaligned.items()))
                    print(f"[TemplateTracks] (placement-stage) cell "
                          f"'{cell}': MISALIGNED{scoped} — {detail}")
                else:
                    print(f"[TemplateTracks] (placement-stage) cell "
                          f"'{cell}': ALIGNED{scoped} — {len(aligned)} "
                          f"instance(s) see identical signal tracks over "
                          f"their whole window (ref {ref.name})")
        if verbose:
            print("[TemplateTracks] placement-stage verdict is advisory: "
                  "the post-run_nuts check (per routed window) gates "
                  "run_detailed_nuts. Misaligned? try align_bottom_up.")
        return verdict

    @staticmethod
    def _pitch_lcm(pitches):
        """LCM of float pitches via micro-unit integers (None if empty)."""
        if not pitches:
            return None
        import math
        SCALE = 1_000_000
        ints = [max(1, int(round(p * SCALE))) for p in pitches]
        l = ints[0]
        for v in ints[1:]:
            l = l // math.gcd(l, v) * v
        return l / SCALE

    def _validate_placement(self):
        """FloorplannerEngine.validate() over the BDB's placed components:
        OVERLAP pairs (ancestor/descendant pairs excluded) and OUTSIDE_DIE
        (skipped when the BDB has no die).  Returns
        [(kind, block_a, block_b, message)] — the block names let the
        aligner's auto-revert attribute a new issue to the moved instance."""
        eng = buda.FloorplannerEngine()
        if self.bdb.die_w() > 0 and self.bdb.die_h() > 0:
            eng.set_die(self.bdb.die_w(), self.bdb.die_h())
        for c in self.bdb.all_components():
            if is_placed(c):
                eng.add_block(c.name, c.x1, c.y1, c.x2, c.y2)
        return [(i.kind, i.block_a, i.block_b, i.message)
                for i in eng.validate()]

    def _align_bottom_up(self, max_shift=None, force=False):
        """Nudge every marked cell's instances onto a common track phase
        with MINIMAL total movement, so the bottom-up copies land on real
        signal tracks in every occurrence.

        Per cell and axis: an instance offset is track-shift-invariant iff
        it is a multiple of every relevant layer's unit pitch — V-layer
        pitches constrain x, H-layer pitches constrain y (combined as their
        LCM).  All instances must share one phase (their coordinate mod
        LCM); the target phase is chosen among the instances' current
        phases minimizing the summed circular shift (the L1 circular median
        lies on a data point), so the whole group moves as little as
        possible — often the reference moves and the majority stand still.

        MIRRORED instances (S/FN/FS — detected geometrically): a reflected
        window sees the reference's tracks iff the signal-track set is
        mirror-symmetric (about some center σ, per layer: T = 2σ − T mod
        pitch) and the instance's EFFECTIVE coordinate e = K − extent − c
        shares the group phase, where K ≡ 2σ_l (mod pitch_l) for every
        layer of the direction (combined by CRT) and extent = the instance
        dim on the axis; the real nudge is the NEGATED effective shift.
        An asymmetric pattern, or layers whose 2σ congruences are
        inconsistent, make a mirrored instance's phase target undefined on
        that axis — it is left unmoved with a WARNING (and does not vote
        for the phase).  check_template_tracks remains the ground truth.

        Moves are applied with translate_comp (whole subtree, preserving
        congruence).  Region overrides are absolute-rect-keyed and cannot
        be fixed by translation — the check reports them.  Run BEFORE
        derive_busterms / add_blocks_from_bdb; a nudge exceeding max_shift
        (when given) is skipped with a WARNING.  Returns #instances moved.

        NESTED marked cells: cells are processed parents-first (by instance
        depth) with coordinates re-read after each cell's moves, because a
        parent nudge drags its whole subtree.  An instance living INSIDE a
        marked ancestor's instance is an ANCHOR — moving it alone would
        break the ancestor's congruence — so it never moves; the target
        phase is chosen among the anchors' phases when any exist (movable
        siblings come to them).  Anchors at differing phases (the template
        places two child instances at incompatible relative positions) are
        not fixable by translation and are reported.

        After the moves, FloorplannerEngine.validate() audits the placement
        (block overlaps + outside-die).  By DEFAULT any move that introduced
        a NEW issue is AUTO-REVERTED — the exact-geometry realization of a
        "slack-aware cap": instead of pre-deriving a scalar bound from free
        slack (which would also block large-but-legal nudges), the applied
        end state itself is the test.  The revert loop runs to a fixpoint,
        because undoing one instance can newly collide with a still-moved
        sibling (both slid into the same gap) — the worst case restores the
        original placement.  A reverted instance leaves its cell (and any
        nested cell aligned against it) possibly misaligned — re-run
        check_template_tracks, which reports it.  `force=True` keeps
        issue-introducing moves and only WARNs (the pre-revert behavior).
        Pre-existing issues are summarized, never blamed on the alignment.
        """
        by_cell = self._bottom_up_placed_instances()
        if not by_cell:
            print("align_bottom_up: nothing to align — mark cells with "
                  "set_bottom_up (each needs >= 2 placed instances) first.")
            return 0
        dirs = self._grid_layer_dirs()
        if not dirs:
            print("Error: align_bottom_up requires a routing grid "
                  "(def_track_pattern) to know the track pitches")
            return 0
        # Anything derived from placement is stale after a nudge — warn on
        # EVERY such artifact, not just bundles: derive_busterms and
        # add_blocks_from_bdb can each run before the bundler.
        stale = [w for got, w in (
            (self.bdb.all_busterms(), "derived busterms"),
            (self.fp.get_all_blocks(), "projected floorplan blocks"),
            (self.bundles, "bundles/routing"),
        ) if got]
        if stale:
            print(f"WARNING: align_bottom_up after {' + '.join(stale)} were "
                  f"built — those coordinates are now stale; re-run the "
                  f"flow from derive_busterms.")
        lx = self._pitch_lcm(
            [self.routing_grid.get_layer_grid(l).global_pattern().unit_pitch()
             for l, horiz in dirs.items() if not horiz])
        ly = self._pitch_lcm(
            [self.routing_grid.get_layer_grid(l).global_pattern().unit_pitch()
             for l, horiz in dirs.items() if horiz])
        if any(self.routing_grid.get_layer_grid(l).has_overrides()
               for l in dirs):
            print("WARNING: add_grid_override regions present — phase "
                  "alignment cannot compensate absolute-rect overrides; "
                  "verify with check_template_tracks afterwards.")

        pre_issues = self._validate_placement()

        # Mirrored instances need the direction's mirror constant K with
        # K ≡ 2σ_l (mod pitch_l) for every layer (σ_l = the symmetry center
        # of layer l's signal-track set); None = undefined (see docstring).
        kx = self._mirror_const(False)   # V layers constrain x
        ky = self._mirror_const(True)    # H layers constrain y

        def to_phase(c, p, period):
            d = (p - c) % period
            return d - period if d > period / 2 else d

        def shifts(recs, period):
            # recs: (name, eff_coord, sign, anchor?, skip?) per instance.
            # Signed minimal per-instance REAL shifts to the common
            # effective phase that (1) sits on the most anchors — immovable
            # instances inside marked ancestors — then (2) minimizes the
            # movables' total movement (the L1 circular median lies on a
            # data point).  Anchors/skips get shift 0 (skips don't vote);
            # returns the names of any anchors OFF the chosen phase (not
            # fixable by translation).
            if period is None:
                return [0.0] * len(recs), []
            anchors = [(n, e) for n, e, _, anc, skip in recs
                       if anc and not skip]
            movs = [e for _, e, _, anc, skip in recs
                    if not anc and not skip]
            best = None
            for cand in ([a for _, a in anchors] or movs or [0.0]):
                p = cand % period
                mis = sum(1 for _, a in anchors
                          if abs(to_phase(a, p, period)) > 1e-6)
                cost = sum(abs(to_phase(m, p, period)) for m in movs)
                if (best is None or mis < best[0]
                        or (mis == best[0] and cost < best[1] - 1e-9)):
                    best = (mis, cost, p)
            p = best[2]
            offside = [n for n, a in anchors
                       if abs(to_phase(a, p, period)) > 1e-6]
            return ([0.0 if (anc or skip)
                     else sign * to_phase(e, p, period)
                     for _, e, sign, anc, skip in recs], offside)

        applied = {}   # instance name → (dx, dy, cell) actually moved
        # Anchor semantics need EVERY placed instance of a marked cell —
        # including solo-class instances that get no alignment group of
        # their own — so the marked set comes from the BDB, not by_cell.
        bu_all = set(self.bdb.bottom_up_cells())

        def all_marked_names():
            return {c.name for c in self.bdb.all_components()
                    if c.cell in bu_all and is_placed(c)}

        # Enclosing cells before enclosed ones, and FRESH coordinates per
        # cell: a parent's nudge drags its whole subtree, so a nested cell's
        # deltas must never come from a pre-move snapshot.  Order by marked-
        # ancestor nesting level (not instance depth — a child cell may also
        # have standalone top-level instances that would tie the depths).
        marked0 = all_marked_names()

        def nest_level(c):
            parts = c.name.split("/")
            return sum(1 for k in range(1, len(parts))
                       if "/".join(parts[:k]) in marked0)

        order = sorted(by_cell,
                       key=lambda cl: (max(nest_level(c)
                                           for c in by_cell[cl]), cl))
        for cell in order:
            snap = self._bottom_up_placed_instances()   # fresh after moves
            insts = snap.get(cell, [])
            if len(insts) < 2:
                continue
            # Band-scoped phases (layer caps, Phase 2): a capped cell's
            # routing lives inside its band, so its alignment period is the
            # LCM of the BAND's pitches only (smaller or equal — more
            # instance pairs already agree, nudges shrink), and the mirror
            # constant combines only the band's congruences.
            dirs_c = self._cell_band_grid_layers(cell, dirs)
            if dirs_c is not dirs:
                lx_c = self._pitch_lcm(
                    [self.routing_grid.get_layer_grid(l).global_pattern()
                         .unit_pitch()
                     for l, horiz in dirs_c.items() if not horiz])
                ly_c = self._pitch_lcm(
                    [self.routing_grid.get_layer_grid(l).global_pattern()
                         .unit_pitch()
                     for l, horiz in dirs_c.items() if horiz])
                kx_c = self._mirror_const(False, lids=dirs_c)
                ky_c = self._mirror_const(True, lids=dirs_c)
                print(f"[Align] cell '{cell}': band-scoped to layer(s) "
                      f"{sorted(dirs_c)} (periods x={lx_c} y={ly_c})")
            else:
                lx_c, ly_c, kx_c, ky_c = lx, ly, kx, ky
            marked = all_marked_names()
            def inside_marked(name):
                parts = name.split("/")
                return any("/".join(parts[:k]) in marked
                           for k in range(1, len(parts)))
            movable = {c.name for c in insts if not inside_marked(c.name)}
            # `cell` may be a rotation-class group key (clone name):
            # detection needs the real cell type, and only THIS group's
            # members matter (the other class legitimately sits at 90°
            # relative to this group's reference).
            group = {c.name for c in insts}
            orients = self._detect_instance_orients(
                self._bu_cell_of(cell), self.bdb.all_components(),
                ref_name=insts[0].name)
            unsupported = sorted(
                n for n, o in orients.items()
                if n in group
                and (o is None or o not in self._DIR_PRESERVING))
            for n in unsupported:
                print(f"WARNING: align_bottom_up: {n} of group '{cell}' has "
                      f"unsupported orientation ({orients[n]}) — left "
                      f"unmoved")

            def axis_recs(axis):
                # axis 0 = x (V layers, const kx_c), 1 = y (H layers, ky_c).
                K = kx_c if axis == 0 else ky_c
                recs = []
                for c in insts:
                    o = orients.get(c.name)
                    skip = c.name in unsupported
                    _, rx, ry = self._ORIENT_MAPS.get(o or "N", (0, 0, 0))
                    refl = rx if axis == 0 else ry
                    coord = c.x1 if axis == 0 else c.y1
                    eff, sign = coord, 1.0
                    if refl and not skip:
                        if K is None:
                            print(f"WARNING: align_bottom_up: {c.name} is "
                                  f"mirrored on {'x' if axis == 0 else 'y'} "
                                  f"but that direction's track layout has "
                                  f"no usable mirror symmetry — left "
                                  f"unmoved on this axis")
                            skip = True
                        else:
                            extent = (c.x2 - c.x1 if axis == 0
                                      else c.y2 - c.y1)
                            eff = K - extent - coord
                            sign = -1.0
                    recs.append((c.name, eff, sign,
                                 c.name not in movable, skip))
                return recs

            dxs, off_x = shifts(axis_recs(0), lx_c)
            dys, off_y = shifts(axis_recs(1), ly_c)
            for n in sorted(set(off_x) | set(off_y)):
                print(f"WARNING: align_bottom_up: {n} (inside a marked "
                      f"parent) sits off cell '{cell}'s chosen phase — the "
                      f"parent template places it at an incompatible "
                      f"offset; not fixable by translation")
            for c, dx, dy in zip(insts, dxs, dys):
                dx = 0.0 if abs(dx) < 1e-9 else dx
                dy = 0.0 if abs(dy) < 1e-9 else dy
                if max_shift is not None and \
                        max(abs(dx), abs(dy)) > max_shift + 1e-9:
                    print(f"WARNING: align_bottom_up: {c.name} needs "
                          f"(dx={dx:+.3f}, dy={dy:+.3f}) > max_shift "
                          f"{max_shift} — skipped (cell '{cell}' may stay "
                          f"misaligned)")
                    continue
                if dx or dy:
                    self.bdb.translate_comp(c.name, dx, dy)
                    applied[c.name] = (dx, dy, cell)
                    print(f"[Align] cell '{cell}': {c.name} moved by "
                          f"(dx={dx:+.3f}, dy={dy:+.3f})")

        # Post-move placement audit.  Default: auto-revert every move that
        # introduced a NEW issue (the exact-geometry slack cap), iterating
        # to a fixpoint since a revert can newly collide with a still-moved
        # sibling.  Attribution: an issue's block (or its subtree parent) is
        # matched back to the moved instance.  force=True keeps the moves
        # and only warns.
        pre = set(pre_issues)
        reverted = {}
        if applied and not force:
            while True:
                new = [i for i in self._validate_placement() if i not in pre]
                names = set()
                first_issue = {}
                for kind, a, b, msg in new:
                    for blk in (a, b):
                        if not blk:
                            continue
                        hit = next((n for n in applied
                                    if blk == n or blk.startswith(n + "/")),
                                   None)
                        if hit is not None:
                            names.add(hit)
                            first_issue.setdefault(hit, (kind, msg))
                if not names:
                    break
                for n in sorted(names):
                    dx, dy, cell = applied.pop(n)
                    self.bdb.translate_comp(n, -dx, -dy)
                    reverted[n] = cell
                    kind, msg = first_issue[n]
                    print(f"WARNING: align_bottom_up: move of {n} REVERTED "
                          f"— it introduced {kind} ({msg}); cell '{cell}' "
                          f"may stay misaligned (pass 'force' to keep such "
                          f"moves)")
        moved = len(applied)
        if applied or reverted:
            # Placement changed at some point: every derived bottom-up
            # artifact is stale.
            self._bu_fixed_cache = None
            self._template_track_verdict = None
            self._template_track_verdict_placement = None
            self._bu_dnuts_plan_cache = None
        pitches = (f"x-period {lx}" if lx else "x unconstrained") + ", " + \
                  (f"y-period {ly}" if ly else "y unconstrained")
        print(f"[Align] {moved} instance(s) moved"
              + (f", {len(reverted)} reverted" if reverted else "")
              + f" ({pitches}); verify with check_template_tracks.")
        if applied or reverted:
            new = [i for i in self._validate_placement() if i not in pre]
            for kind, a, b, msg in new:
                print(f"WARNING: align_bottom_up introduced {kind}: {msg}")
            print(f"[Align] validate: {len(new)} new issue(s) from the "
                  f"moves, {len(pre)} pre-existing.")
        return moved

    def _bottom_up_dnuts_plan(self):
        """Cached wrapper for `_bottom_up_dnuts_plan_compute`.

        The plan depends only on the placement, the `check_template_tracks`
        verdict, and `_bottom_up_fixed_segments` — all invariant during a
        `ripup_reroute` / `negotiate_congestion` run (trials move topologies,
        never geometry), so recomputing it on every `_run_detailed_nuts` was
        ~12.8 ms/trial of pure overhead on `mix2_fast_bottomup`
        (docs/internal/ripup_runtime_analysis.md, item A).  The cache is reset
        wherever `_bu_fixed_cache` / `_template_track_verdict` are — a re-plan
        (`_plan_bottom_up_templates`), an `align_bottom_up` placement nudge, and
        a fresh `check_template_tracks` verdict.  A one-tuple wrapper
        distinguishes a cached `None` (no bottom-up plan) from 'not computed';
        a raising compute (stop-policy mismatch) is never cached."""
        cached = getattr(self, "_bu_dnuts_plan_cache", None)
        if cached is not None:
            return cached[0]
        result = self._bottom_up_dnuts_plan_compute()
        self._bu_dnuts_plan_cache = (result,)
        return result

    def _bottom_up_dnuts_plan_compute(self):
        """Stage (c) DNUTS routing plan for bottom-up cells, from the cached
        (or implicitly run) track verdict.  Returns None when no bottom-up
        fixed routing exists; else (ref_ids, copy_specs, skip_ids):
          ref_ids    — bundle ids solved once (the reference instances)
          copy_specs — [(ref_bid, sib_bid, orient, cw, ch, rx, ry, sx, sy)]
                       oriented copies for ALIGNED siblings: map the
                       reference solve from the ref instance's frame (cw×ch
                       box at (rx, ry)) through `orient` (N/S/FN/FS — locked
                       instances are guarded to the direction-preserving set
                       at expansion) into the sibling frame at (sx, sy)
          skip_ids   — the copied siblings (excluded from the global solve);
                       misaligned siblings stay in the global solve
                       ('independent' policy — copy aligned, solve outliers).
        Raises RuntimeError under the default 'stop' policy when any
        instance is misaligned.
        """
        fixed = self._bottom_up_fixed_segments()
        if not fixed:
            return None
        if getattr(self, "_template_track_verdict", None) is None:
            print("run_detailed_nuts: bottom-up cells present — running "
                  "check_template_tracks (implicit).")
            self._check_template_tracks()
        verdict = self._template_track_verdict
        policy = getattr(self, "_bu_mismatch_policy", "stop")
        mis = [(cell, inst) for cell, v in verdict.items()
               for inst in v['misaligned']]
        if mis and policy == "stop":
            names = "; ".join(f"{c}/{i}" for c, i in mis[:5])
            more = "" if len(mis) <= 5 else f" (+{len(mis) - 5} more)"
            raise RuntimeError(
                f"run_detailed_nuts: {len(mis)} bottom-up instance(s) see "
                f"different signal tracks than their template reference "
                f"({names}{more}). Fix the placement (offset instances by a "
                f"multiple of the layer track pitch / align grid overrides), "
                f"or accept per-instance solving with "
                f"'check_template_tracks on_mismatch independent'.")
        comps = {c.name: c for c in self.bdb.all_components()}
        ref_ids, skip_ids, copy_specs = set(), set(), []
        for cell, tid, iws in self._bottom_up_instance_groups():
            v = verdict.get(cell)
            if v is None:
                continue
            by_inst = {iw.input.original_bundle.instances[0]: iw
                       for iw in iws}
            ref_iw = by_inst.get(v['ref'])
            if ref_iw is None:
                continue
            ref_bid = ref_iw.input.original_bundle.id
            ref_ids.add(ref_bid)
            rc = comps[v['ref']]
            cw = int(round(rc.x2 - rc.x1))
            ch = int(round(rc.y2 - rc.y1))
            # Orientation of each sibling relative to the SAME reference the
            # verdict used (ref maps to 'N', so orients[inst] IS the
            # ref→sibling transform).
            orients = self._detect_instance_orients(
                self._bu_cell_of(cell), comps.values(), ref_name=v['ref'])
            for inst, iw in by_inst.items():
                if inst == v['ref'] or inst in v['misaligned']:
                    continue        # ref solves; misaligned solve globally
                oi = orients.get(inst)
                if oi is None or oi not in self._DIR_PRESERVING:
                    # Shouldn't happen for locked instances (guarded at
                    # expansion) — leave it in the global solve.
                    print(f"WARNING: bottom-up DNUTS copy: instance "
                          f"'{inst}' of '{cell}' has unsupported "
                          f"orientation ({oi}); solving it independently.")
                    continue
                c = comps[inst]
                copy_specs.append((ref_bid, iw.input.original_bundle.id,
                                   oi, cw, ch,
                                   int(round(rc.x1)), int(round(rc.y1)),
                                   int(round(c.x1)), int(round(c.y1))))
                skip_ids.add(iw.input.original_bundle.id)
        if not ref_ids:
            return None
        return ref_ids, copy_specs, skip_ids

    def _expand_hier_bundles(self, bundles):
        """Expand cell-level BundleWrappers to per-instance absolute-coord wrappers.

        Cross-block bundles are kept as-is (candidates already absolute).
        Cell-level bundles (cell_context set) are replaced by one wrapper
        per entry in b.instances, each with candidates offset to that
        instance's absolute position.  Each expanded wrapper gets a unique
        HBundle ID so assignment matching is unambiguous.

        Returns (result_list, expansion_map) where expansion_map maps each
        original bundle ID to the list of expanded wrappers produced from it.
        Cross-block bundles (not expanded) are not included in the map.
        """
        comps = {c.name: c for c in self.bdb.all_components()}
        bottom_up = set(self.bdb.bottom_up_cells())
        # instance bundle id -> uids of its template-inherited candidates
        # (see the registration below; also populated by the expanded
        # loader for resumed sessions).
        self._inherited_uids = getattr(self, "_inherited_uids", {})
        # Replica bookkeeping: the multiple-occurrence merge accumulates all
        # instance paths on the template but leaves each replica in the list
        # with its own instance and its own nets.  Expanding both the template
        # (over all instances) and the replicas would route the same physical
        # buses twice — so replicas are skipped here, and the template's
        # per-instance wrappers take their nets from the bundle that
        # physically lives in that instance (the replica = "donor").
        cell_bundle_ids = {w.input.original_bundle.id for w in bundles
                           if w.input.original_bundle.cell_context}
        donor_nets = {}    # (template id, instance path) → replica net list
        donor_fanin = {}   # (template id, instance path) → (drvs, rcvs)
        replica_wrapper_of = {}  # replica id → (template id, instance path)
        for w in bundles:
            b = w.input.original_bundle
            if (b.cell_context and b.parent_id in cell_bundle_ids
                    and b.instances):
                donor_nets[(b.parent_id, b.instances[0])] = list(b.net_names)
                if b.net_drivers:
                    donor_fanin[(b.parent_id, b.instances[0])] = (
                        list(b.net_drivers),
                        [list(r) for r in b.net_receivers])
                replica_wrapper_of[b.id] = (b.parent_id, b.instances[0])
        # Start synthetic IDs above any real bundle ID in the set.
        max_id = max((w.input.original_bundle.id for w in bundles), default=-1)
        next_id = max_id + 1
        result = []
        expansion_map = {}  # original bundle id → [expanded wrappers]
        wrapper_at = {}     # (template id, instance path) → expanded wrapper
        checked_bu = set()
        warned_orient = set()
        ocache = {}   # orient-detection memo for this expansion pass
        for w in bundles:
            b = w.input.original_bundle
            if not b.cell_context or not b.instances:
                result.append(w)
                continue
            if b.id in replica_wrapper_of:
                continue   # replica: covered by its template's expansion
            # Bottom-up cells: copies must be exact transforms, and only the
            # direction-preserving orientations (N/S/FN/FS) RELATIVE TO THE
            # GROUP REFERENCE keep the pinned per-segment layers valid —
            # hard error otherwise, since uniform copies are the whole
            # contract.  The rotation-class split (which ran just before
            # this expansion) moved every 90°-family instance to its own
            # clone template whose members are direction-preserving among
            # themselves, so a violation here means the placement changed
            # under us or an instance matches no orientation at all.
            if (self._bu_cell_of(b.cell_context) in bottom_up
                    and b.cell_context not in checked_bu):
                checked_bu.add(b.cell_context)
                issues = self._bottom_up_congruence_issues(
                    self._bu_cell_of(b.cell_context), comps.values(),
                    ref_name=b.instances[0], cache=ocache,
                    only=set(b.instances), allow_90=False)
                if issues:
                    raise RuntimeError(
                        f"run_planner hier: bottom-up template "
                        f"'{b.cell_context}' has unsupported instances: "
                        f"{'; '.join(issues[:4])}")
            # Per-instance orientation, GEOMETRICALLY detected against the
            # template's reference instance (candidates were generated from
            # its cell-local layout).  Detected instances get the full
            # transform — the fix for the old translation-only silent
            # mis-transform; an undetectable instance falls back to
            # translation with a warning.  cell_context may be a rotation-
            # class clone name — detection needs the real cell type.
            orients = self._detect_instance_orients(
                self._bu_cell_of(b.cell_context), comps.values(),
                ref_name=b.instances[0], cache=ocache)
            ref_c = comps.get(b.instances[0])
            cw = int(round(ref_c.x2 - ref_c.x1)) if ref_c else 0
            ch = int(round(ref_c.y2 - ref_c.y1)) if ref_c else 0
            expansion_map[b.id] = []
            for inst_name in b.instances:
                parent = comps.get(inst_name)
                if parent is None:
                    continue
                oi = orients.get(inst_name)
                if oi is None and inst_name not in warned_orient:
                    warned_orient.add(inst_name)
                    print(f"WARNING: instance {inst_name} matches the "
                          f"template under no orientation — falling back to "
                          f"translation; copied topologies may be "
                          f"mis-transformed")
                dx = int(round(parent.x1))
                dy = int(round(parent.y1))
                new_w = buda.BundleWrapper()
                clone = self._clone_hbundle_with_id(b, next_id)
                next_id += 1
                # Each instance wrapper carries the nets that physically live
                # in that instance and names only its own instance path.
                clone.net_names = donor_nets.get((b.id, inst_name),
                                                 list(b.net_names))
                clone.instances = [inst_name]
                # Re-map the busterm ids from the template's REFERENCE
                # instance (b.instances[0]) to THIS occurrence, so the
                # expanded wrapper's entry/exit_busterm_ids — and the
                # per-instance `bundle_busterm` links _add_expanded_bundle
                # persists from them — name the actual instance (core_i2/…,
                # not the reference core_i1/…).  Without this the second+
                # occurrences inherited the reference instance's links, so
                # any consumer of HBundle.entry/exit_busterm_ids on a resumed
                # `load_pipeline expanded` session resolved the wrong
                # instance (Codex #368).  `_qual` in _add_expanded_bundle
                # still handles a purely cell-local id (no '/').
                ref_inst = b.instances[0] if b.instances else ""

                def _reinst(bt, _ref=ref_inst, _inst=inst_name):
                    if not _ref or _ref == _inst:
                        return bt
                    for pre, mk in ((f"bt:{_ref}/", f"bt:{_inst}/"),
                                    (f"{_ref}/", f"{_inst}/")):
                        if bt.startswith(pre):
                            return mk + bt[len(pre):]
                    return bt
                clone.entry_busterm_ids = [_reinst(x)
                                           for x in b.entry_busterm_ids]
                clone.exit_busterm_ids = [_reinst(x)
                                          for x in b.exit_busterm_ids]
                new_w.input.original_bundle = clone
                new_w.input.width = w.input.width
                # R2d: the template's resolved NDR spec rides to every
                # instance (donor nets resolve to the same rule — the
                # bundler's class-congruence check guarantees it); the
                # rule's LAYER restriction is re-derived by
                # _apply_layer_policies' NDR re-application on the
                # expanded set, like every other mask.
                new_w.input.ndr = w.input.ndr
                # Map each template candidate to instance coords — through
                # the instance's detected orientation over the cell box, then
                # the translation — AND qualify its cell-local block names
                # (segments' seg_busterms annotation, connected_block_names,
                # feedthru_blocks) with the instance path, so ConnTopology's
                # authoritative endpoint annotation resolves against the
                # global floorplan (no geometric-fallback mis-taps).
                if oi and oi != "N":
                    new_w.input.candidates = [
                        buda.transform_topology(t, oi, cw, ch, dx, dy,
                                                inst_name)
                        for t in w.input.candidates]
                else:
                    new_w.input.candidates = [
                        buda.offset_topology(t, dx, dy, inst_name)
                        for t in w.input.candidates]
                # Fan-in template: re-derive the per-bit taper for THIS
                # instance from its own donor nets' endpoints (Codex #276:
                # a replica's net names need not sort in the template's
                # driver order, so the copied seg_bits indices could taper
                # bits along the wrong driver branches).  The expanded
                # candidates carry instance-qualified block names, matching
                # the donor metadata's full paths; the template's own
                # instance re-derives with the template metadata
                # (idempotent).  Derivation failures fall back to
                # all-segments — conservative, and reported by the flat
                # fidelity machinery where applicable.
                if b.net_drivers:
                    d_drvs, d_rcvs = donor_fanin.get(
                        (b.id, inst_name),
                        (list(b.net_drivers),
                         [list(r) for r in b.net_receivers]))
                    clone.net_drivers = d_drvs
                    clone.net_receivers = d_rcvs
                    # original_bundle was assigned BY VALUE above — re-assign
                    # so the wrapper's copy carries the donor metadata.
                    new_w.input.original_bundle = clone
                    cands = new_w.input.candidates
                    for t in cands:
                        buda.derive_fanin_seg_bits(t, self.fp, d_drvs, d_rcvs)
                    new_w.input.candidates = cands
                # Reserve the instance footprint: until this local bundle is
                # planned, its demand is parked as virtual usage so earlier
                # (global) bundles leave room over the cell interior.
                new_w.hier.has_reservation = True
                new_w.hier.res_x1 = dx
                new_w.hier.res_y1 = dy
                new_w.hier.res_x2 = int(round(parent.x2))
                new_w.hier.res_y2 = int(round(parent.y2))
                # (block-name qualification now done inside offset_topology above)
                # Propagate topology pinning from template to each instance.
                # Candidate indices are preserved (expansion offsets coordinates
                # but keeps the same ordering as the template candidate list), so
                # a super-candidate group pin (raw indices) copies directly too
                # (Codex — else a pre-`run_planner hier` group pin was dropped and
                # every instance did a full sweep).
                new_w.input.topology_pinned = w.input.topology_pinned
                new_w.plan.selected_topology_index = w.plan.selected_topology_index
                new_w.input.pinned_group = list(w.input.pinned_group)
                if w.input.pinned_seg_layers:
                    if oi in ("W", "E", "FW", "FE"):
                        # 90/270 swaps every segment H<->V: the pinned layers
                        # are direction-invalid for this instance — drop them
                        # and let the planner re-assign (bottom-up cells never
                        # reach here; their guard refuses 90° instances).
                        if inst_name not in warned_orient:
                            warned_orient.add(inst_name)
                            print(f"WARNING: instance {inst_name} is "
                                  f"90°-rotated ({oi}) — pinned per-segment "
                                  f"layers dropped for it (H<->V swap); the "
                                  f"planner re-assigns its layers")
                    else:
                        new_w.input.pinned_seg_layers = \
                            list(w.input.pinned_seg_layers)
                # Bottom-up template instance: a uniform copy of the local
                # solve (index + layers pinned above).  locked wrappers are
                # planned first (later bundles detour their committed usage)
                # and are never rip-up victims or replan/negotiate targets.
                # Requires a full pin — a template the local solve could not
                # plan stays unlocked rather than freezing an unplanned state.
                new_w.hier.locked = (
                    self._bu_cell_of(b.cell_context) in bottom_up
                    and w.input.topology_pinned
                    and bool(w.input.pinned_seg_layers))
                # Register the instance's INHERITED candidate uids (the
                # template-pool copies, transformed — incl. any template
                # USER candidate replicated here).  _add_expanded_bundle
                # persists a non-selected USER candidate for an instance
                # only when its uid is NOT in this set, so a template-level
                # USER alternative never multiplies into per-instance extra
                # rows (Codex #358); only candidates committed ON the
                # instance after expansion count as instance-local.
                self._inherited_uids[clone.id] = {
                    buda.topo_uid(t) for t in new_w.input.candidates}
                expansion_map[b.id].append(new_w)
                wrapper_at[(b.id, inst_name)] = new_w
                result.append(new_w)
        # Replicas map to the template wrapper at their instance, so a pin on
        # a replica's bundle ID still reaches the wrapper that routes it.
        for rid, key in replica_wrapper_of.items():
            if key in wrapper_at:
                expansion_map[rid] = [wrapper_at[key]]
        return result, expansion_map

    def _make_topo_gen(self, fp, use_center=False, use_double_detour=False,
                       use_multi_trunk=False, use_hanan_loci=True,
                       use_spine_relays=False):
        """Create a TopologyGenerator on fp with the current layer stack.

        use_hanan_loci defaults to True (the hanan_loci default flip) and is
        ALWAYS stamped on the generator explicitly, so a caller that resolved
        an opt-out (`no_hanan_loci` command flag or a per-bundle
        `no_hanan_loci` knob memo) gets a midpoint-only generator regardless
        of the C++ member default.

        use_spine_relays (default off — the `spine_relays` command flag /
        knob-memo opt-in) switches MST relay-hub completion from the
        over-the-cell bracket chain to a single collector SPINE the majority
        stubs T-tap independently (src/topology.cpp complete_relay_junctions).
        Unlike use_hanan_loci there is NO opt-OUT token, so it is stamped ONLY
        when opting IN: a caller without the token keeps the generator's
        constructor default, which honors the BUDA_SPINE_RELAYS env hook (the
        documented global opt-in for A/B-ing a corpus without editing each
        flow — topology.h).  An explicit token / knob memo wins over the env."""
        tg = buda.TopologyGenerator(fp)
        h = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
        v = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
        if h != -1 and v != -1:
            tg.set_layer_ids(h, v)
            # Inform the generator of ALL same-direction layers so keepout
            # avoidance only suppresses a trunk position when every routable
            # layer for that direction is blocked.  Without this, a keepout on
            # the preferred (TOP) layer alone would wrongly cull candidates the
            # planner could legally reassign to a free same-direction layer.
            h_all = self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL)
            v_all = self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)
            if h_all:
                tg.set_all_h_layers(h_all)
            if v_all:
                tg.set_all_v_layers(v_all)
        if use_center:
            tg.set_busterm_mode(False)
        if use_double_detour:
            tg.set_double_detour(True)
        if use_multi_trunk:
            tg.set_multi_trunk(True)
        # Default-on knob: set unconditionally (see docstring) so an opt-out
        # actually reaches the generator.
        tg.set_hanan_loci(bool(use_hanan_loci))
        # Default-off opt-in with NO opt-out token: stamp ONLY when opting in,
        # so a flow without the token keeps the constructor's env-derived
        # default and the BUDA_SPINE_RELAYS global opt-in still works (Codex
        # #463 P2).  An explicit token / knob memo passes True and wins.
        if use_spine_relays:
            tg.set_spine_relays(True)
        # Session-scope opt-ins (set_trim_mst_legs / set_trim_trunk_stubs), not
        # per-command tokens, so they need no slot in the generation-knob tuple
        # or its persisted memo.
        #
        # TRI-STATE, and the None matters: an UNSET flag must leave the
        # constructor's env-derived default alone, or the BUDA_*_TRIM corpus
        # A/B hooks stop working.  But an EXPLICIT value must be stamped in
        # BOTH directions — stamping only `True` meant `set_trim_mst_legs off`
        # under BUDA_MST_LEG_TRIM=1 never reached the setter, so the generator
        # kept trimming while the command printed "disabled" (Codex #745).  The
        # documented contract is that an explicit command wins over the env.
        if getattr(self, "_trim_mst_legs", None) is not None:
            tg.set_mst_leg_trim(bool(self._trim_mst_legs))
        if getattr(self, "_trim_trunk_stubs", None) is not None:
            tg.set_trunk_stub_trim(bool(self._trim_trunk_stubs))
        return tg

    def _make_layer_names(self):
        """Build a layer_id -> name dict from def_layer commands, with fallback defaults."""
        names = {4: 'M4', 5: 'M5', 6: 'M6'}
        for name, lid in self._layer_name_map.items():
            names[lid] = name
        return names

    @staticmethod
    def _pin_instance(pin):
        """Instance (block) name for a pin, matching the bundler's rule
        (bundler.cpp: substr up to the LAST '.').  A block name may itself contain
        dots (e.g. 'u.core' with pin 'u.core.tx'), so split on the last dot, not the
        first — otherwise the endpoint resolves to 'u' and misroutes / fails to
        validate against the real block 'u.core'."""
        return pin.rsplit('.', 1)[0]

    def _bundle_endpoints(self, w):
        """Derive a bundle's generation endpoints from ALL of its nets.

        Single-driver bundle (STRICT, or a CONVERGENT group that degenerates
        to one): the first net's (driver, receivers), exactly the historical
        behavior — byte-identical for every existing flow.

        Multi-driver bundle (CONVERGENT fan-in: nets from DIFFERENT driver
        blocks sharing a receiver set): a single src->dst pair cannot
        represent it — the old derivation routed the whole bundle from ONE
        arbitrary driver and silently left the rest unrouted
        (docs/internal/convergent_bundling.md).  Root the tree at the shared
        SINK instead and hand every driver block (plus any receiver block
        beyond the root) to the generator as leaves: generate_candidates'
        multicast trunk+branch / MST machinery connects a root to N leaves
        direction-agnostically, so the same shapes serve fan-in with the
        arrows reversed, and connected_block_names then carries every
        driver — the contract check_topo and the net-driver fidelity check
        verify.

        Returns (src, dsts, fanin) — fanin True on the multi-driver path —
        or None when no net has endpoint info (the hier flow)."""
        nets = w.input.original_bundle.get_net_names()
        eps = [(n, self._net_endpoints.get(n)) for n in nets]
        eps = [(n, e) for n, e in eps if e is not None]
        if not eps:
            return None
        first = eps[0][1]
        drivers = []
        for _, (drv, _rcvs) in eps:
            if drv not in drivers:
                drivers.append(drv)
        if len(drivers) == 1:
            # Fan-OUT (DIVERGENT): one driver, and the nets do NOT all reach
            # the same receiver blocks.  The historical single-net derivation
            # would hand the generator the FIRST net's receiver alone and
            # silently leave the other blocks unrouted — the exact fault the
            # fan-in path below was written to fix, mirrored.  Root at the
            # driver, every receiver block a leaf.
            rcv_sets = {tuple(sorted(set(r))) for _, (_d, r) in eps}
            if len(rcv_sets) > 1:
                root = first[0]
                leaves = []
                for _, (_drv, rcvs) in eps:
                    for r in rcvs:
                        if r != root and r not in leaves:
                            leaves.append(r)
                if leaves:
                    return root, leaves, True
            return first[0], list(first[1]), False
        # Fan-in: root at the shared sink (the first net's first receiver —
        # safe because CONVERGENT's signature guarantees every net in the
        # bundle shares the same receiver-instance set); leaves = every
        # driver + any receiver beyond the root.
        root = first[1][0]
        leaves = [d for d in drivers if d != root]
        for _, (_drv, rcvs) in eps:
            for r in rcvs:
                if r != root and r not in leaves:
                    leaves.append(r)
        return root, leaves, True

    @staticmethod
    def _xlevel_fanin_endpoints(b):
        """Cross-level fan-in generation endpoints — (fanin, root, leaves).

        A multi-driver cross-level bundle (CONVERGENT/COMBINED) roots the tree
        at the shared sink with every driver as a leaf.  Uses the per-net
        HBundle.net_drivers/net_receivers when present (fresh bundling); on a
        RESUMED session — where net_drivers is not persisted — it recovers the
        same endpoints by parsing the persisted `FANIN:root|FROM:leaves` reason,
        so the route stays complete (only the per-bit taper falls back to
        conservative full width).  Returns (False, None, []) for a single-driver
        cross-level bundle (the historical drv_spec_path path)."""
        # Fan-OUT first: a DIVERGENT bundle DOES carry net_drivers (one
        # driver, repeated per bit), so the single-driver early-out below
        # would reject it before the reason is ever consulted.
        if b.reason.startswith('FANOUT:'):
            root, leaves = HierMixin._parse_bundle_reason(b.reason)
            if root and leaves:
                return True, root, leaves
            return False, None, []
        if b.net_drivers:
            drivers = list(dict.fromkeys(b.net_drivers))
            if len(drivers) <= 1:
                return False, None, []
            receivers = sorted({r for rl in b.net_receivers for r in rl})
            if not receivers:
                return False, None, []
            root = receivers[0]
            leaves = [d for d in drivers if d != root]
            leaves += [r for r in receivers if r != root and r not in drivers]
            return (True, root, leaves) if leaves else (False, None, [])
        if b.reason.startswith('FANIN:'):
            root, leaves = HierMixin._parse_bundle_reason(b.reason)
            if root and leaves:
                return True, root, leaves
        return False, None, []

    def _fanin_net_endpoints(self, w):
        """Per-bit endpoint lists for a FAN-IN bundle: ([driver_block per
        bit], [receiver_blocks per bit]) in the bundle's net order (= the
        global bit index), or None when the bundle is not a multi-driver
        fan-in or any net lacks endpoint info."""
        ep = self._bundle_endpoints(w)
        if ep is None or not ep[2]:
            return None
        drvs, rcvs = [], []
        for n in w.input.original_bundle.get_net_names():
            e = self._net_endpoints.get(n)
            if e is None:
                return None
            drvs.append(e[0])
            rcvs.append(list(e[1]))
        return drvs, rcvs

    def _derive_fanin_bits_all(self, selected_only=False):
        """Derive per-segment bit membership (Topology.seg_bits — the tapered
        fan-in model) for every FAN-IN bundle's candidates: each segment then
        carries only the bits whose driver→sink path uses it, and the
        planner / NUTS / DNUTS width model tapers accordingly (a driver stub
        is as wide as its own sub-bus, not the whole bundle).

        Called before planning (all candidates — the planner scores each) and
        before the NUTS solve (selected only — covers the resume path where
        run_planner was not re-run, and re-derives an adopted dogleg split).
        Flat flow only; idempotent (derivation overwrites the whole map)."""
        if (getattr(self, "_hier_bundles_orig", None)
                or self._hier_expansion_map or not self._net_endpoints):
            return
        for w in self.bundles:
            eps = self._fanin_net_endpoints(w)
            if eps is None or not w.input.candidates:
                continue
            drvs, rcvs = eps
            sel = w.plan.selected_topology_index
            cands = w.input.candidates          # pybind copy semantics
            for i, t in enumerate(cands):
                if selected_only and i != sel:
                    continue
                buda.derive_fanin_seg_bits(t, self.fp, drvs, rcvs)
            w.input.candidates = cands

    def _derive_hier_fanin_bits(self, w, fp, local):
        """Per-bit taper for a HIER fan-in bundle: derive Topology.seg_bits
        on every candidate from the bundler-recorded per-net endpoints
        (HBundle.net_drivers/net_receivers, aligned with the sorted
        net_names).  local=True maps paths to cell-local leaf names (the
        case (a) frame); case (b) uses the depth-level paths as-is.  No-op
        for single-driver bundles (empty net_drivers).  seg_bits is derived
        (never persisted) and rides the per-instance expansion copies
        (offset/transform_topology copy the whole struct), so the planner /
        NUTS / DNUTS width model tapers in the hier flow exactly as in the
        flat flow; a resumed session falls back to conservative full
        width."""
        b = w.input.original_bundle
        drvs = list(b.net_drivers)
        if not drvs or fp is None:
            return
        rcvs = [list(r) for r in b.net_receivers]
        if local:
            drvs = [d.rsplit('/', 1)[-1] for d in drvs]
            rcvs = [[r.rsplit('/', 1)[-1] for r in rl] for rl in rcvs]
        cands = w.input.candidates
        for t in cands:
            buda.derive_fanin_seg_bits(t, fp, drvs, rcvs)
        w.input.candidates = cands

    def _validate_endpoint_blocks(self, net_name, src, dsts):
        """Fatal input validation: every block a net/bus connects to must exist in
        the floorplan.  get_block_bounds() silently returns {0,0,0,0} for an unknown
        name, so a typo'd endpoint (e.g. 'IOPAD' for the block 'IO_PAD') would route
        from the chip origin instead of the intended block, with no error.  Surface
        it as a fatal error and quit rather than misroute."""
        missing = [b for b in [src, *dsts] if not self.fp.has_block(b)]
        if not missing:
            return
        import difflib
        known = sorted(n for n, _ in self.fp.get_all_blocks())
        for b in missing:
            hint = difflib.get_close_matches(b, known, n=1)
            suffix = f" — did you mean '{hint[0]}'?" if hint else ""
            print(f"Error: net '{net_name}' references block '{b}', which is not "
                  f"defined in the floorplan{suffix}")
        print(f"  Defined blocks: {', '.join(known) if known else '(none)'}")
        sys.exit(1)
