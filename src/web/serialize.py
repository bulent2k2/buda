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

"""Struct -> JSON serializers for the web backend.

PURE and PRINT-FREE: every function reads the pybind C++ result structs
(`session.bundles[i].{input,plan}`, `session.nuts_result`,
`session.detailed_result`, `buda.ConnTopology.segs()`, Floorplan accessors) and
returns plain dicts/lists of JSON scalars.  The topology traversal mirrors the
golden-test walker in `tools/topo_snapshot.py`, emitting dicts instead of text.

Nothing here parses the human-facing print output of any command — all routed
data is available structured on the objects above.
"""

# ConnTopology marks an unbounded slide window / undefined pull with an INT
# sentinel (~1e9 magnitude); mirror the CLI/viz threshold so the client can tell
# "unconstrained" (JSON null) from a real bound.  (viz_common.py uses 1e9;
# nutsflow/reports use 1e8 — 1e9 is the safe upper discriminator.)
_SENTINEL = 10 ** 9
_INT_MIN = -2147483648      # pull_break "no breakpoint" sentinel
_INT_MAX = 2147483647       # Segment.perp_clamp_hi "unclamped" sentinel


def _rect(r):
    return {"x1": r.x1, "y1": r.y1, "x2": r.x2, "y2": r.y2}


def _slide(v):
    """A slide-window / clamp bound: JSON null when it is the unbounded sentinel."""
    return None if abs(v) >= _SENTINEL else v


def _pull_break(v):
    return None if v == _INT_MIN else v


def _clamp(v):
    return None if v in (_INT_MIN, _INT_MAX) or abs(v) >= _SENTINEL else v


def _net_names(original_bundle):
    """Bundle net names, tolerant of the flat vs hier bundle interface."""
    try:
        return list(original_bundle.get_net_names())
    except Exception:
        return list(getattr(original_bundle, "net_names", []) or [])


def serialize_state(session):
    """Lightweight `StateSummary` — which stages have run + a per-bundle digest.

    stages_run is inferred from observable session state (no bespoke flags):
      bundler    -> any bundles exist
      topologies -> any bundle has candidates
      planner    -> any bundle's plan carries assigned seg_layers
      nuts/dnuts -> the corresponding result object exists
    """
    bundles = list(getattr(session, "bundles", []) or [])

    def _planned(w):
        try:
            return len(list(w.plan.seg_layers)) > 0
        except Exception:
            return False

    stages_run = {
        "bundler": len(bundles) > 0,
        "topologies": any(len(w.input.candidates) > 0 for w in bundles),
        "planner": any(_planned(w) for w in bundles),
        "nuts": getattr(session, "nuts_result", None) is not None,
        "dnuts": getattr(session, "detailed_result", None) is not None,
    }

    bundle_digests = []
    for w in bundles:
        ob = w.input.original_bundle
        bundle_digests.append({
            "id": ob.id,
            "net_count": len(_net_names(ob)),
            "num_candidates": len(w.input.candidates),
            "selected_index": w.plan.selected_topology_index,
            "pinned": bool(getattr(w.input, "topology_pinned", False)),
            "width": w.input.width,
        })

    return {
        "stages_run": stages_run,
        "bundles": bundle_digests,
        "has_bdb": getattr(session, "bdb", None) is not None,
    }


# ── floorplan / grid ─────────────────────────────────────────────────────────
def serialize_floorplan(fp):
    """Blocks (bbox + real rects + container flag + corner margin) and keepouts."""
    blocks = []
    for name, rect in fp.get_all_blocks():
        rects = fp.get_block_rects(name)         # [] for single-rect blocks
        # rects entries may be Rect or (x1,y1,x2,y2) tuples — normalize.
        norm_rects = []
        for rr in rects:
            if hasattr(rr, "x1"):
                norm_rects.append([rr.x1, rr.y1, rr.x2, rr.y2])
            else:
                norm_rects.append([rr[0], rr[1], rr[2], rr[3]])
        cm = fp.get_block_corner_margin(name)
        blocks.append({
            "name": name,
            "bbox": _rect(rect),
            "rects": norm_rects,
            "is_container": bool(fp.is_container(name)),
            "corner_margin": {"dx": cm.dx, "dy": cm.dy},
        })
    keepouts = []
    for koz in fp.get_keepout_zones():
        keepouts.append({"bbox": _rect(koz.bbox),
                         "layers": sorted(koz.layer_ids)})
    return {"blocks": blocks, "keepouts": keepouts}


