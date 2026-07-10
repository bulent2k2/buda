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
import json
import os
import re
import sys

import buda


class HierMixin:

    def _add_expanded_bundle(self, w, sel, expanded_to_template):
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
        # Persist only the SELECTED (placed) topology for the instance, with the
        # planner's assigned layers; the template retains the full candidate set.
        topo = w.input.candidates[sel]
        tr = buda.TopoRow()
        tr.id = bid
        tr.cand_index = sel
        tr.type = topo.type
        tr.wirelength = topo.estimated_wirelength
        tr.trunk_location = topo.trunk_location
        tr.pass_through_count = topo.pass_through_count
        tr.connected_blocks = json.dumps(list(topo.connected_block_names))
        tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
        tr.is_selected = True
        tr.topo_uid = buda.topo_uid(topo)
        self.bdb.add_topology(tr)
        seg_layers = list(w.plan.seg_layers)
        for si, seg in enumerate(topo.segments):
            sr = buda.TopoSegRow()
            sr.id = bid
            sr.cand_index = sel
            sr.seg_index = si
            sr.x1, sr.y1 = seg.start.x, seg.start.y
            sr.x2, sr.y2 = seg.end.x, seg.end.y
            sr.layer_hint = seg.layer_hint
            sr.is_jog = seg.is_jog
            sr.edge_id = seg.edge_id
            sr.perp_clamp_lo = seg.perp_clamp_lo
            sr.perp_clamp_hi = seg.perp_clamp_hi
            sr.assigned_layer = int(seg_layers[si]) if si < len(seg_layers) else -1
            self.bdb.add_topology_segment(sr)
        # Logical seg-busterm links + TEG-over bridges for the instance's
        # selected topology, so a `load_pipeline expanded` resume restores its
        # connectivity (and any bridge) too.
        self._persist_topology_annotations(bid, sel, topo)

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

        for sel in data.get('selections', []):
            hint = sel['bundle_hint']
            matching = [w for w in self.bundles
                        if w.input.original_bundle.get_net_names() and
                           w.input.original_bundle.get_net_names()[0].startswith(hint)]
            if not matching:
                continue

            first_w = matching[0]
            bid = first_w.input.original_bundle.id

            # 1. Resolve which topology the sidecar points to.  Chain
            #    (Phase E1b): stable content uid (survives regeneration and
            #    list reordering) -> type+WL -> warned index hint.
            resolved_sidecar_idx = None
            sc_uid = sel.get('topo_uid')
            if sc_uid:
                for i, cand in enumerate(first_w.input.candidates):
                    if buda.topo_uid(cand) == sc_uid:
                        resolved_sidecar_idx = i
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
            n_adopted = 0   # wrappers newly pinned from the sidecar
            n_layered = 0   # wrappers that received layer overrides
            for w in matching:
                w_pinned = getattr(w.input, 'topology_pinned', False)
                target_idx = (w.plan.selected_topology_index if w_pinned
                              else resolved_sidecar_idx)

                if not w_pinned:
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
        Components without valid placement (x1 < 0) are always skipped.
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
            if r.x1 < 0 or r.y1 < 0:
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

    def _build_bdb_floorplan(self, depth):
        """Build a Floorplan with placed components at exactly this depth from BDB."""
        fp = buda.Floorplan()
        dx, dy = self._corner_margin
        if dx or dy:
            fp.set_global_corner_margin(dx, dy)
        for c in self.bdb.all_components():
            if c.depth == depth and c.x1 >= 0:
                fp.add_block(c.name, int(round(c.x1)), int(round(c.y1)),
                             int(round(c.x2)), int(round(c.y2)))
        return fp

    def _bottom_up_congruence_issues(self, cell, comps=None):
        """Verify every instance of `cell` is a pure translated copy of the
        first one — the precondition for bottom-up template planning, whose
        per-instance copies are translation-only (offset_topology).

        Checks, per instance: identity orientation 'N', equal outline
        dimensions, and an identical FULL SUBTREE — every descendant matched
        by path suffix on (cell type, orient, bbox relative to the instance
        origin). The geometric comparison matters because rotate_comp /
        flip_comp on a *hierarchical* block rewrite the children's absolute
        bboxes and deliberately keep orient='N' — the orient token alone
        cannot detect such an instance; the full-depth walk matters because
        a moved GRANDCHILD leaves outline + direct children matching, and
        the per-descendant orient matters because a rotated *square* leaf
        descendant leaves the geometry matching too.

        Returns a list of human-readable issue strings (empty = congruent).
        """
        if comps is None:
            comps = self.bdb.all_components()
        insts, by_parent = [], {}
        for c in comps:
            if c.cell == cell:
                insts.append(c)
            by_parent.setdefault(c.parent_id, []).append(c)
        issues = [f"{c.name}: orientation {c.orient} (need 'N')"
                  for c in insts if c.orient != "N"]
        if len(insts) < 2:
            return issues

        def dims(c):
            return (round(c.x2 - c.x1, 3), round(c.y2 - c.y1, 3))

        def shape(inst):
            # Full subtree, keyed by path suffix relative to the instance.
            out, stack, prefix = {}, [inst], inst.name + "/"
            while stack:
                node = stack.pop()
                for k in by_parent.get(node.id, []):
                    rel = (k.name[len(prefix):]
                           if k.name.startswith(prefix) else k.name)
                    out[rel] = (k.cell, k.orient,
                                round(k.x1 - inst.x1, 3),
                                round(k.y1 - inst.y1, 3),
                                round(k.x2 - inst.x1, 3),
                                round(k.y2 - inst.y1, 3))
                    stack.append(k)
            return out

        ref = insts[0]
        ref_shape = shape(ref)
        for c in insts[1:]:
            if dims(c) != dims(ref):
                issues.append(f"{c.name}: outline {dims(c)} differs from "
                              f"{ref.name} {dims(ref)}")
                continue
            s = shape(c)
            if s != ref_shape:
                bad = (sorted(set(ref_shape) ^ set(s))
                       or sorted(k for k in ref_shape
                                 if s.get(k) != ref_shape[k]))
                issues.append(f"{c.name}: subtree differs from "
                              f"{ref.name} (e.g. {', '.join(bad[:3])})")
        return issues

    def _build_cell_local_floorplan(self, parent_comp_name):
        """Build a Floorplan in cell-local coords for sub-components of parent."""
        comps = {c.name: c for c in self.bdb.all_components()}
        parent = comps.get(parent_comp_name)
        if parent is None:
            return None
        fp = buda.Floorplan()
        for c in comps.values():
            if c.parent_id == parent.id and c.x1 >= 0:
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
        Returns (None, []) on failure."""
        try:
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

        A net named '<bus>_<idx>' or '<bus>_b<idx>' (the two forms add_bus/add_net
        emit — e.g. 'bus_007_3', 'bus_077_b00') is folded into its bus with a
        compressed bit range like 'bus_077[0:59]'; nets with no numeric suffix are
        listed verbatim.  First-seen order is preserved; truncated past max_len."""
        groups, order, seen = {}, [], set()
        for nm in net_names:
            m = re.match(r'^(.*?)_([A-Za-z]*)(\d+)$', nm)
            if m:
                key = (m.group(1), m.group(2))      # (bus, bit-tag) e.g. ('bus_077','b')
                if key not in groups:
                    groups[key] = []
                    order.append(('bus', key, m.group(1)))
                groups[key].append(int(m.group(3)))
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

    def _generate_hier_topo_one(self, w, use_center, use_double_detour,
                                fp_cache, comps_by_name, use_multi_trunk=False):
        """Generate topology candidates for a single HBundle wrapper.

        Updates w.input.candidates in place. Returns candidate count.
        fp_cache is a dict shared across calls; pass {} for a fresh cache.
        comps_by_name is {name: ComponentRow} from bdb.all_components().
        use_multi_trunk adds two-level BITRUNK_HVH/VHV datapath trees (opt-in),
        as in the flat generate_topologies.
        """
        b = w.input.original_bundle
        nets_suffix = self._bundle_nets_suffix(w)   # rides on each per-bundle log line
        if b.cell_context and b.entry_busterm_ids:
            # Case (a): cell-local floorplan
            parent_name = b.instances[0] if b.instances else None
            if parent_name is None:
                print(f"  Warning: bundle {b.id} has cell_context but no instances — skipping")
                return 0
            cache_key = ('cell', parent_name)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_cell_local_floorplan(parent_name)
            cell_fp = fp_cache[cache_key]
            if cell_fp is None:
                print(f"  Warning: could not build cell-local fp for {parent_name!r} — skipping")
                return 0
            tg = self._make_topo_gen(cell_fp, use_center, use_double_detour,
                                     use_multi_trunk)
            src_local = b.entry_busterm_ids[0].removeprefix('bt:').rsplit('/', 1)[-1]
            dsts_local = [e.removeprefix('bt:').rsplit('/', 1)[-1] for e in b.exit_busterm_ids]
            old_pin_uid = self._pinned_uid(w)
            kept_user = self._user_candidates(w)
            w.input.candidates = tg.generate_candidates(src_local, dsts_local)
            self._reset_plan_for_regen(w, old_pin_uid, kept_user)
            label = f"{src_local}→{dsts_local[0]}"
            n = len(w.input.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  [cell:{b.cell_context}] {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"{n} candidates  [cell:{b.cell_context}] {nets_suffix}")
            return n

        elif b.drv_spec_depth >= 0:
            # Case (c): cross-level — custom floorplan from actual endpoint blocks
            drv_comp = comps_by_name.get(b.drv_spec_path)
            if drv_comp is None:
                print(f"  Warning: driver comp {b.drv_spec_path!r} not found — "
                      f"skipping bundle {b.id}")
                return 0
            fp = buda.Floorplan()
            dx, dy = self._corner_margin
            if dx or dy:
                fp.set_global_corner_margin(dx, dy)
            fp.add_block(b.drv_spec_path,
                         int(round(drv_comp.x1)), int(round(drv_comp.y1)),
                         int(round(drv_comp.x2)), int(round(drv_comp.y2)))
            ok = True
            for rpath in b.rcv_spec_paths:
                rc = comps_by_name.get(rpath)
                if rc is None:
                    print(f"  Warning: receiver comp {rpath!r} not found — "
                          f"skipping bundle {b.id}")
                    ok = False; break
                fp.add_block(rpath,
                             int(round(rc.x1)), int(round(rc.y1)),
                             int(round(rc.x2)), int(round(rc.y2)))
            if not ok:
                return 0
            tg = self._make_topo_gen(fp, use_center, use_double_detour,
                                     use_multi_trunk)
            old_pin_uid = self._pinned_uid(w)
            kept_user = self._user_candidates(w)
            w.input.candidates = tg.generate_candidates(b.drv_spec_path, list(b.rcv_spec_paths))
            self._reset_plan_for_regen(w, old_pin_uid, kept_user)
            label = (f"{b.drv_spec_path}→{b.rcv_spec_paths[0]}"
                     if len(b.rcv_spec_paths) == 1
                     else f"{b.drv_spec_path}→[{','.join(b.rcv_spec_paths)}]")
            n = len(w.input.candidates)
            tag = f"[cross-level D{b.drv_spec_depth}→D{b.rcv_spec_depth}]"
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  {tag} {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates  {tag} {nets_suffix}")
            return n

        else:
            # Case (b): same-level cross-block — BDB floorplan at the
            # endpoints' depth.  b.level is the routing-context depth (the
            # endpoints' common ancestor), which can be shallower than the
            # endpoint paths themselves; the blocks to route between live at
            # the endpoint depth (path segments − 1).
            src, dsts = self._parse_bundle_reason(b.reason)
            ep_depth = src.count('/') if src else b.level
            cache_key = ('depth', ep_depth)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_bdb_floorplan(ep_depth)
            depth_fp = fp_cache[cache_key]
            tg = self._make_topo_gen(depth_fp, use_center, use_double_detour,
                                     use_multi_trunk)
            if src is None:
                print(f"  Warning: could not parse reason for bundle {b.id}: {b.reason!r}")
                return 0
            old_pin_uid = self._pinned_uid(w)
            kept_user = self._user_candidates(w)
            w.input.candidates = tg.generate_candidates(src, dsts)
            self._reset_plan_for_regen(w, old_pin_uid, kept_user)
            label = f"{src}→{dsts[0]}" if len(dsts) == 1 else f"{src}→[{','.join(dsts)}]"
            n = len(w.input.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted! {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates {nets_suffix}")
            return n

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
            cache_key = ('xlevel', b.id)
            if cache_key not in fp_cache:
                fp = None
                drv_comp = comps_by_name.get(b.drv_spec_path)
                if drv_comp is not None:
                    fp = buda.Floorplan()
                    dx, dy = self._corner_margin
                    if dx or dy:
                        fp.set_global_corner_margin(dx, dy)
                    fp.add_block(b.drv_spec_path,
                                 int(round(drv_comp.x1)), int(round(drv_comp.y1)),
                                 int(round(drv_comp.x2)), int(round(drv_comp.y2)))
                    for rpath in b.rcv_spec_paths:
                        rc = comps_by_name.get(rpath)
                        if rc is None:
                            fp = None
                            break
                        fp.add_block(rpath,
                                     int(round(rc.x1)), int(round(rc.y1)),
                                     int(round(rc.x2)), int(round(rc.y2)))
                fp_cache[cache_key] = fp
            return fp_cache[cache_key]

        # Case (b): same-level cross-block — BDB floorplan at the endpoint depth.
        src, _ = self._parse_bundle_reason(b.reason)
        ep_depth = src.count('/') if src else b.level
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
        return nb

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
        bu_cells = set(self.bdb.bottom_up_cells()) if self.bdb else set()
        if not bu_cells or not self.bundles:
            return
        # Same replica detection as _expand_hier_bundles: a replica's routing
        # is carried by its template's per-instance expansion, so only the
        # template participates in the local solve.
        cell_ids = {w.input.original_bundle.id for w in self.bundles
                    if w.input.original_bundle.cell_context}
        by_cell = {}
        for w in self.bundles:
            b = w.input.original_bundle
            if (not b.cell_context or not b.instances
                    or b.cell_context not in bu_cells
                    or b.parent_id in cell_ids       # replica
                    or not w.input.candidates):
                continue
            by_cell.setdefault(b.cell_context, []).append(w)
        deepest_first = sorted(
            by_cell,
            key=lambda c: -max(w.input.original_bundle.level
                               for w in by_cell[c]))
        for cell in deepest_first:
            wrappers = by_cell[cell]
            fp = self._build_cell_local_floorplan(
                wrappers[0].input.original_bundle.instances[0])
            if fp is None:
                print(f"WARNING: bottom-up cell '{cell}': no placed instance "
                      f"to derive the cell-local floorplan — skipped")
                continue
            planner = buda.CongestionPlanner(fp, self.layers)
            for pname, pval in self._planner_params.items():
                planner.set_planner_param(pname, pval)
            planner.set_track_pitch(self._nuts_pitch)
            planner.build_congestion_map()
            assignments = planner.optimize_topologies(wrappers, iterations)
            bid_to_w = {w.input.original_bundle.id: w for w in wrappers}
            for asn in assignments:
                w = bid_to_w.get(asn.bundle_id)
                if w is None:
                    continue
                w.plan.selected_topology_index = asn.topo_index
                w.plan.seg_layers = list(asn.seg_layers)
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.input.topology_pinned = True
                w.input.pinned_seg_layers = list(asn.seg_layers)
            n_inst = len(wrappers[0].input.original_bundle.instances)
            print(f"[BottomUp] cell '{cell}': {len(assignments)} template "
                  f"bundle(s) planned locally; decision pinned for "
                  f"{n_inst} instance(s)")

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
        replica_wrapper_of = {}  # replica id → (template id, instance path)
        for w in bundles:
            b = w.input.original_bundle
            if (b.cell_context and b.parent_id in cell_bundle_ids
                    and b.instances):
                donor_nets[(b.parent_id, b.instances[0])] = list(b.net_names)
                replica_wrapper_of[b.id] = (b.parent_id, b.instances[0])
        # Start synthetic IDs above any real bundle ID in the set.
        max_id = max((w.input.original_bundle.id for w in bundles), default=-1)
        next_id = max_id + 1
        result = []
        expansion_map = {}  # original bundle id → [expanded wrappers]
        wrapper_at = {}     # (template id, instance path) → expanded wrapper
        checked_bu = set()
        warned_orient = set()
        for w in bundles:
            b = w.input.original_bundle
            if not b.cell_context or not b.instances:
                result.append(w)
                continue
            if b.id in replica_wrapper_of:
                continue   # replica: covered by its template's expansion
            # Expansion is translation-only (offset_topology), so a bottom-up
            # cell requires congruent (purely translated) instances — hard
            # error otherwise, since uniform copies are its whole contract.
            # Checked here (not only at set_bottom_up time) because placement
            # may have changed since the cell was marked.
            if b.cell_context in bottom_up and b.cell_context not in checked_bu:
                checked_bu.add(b.cell_context)
                issues = self._bottom_up_congruence_issues(
                    b.cell_context, comps.values())
                if issues:
                    raise RuntimeError(
                        f"run_planner hier: bottom-up cell "
                        f"'{b.cell_context}' has non-congruent instances "
                        f"(translation-only copies impossible): "
                        f"{'; '.join(issues[:4])}")
            expansion_map[b.id] = []
            for inst_name in b.instances:
                parent = comps.get(inst_name)
                if parent is None:
                    continue
                # For non-bottom-up cells a rotated/mirrored instance is only
                # a quality risk (each instance is planned separately), so
                # warn rather than error — once per instance, not per
                # (bundle × instance): a rotated instance shared by many
                # bundles would otherwise repeat the identical line.
                if (parent.orient != "N" and parent.cell not in bottom_up
                        and inst_name not in warned_orient):
                    warned_orient.add(inst_name)
                    print(f"WARNING: instance {inst_name} has orientation "
                          f"{parent.orient}; hier expansion is translation-"
                          f"only — copied topologies may be mis-transformed")
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
                new_w.input.original_bundle = clone
                new_w.input.width = w.input.width
                # Offset each template candidate to instance coords AND qualify
                # its cell-local block names (segments' seg_busterms annotation,
                # connected_block_names, feedthru_blocks) with the instance path,
                # so ConnTopology's authoritative endpoint annotation resolves
                # against the global floorplan (no geometric-fallback mis-taps).
                new_w.input.candidates = [
                    buda.offset_topology(t, dx, dy, inst_name)
                    for t in w.input.candidates]
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
                # but keeps the same ordering as the template candidate list).
                new_w.input.topology_pinned = w.input.topology_pinned
                new_w.plan.selected_topology_index = w.plan.selected_topology_index
                if w.input.pinned_seg_layers:
                    new_w.input.pinned_seg_layers = list(w.input.pinned_seg_layers)
                # Bottom-up template instance: a uniform copy of the local
                # solve (index + layers pinned above).  locked wrappers are
                # planned first (later bundles detour their committed usage)
                # and are never rip-up victims or replan/negotiate targets.
                # Requires a full pin — a template the local solve could not
                # plan stays unlocked rather than freezing an unplanned state.
                new_w.hier.locked = (b.cell_context in bottom_up
                                     and w.input.topology_pinned
                                     and bool(w.input.pinned_seg_layers))
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
                       use_multi_trunk=False):
        """Create a TopologyGenerator on fp with the current layer stack."""
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