def serialize_hanan(fp):
    xs, ys = fp.get_hanan_grid()
    return {"xs": list(xs), "ys": list(ys)}


# ── topology + connectivity analysis ─────────────────────────────────────────
def _busterm(bt):
    if bt is None:
        return None
    return {
        "block_name": bt.block_name,
        "teg_mode": str(bt.teg_mode).split(".")[-1],
        "bbox": _rect(bt.bbox),
        "orig_bbox": _rect(bt.orig_bbox),
        "rects": [_rect(r) for r in bt.rects],
    }


def _segment(sg):
    return {
        "start": {"x": sg.start.x, "y": sg.start.y},
        "end": {"x": sg.end.x, "y": sg.end.y},
        "horiz": sg.start.y == sg.end.y,
        "layer_hint": sg.layer_hint,
        "is_jog": bool(sg.is_jog),
        "edge_id": sg.edge_id,
        "perp_clamp_lo": _clamp(sg.perp_clamp_lo),
        "perp_clamp_hi": _clamp(sg.perp_clamp_hi),
    }


def _conn(cn):
    import buda
    is_bt = cn.kind == buda.SegConnKind.BUSTERM
    # face_coord / block_name are only meaningful for a BUSTERM conn; a SEG conn
    # leaves them uninitialized, so don't leak the garbage.
    return {
        "kind": "BUSTERM" if is_bt else "SEG",
        "block_name": cn.block_name if is_bt else None,
        "face_coord": cn.face_coord if is_bt else None,
        "seg_idx": cn.seg_idx,
        "at_pos": cn.at_pos,
        "is_endpoint": bool(cn.is_endpoint),
    }


def serialize_conn_topology(topo, fp):
    """Per-segment connectivity + slide range + pull (the render/edit payload).

    One entry per `topo.segments[i]`, index-aligned.  Conn ORDER is preserved
    verbatim (NUTS tie-breaks depend on it — same invariant topo_snapshot
    freezes).  Slide sentinels and the pull_break sentinel become JSON null.
    """
    import buda
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    out = []
    for cs in ct.segs():
        out.append({
            "horiz": bool(cs.horiz),
            "layer_id": cs.layer_id,
            "along_lo": cs.along_lo,
            "along_hi": cs.along_hi,
            "perp_pos": cs.perp_pos,
            "perp_lo": _slide(cs.perp_lo),
            "perp_hi": _slide(cs.perp_hi),
            "net_pull": cs.net_pull,
            "pull_break": _pull_break(cs.pull_break),
            "along_flex_lo": bool(cs.along_flex_lo),
            "along_flex_hi": bool(cs.along_flex_hi),
            "along_cover_lo": cs.along_cover_lo,
            "along_cover_hi": cs.along_cover_hi,
            "along_pull": cs.along_pull,
            "conns": [_conn(cn) for cn in cs.conns],   # order preserved
        })
    return out


def serialize_topology(topo, fp):
    """One candidate: Topology geometry + busterm taps + junctions + the derived
    ConnTopology analysis (slide/pull), everything the renderer + edit layer need."""
    seg_busterms = []
    for si in sorted(topo.seg_busterms):
        a, b = topo.seg_busterms[si]
        if a is None and b is None:
            continue          # canonical: an all-junction entry == no entry
        seg_busterms.append({"seg_idx": si,
                             "start": _busterm(a), "end": _busterm(b)})
    seg_conns = []
    for (si, ep) in sorted(topo.seg_conns):
        seg_conns.append({"seg_idx": si, "endpoint": ep,
                          "partners": list(topo.seg_conns[(si, ep)])})
    bridges = {name: _segment(seg)
               for name, seg in topo.bridge_segments.items()}
    return {
        "type": topo.type,
        "estimated_wirelength": topo.estimated_wirelength,
        "trunk_location": topo.trunk_location,
        "pass_through_count": topo.pass_through_count,
        "connected_block_names": list(topo.connected_block_names),
        "feedthru_blocks": list(topo.feedthru_blocks),
        "wl_lo": getattr(topo, "wl_lo", None),
        "wl_hi": getattr(topo, "wl_hi", None),
        "segments": [_segment(sg) for sg in topo.segments],
        "seg_busterms": seg_busterms,
        "seg_conns": seg_conns,
        "bridge_segments": bridges,
        "analysis": serialize_conn_topology(topo, fp),
    }


def bundle_floorplan(session, w):
    """The floorplan a bundle's candidates were generated in — the session
    floorplan for the flat flow; a hier (pre-expansion) HBundle's resolved
    cell-local / depth / endpoint floorplan otherwise (mirrors
    tools/topo_snapshot.snapshot_bundles + check_design)."""
    import buda
    fp = session.fp
    b = w.input.original_bundle
    bdb = getattr(session, "bdb", None)
    exp_map = getattr(session, "_hier_expansion_map", None) or {}
    expanded_ids = {id(x) for ws in exp_map.values() for x in ws}
    if (bdb is not None and isinstance(b, buda.HBundle)
            and id(w) not in expanded_ids):
        try:
            comps = {c.name: c for c in bdb.all_components()}
            resolved = session._floorplan_for_hbundle(b, {}, comps)
            if resolved is not None:
                fp = resolved
        except Exception:
            pass
    return fp


def serialize_bundle(session, w, include_candidates=True):
    """A bundle: identity + plan + its OWN floorplan/hanan + (optionally) its
    full candidate list.

    The per-bundle `floorplan`/`hanan` are computed in the SAME frame the
    candidates were generated in (`bundle_floorplan`): the shared session
    floorplan for the flat flow, a hier (pre-expansion) HBundle's resolved
    cell-local / depth / endpoint frame otherwise.  An unfocused multi-bundle
    hier render therefore carries the right frame PER bundle — the client draws
    each bundle's candidates over its own blocks/grid instead of the last
    bundle's shared top-level floorplan.  For the flat flow this frame IS
    `session.fp`, so the per-bundle fields duplicate the top-level ones."""
    ob = w.input.original_bundle
    fp = bundle_floorplan(session, w)
    plan = w.plan
    d = {
        "id": ob.id,
        "net_names": _net_names(ob),
        "width": w.input.width,
        "pinned": bool(getattr(w.input, "topology_pinned", False)),
        "selected_index": plan.selected_topology_index,
        "floorplan": serialize_floorplan(fp),
        "hanan": serialize_hanan(fp),
        "plan": {
            "seg_layers": list(plan.seg_layers),
            "seg_slide_lo": [_slide(v) for v in plan.seg_slide_lo],
            "seg_slide_hi": [_slide(v) for v in plan.seg_slide_hi],
            "seg_perp": list(plan.seg_perp),
            "seg_net_pull": [_pull_break(v) for v in plan.seg_net_pull],
        },
    }
    if include_candidates:
        d["candidates"] = [serialize_topology(t, fp) for t in w.input.candidates]
    return d


def serialize_generation(session, bundle_id=None, candidate=None):
    """Compose the generation-stage render payload: floorplan + hanan + bundles.

    `bundle_id` restricts to one bundle; `candidate` (with a bundle) restricts to
    one candidate (keeps the payload small for a focused view).  The Hanan grid
    is emitted per the (possibly hier-resolved) floorplan of the shown bundle, or
    the session floorplan when none is selected.
    """
    bundles = list(getattr(session, "bundles", []) or [])
    if bundle_id is not None:
        bundles = [w for w in bundles
                   if w.input.original_bundle.id == bundle_id]

    hanan_fp = session.fp
    out_bundles = []
    for w in bundles:
        fp = bundle_floorplan(session, w)
        hanan_fp = fp
        sb = serialize_bundle(session, w, include_candidates=True)
        if candidate is not None and 0 <= candidate < len(sb["candidates"]):
            sb["candidates"] = [sb["candidates"][candidate]]
            sb["candidate_offset"] = candidate
        out_bundles.append(sb)

    # The TOP-LEVEL floorplan/hanan are kept for backward-compat and the flat
    # case (the reference client's fallback + the golden snapshot): they MUST be
    # in the same coordinate frame as a single shown bundle's candidates.  For a
    # pre-expansion hier bundle, bundle_floorplan resolves a cell-local /
    # endpoint frame (`hanan_fp`); returning the top-level session.fp here would
    # draw those local candidates over the wrong blocks, so we emit the last
    # resolved frame — correct for a focused `?bundle=` request.  A multi-bundle
    # hier render can no longer be represented by ONE top-level floorplan, so
    # each serialized bundle now ALSO carries its OWN `floorplan`/`hanan`
    # (`serialize_bundle`); the client prefers those and falls back to these.
    # For the flat flow hanan_fp IS session.fp, so this is unchanged there.
    return {
        "floorplan": serialize_floorplan(hanan_fp),
        "hanan": serialize_hanan(hanan_fp),
        "bundles": out_bundles,
        "state": serialize_state(session),
    }


# ── placed results: abstract NUTS + detailed NUTS ────────────────────────────
def serialize_track_segment(ts):
    """One placed bus segment (stage-4 abstract NUTS)."""
    return {
        "bundle_id": ts.bundle_id,
        "seg_idx": ts.seg_idx,
        "layer": ts.layer,
        "horiz": bool(ts.horiz),
        "span_lo": ts.span_lo,
        "span_hi": ts.span_hi,
        "interval_lo": ts.interval_lo,
        "interval_hi": ts.interval_hi,
        "track_position": ts.track_position,
        "width": ts.width,
        "placed": bool(ts.placed),
        "is_jog": bool(ts.is_jog),
    }


def _overlap(od):
    return {
        "layer": od.layer,
        "bid_a": od.bid_a, "seg_a": od.seg_a,
        "bid_b": od.bid_b, "seg_b": od.seg_b,
        "span_lo": od.span_lo, "span_hi": od.span_hi,
        "perp_lo": od.perp_lo, "perp_hi": od.perp_hi,
    }


def serialize_nuts(nuts_result):
    """Abstract-NUTS placement: placed bus segments + overlap sites + counts."""
    if nuts_result is None:
        return None
    return {
        "segments": [serialize_track_segment(ts) for ts in nuts_result.segments],
        "overlap_details": [_overlap(od) for od in nuts_result.overlap_details],
        "num_overlaps": nuts_result.num_overlaps,
        "num_violations": nuts_result.num_violations,
    }


def serialize_net_segment(ns, orient=None):
    """One placed bit-wire (stage-9 detailed NUTS).

    `orient` is an optional `{(bundle_id, seg_idx): horiz}` map (built from the
    bus TrackSegments) — a bit-wire carries no direction flag of its own but
    inherits its bus segment's, so the renderer knows whether `track_position`
    is a y (H) or x (V)."""
    horiz = None
    if orient is not None:
        horiz = orient.get((ns.bundle_id, ns.seg_idx))
    return {
        "bundle_id": ns.bundle_id,
        "seg_idx": ns.seg_idx,
        "bit_index": ns.bit_index,
        "layer": ns.layer,
        "span_lo": ns.span_lo,
        "span_hi": ns.span_hi,
        "track_position": ns.track_position,
        "width": ns.width,
        "horiz": horiz,
    }


def _net_via(v):
    return {
        "bundle_id": v.bundle_id,
        "from_seg": v.from_seg,
        "to_seg": v.to_seg,
        "bit_index": v.bit_index,
        "from_layer": v.from_layer,
        "to_layer": v.to_layer,
        "x": v.x,
        "y": v.y,
    }


def serialize_detailed(detailed_result, orient=None):
    """Detailed-NUTS placement: per-bit wires + per-bit vias + counts.

    `orient` (optional) maps each `(bundle_id, seg_idx)` to its bus segment's
    `horiz` so bit-wires render with the right orientation."""
    if detailed_result is None:
        return None
    return {
        "net_segments": [serialize_net_segment(ns, orient)
                         for ns in detailed_result.net_segments],
        "net_vias": [_net_via(v) for v in detailed_result.net_vias],
        "num_unplaced": detailed_result.num_unplaced,
    }


def _orient_map(nuts_result):
    """{(bundle_id, seg_idx): horiz} from the placed bus segments."""
    if nuts_result is None:
        return {}
    return {(ts.bundle_id, ts.seg_idx): bool(ts.horiz)
            for ts in nuts_result.segments}


def serialize_render_nuts(session):
    """Compose the NUTS-stage render payload: floorplan + placed bus segments."""
    return {
        "floorplan": serialize_floorplan(session.fp),
        "hanan": serialize_hanan(session.fp),
        "nuts": serialize_nuts(getattr(session, "nuts_result", None)),
        "state": serialize_state(session),
    }


# ── interactive topology edit (Phase 4) ──────────────────────────────────────
def _violation(v):
    kind = getattr(v, "kind", None)
    return {
        "kind": getattr(kind, "name", str(kind)),
        "seg_idx": getattr(v, "seg_idx", -1),
        "seg_idx2": getattr(v, "seg_idx2", -1),
        "block_name": getattr(v, "block_name", ""),
        "message": getattr(v, "message", ""),
    }


def serialize_verdict(v):
    """An EditVerdict (from buda.edit_verdict / an edit_* op) as JSON."""
    if v is None:
        return None
    conn = getattr(v, "conn", None)
    violations = list(conn.violations) if conn is not None else []
    return {
        "applied": bool(v.applied),
        "note": v.note,
        "seg_idx": v.seg_idx,
        "pinched": bool(v.pinched),
        "components": v.components,
        "ok": bool(v.ok()),
        "violations": [_violation(x) for x in violations],
    }


def serialize_edit(session):
    """The current interactive-edit session, if one is open.

    `{open: False}` when no `edit_topology` is active; otherwise the working-copy
    topology (rendered in the bundle's own frame) + the live EditVerdict
    (re-judged via `buda.edit_verdict`, the same call the CLI uses)."""
    import buda
    topo = getattr(session, "_edit_topo", None)
    if topo is None:
        return {"open": False}
    fp = getattr(session, "_edit_fp", None) or session.fp
    w = getattr(session, "_edit_w", None)
    bundle_id = w.input.original_bundle.id if w is not None else None
    try:
        verdict = serialize_verdict(buda.edit_verdict(topo, fp))
    except Exception:
        verdict = None
    return {
        "open": True,
        "bundle_id": bundle_id,
        "src": getattr(session, "_edit_src", ""),
        "verdict": verdict,
        "topology": serialize_topology(topo, fp),
        "slide_overrides": {str(k): list(v)
                            for k, v in (getattr(session, "_edit_slide", {})
                                         or {}).items()},
    }


def serialize_render_detailed(session):
    """Compose the detailed-NUTS render payload: floorplan + bit-wires + vias."""
    orient = _orient_map(getattr(session, "nuts_result", None))
    return {
        "floorplan": serialize_floorplan(session.fp),
        "hanan": serialize_hanan(session.fp),
        "detailed": serialize_detailed(
            getattr(session, "detailed_result", None), orient),
        "state": serialize_state(session),
    }
