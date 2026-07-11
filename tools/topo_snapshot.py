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

"""Canonical topology + analysis snapshot — the Phase 0 byte-identity gate of
docs/internal/topo_conn_unification.md.

For each corpus flow this captures, at GENERATION stage (the flow's commands are
executed up to — not including — the first planner/NUTS command), every bundle's
full candidate list:

  * the Topology itself: type, wirelength, trunk, segments (coords, layer,
    is_jog, edge_id), seg_busterms (normalized like the persist tests),
    seg_conns, bridge_segments, connected/feedthru block names;
  * the DERIVED ANALYSIS: one line per ConnSeg with all its fields
    (horiz, layer, along span, perp pos, slide window, net_pull, along-flex
    flags/cover/pull) and the ordered conns list — conn ORDER is part of the
    frozen contract (conn_topology.cpp preserves the retired geometric scan's
    ordering because NUTS tie-breaks depend on it).

Golden files live in test/tests/data/topo_golden/ and are compared by
test_topo_analysis_golden.py.  Any refactor of topology.cpp/conn_topology.cpp
must reproduce them byte-for-byte — or re-baseline deliberately by re-running
this tool and reviewing the diff.

Generation stage only, on purpose: candidate geometry and its analysis are pure
integer arithmetic (machine-stable), while post-NUTS mutations (doglegs) ride on
float placements that are documented to diverge across CPUs (-march=native
double rounding).  Post-generation mutation equivalence is covered by the
resume test (test_topo_resume_analysis.py) and, later, the Phase D fuzz gate.

Usage:
  PYTHONPATH=build:tools python3 tools/topo_snapshot.py            # rewrite goldens
  PYTHONPATH=build:tools python3 tools/topo_snapshot.py <out_dir>  # write elsewhere
"""
import contextlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# The wl_corpus flow list (tools/wl_corpus.py), split by generation-stage
# runtime so the golden test can tier them (fast tier excludes 'mid'-marked).
CORPUS_FAST = [
    "flow/four_blocks.buda",
    "flow/four_blocks_3_bundles.buda",
    "flow/dogleg1.buda",
    "flow/dogleg2.buda",
    "flow/double_detour.buda",
    "flow/channel_stress.buda",
]
CORPUS_MID = [
    "demo/comprehensive_demo.buda",
    "flow/big_data_test/big.buda",
    "flow/big_data_test/big2/b4_bus_077.buda",
    "flow/rnr/mix.buda",                       # hier
]
CORPUS = CORPUS_FAST + CORPUS_MID

# Flows whose full snapshot is too large to commit as text (multi-MB): their
# golden is a per-bundle sha256 digest instead — byte-identity is gated just as
# hard, mismatches localize to a bundle, and the reviewable-diff workflow is the
# wl_corpus one (regenerate the full snapshot on the baseline tree and diff).
DIGEST_FLOWS = {
    "flow/big_data_test/big.buda",
    "flow/rnr/mix.buda",
}

# Commands that end the generation stage: everything before the first of these
# is setup + bundling + topology generation (+ pins), which is what we snapshot.
_STOP = {"run_planner", "run_nuts", "run_nuts_on_layer", "run_detailed_nuts",
         "ripup_reroute", "negotiate_congestion"}
# Commands never executed in the headless harness.
_SKIP = {"visualize", "visualize_topologies", "exit",
         "report_wirelength", "report_wl"}


def slug(path):
    return path.replace("flow/", "").replace("/", "_").removesuffix(".buda")


def run_flow_generation(path):
    """Execute a flow's commands up to the generation stage, in-process.

    Returns the live BudaSession.  cwd is saved/restored (flows source
    relative fixtures, so commands run from the flow's directory).
    """
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    os.chdir(os.path.join(ROOT, os.path.dirname(path)))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for line in open(os.path.basename(path)):
                c = line.strip()
                if not c or c.startswith("#"):
                    continue
                cmd = c.split()[0]
                if cmd in _STOP:
                    break
                if cmd in _SKIP:
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)
    return s


def _fmt_rect(r):
    return f"({r.x1},{r.y1},{r.x2},{r.y2})"


def _fmt_bt(bt):
    """A Busterm as a canonical token (mirrors the persist tests' _norm_bt)."""
    if bt is None:
        return "-"
    rects = "".join(_fmt_rect(r) for r in bt.rects)
    teg = str(bt.teg_mode).split(".")[-1]
    return (f"{bt.block_name}:{teg}:{_fmt_rect(bt.bbox)}"
            f":{_fmt_rect(bt.orig_bbox)}:[{rects}]")


def _fmt_seg(sg, include_edge_id=True):
    edge = f" edge={sg.edge_id}" if include_edge_id else ""
    return (f"({sg.start.x},{sg.start.y})-({sg.end.x},{sg.end.y})"
            f" L{sg.layer_hint} jog={int(sg.is_jog)}{edge}")


def _fmt_conn(cn):
    import buda
    if cn.kind == buda.SegConnKind.BUSTERM:
        return f"BT:{cn.block_name}@{cn.face_coord}"
    return f"SEG:{cn.seg_idx}@{cn.at_pos}" + ("e" if cn.is_endpoint else "")


def snapshot_topology(topo, fp, include_edge_id=True):
    """Serialize one candidate: the Topology and its full derived analysis.

    Every ConnSeg field appears; slide sentinels are emitted as raw ints — the
    sentinel magnitude is part of the frozen contract (leaked into Python/NUTS/
    planner constants).  All-junction (None,None) seg_busterms entries are
    omitted, matching the canonical persisted form (a BDB reload legitimately
    lacks them).
    """
    import buda
    lines = []
    lines.append(f" cand type={topo.type} wl={topo.estimated_wirelength}"
                 f" trunk={topo.trunk_location}"
                 f" passthru={topo.pass_through_count}")
    lines.append(f"  blocks={','.join(topo.connected_block_names)}"
                 f" feedthru={','.join(topo.feedthru_blocks)}")
    for name in sorted(topo.bridge_segments):
        lines.append(f"  bridge {name} "
                     f"{_fmt_seg(topo.bridge_segments[name], include_edge_id)}")
    for i, sg in enumerate(topo.segments):
        lines.append(f"  seg {i} {_fmt_seg(sg, include_edge_id)}")
    for si in sorted(topo.seg_busterms):
        a, b = topo.seg_busterms[si]
        if a is None and b is None:
            continue
        lines.append(f"  bt {si} {_fmt_bt(a)} {_fmt_bt(b)}")
    for (si, ep) in sorted(topo.seg_conns):
        partners = ",".join(str(p) for p in topo.seg_conns[(si, ep)])
        lines.append(f"  sconn ({si},{ep})->[{partners}]")
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    for i, cs in enumerate(ct.segs()):
        lines.append(
            f"  ana {i} horiz={int(cs.horiz)} layer={cs.layer_id}"
            f" along=[{cs.along_lo},{cs.along_hi}] perp={cs.perp_pos}"
            f" slide=[{cs.perp_lo},{cs.perp_hi}] pull={cs.net_pull}"
            f" aflex={int(cs.along_flex_lo)}{int(cs.along_flex_hi)}"
            f" acover=[{cs.along_cover_lo},{cs.along_cover_hi}]"
            f" apull={cs.along_pull}")
        conns = " ; ".join(_fmt_conn(cn) for cn in cs.conns)  # ORDER matters
        lines.append(f"  ana {i} conns {conns}")
    return lines


def snapshot_bundles(s, include_edge_id=True):
    """Serialize every bundle's candidate list + analysis from a session.

    Returns [(bundle_id, block_text)] — one text block per bundle, so the full
    snapshot and the per-bundle digest golden derive from identical bytes.

    Hier (pre-expansion) HBundles are analyzed against the same per-bundle
    resolved floorplan check_design uses, so the snapshot sees the
    coordinate/name space the candidates were generated in.
    """
    import buda
    hier_fp_cache = {}
    comps_by_name = ({c.name: c for c in s.bdb.all_components()}
                     if getattr(s, "bdb", None) is not None else {})
    expanded_ids = {id(w)
                    for ws in (getattr(s, "_hier_expansion_map", None) or {}).values()
                    for w in ws}
    blocks = []
    for w in s.bundles:
        b = w.input.original_bundle
        lines = [f"bundle {b.id} nets={len(b.get_net_names())}"
                 f" candidates={len(w.input.candidates)}"]
        fp = s.fp
        if (getattr(s, "bdb", None) is not None and isinstance(b, buda.HBundle)
                and id(w) not in expanded_ids):
            resolved = s._floorplan_for_hbundle(b, hier_fp_cache, comps_by_name)
            if resolved is not None:
                fp = resolved
        for t in w.input.candidates:
            lines.extend(snapshot_topology(t, fp, include_edge_id))
        blocks.append((b.id, "\n".join(lines) + "\n"))
    return blocks


def snapshot_session(s, include_edge_id=True):
    """The full snapshot text (all bundle blocks concatenated)."""
    return "".join(text for _, text in snapshot_bundles(s, include_edge_id))


def snapshot_digest(s):
    """Per-bundle sha256 digest text, for flows too large to commit as text."""
    import hashlib
    lines = ["# topo_snapshot per-bundle sha256 (see tools/topo_snapshot.py)"]
    for bid, text in snapshot_bundles(s):
        lines.append(f"bundle {bid} "
                     f"{hashlib.sha256(text.encode()).hexdigest()}")
    return "\n".join(lines) + "\n"


def golden_dir():
    return os.path.join(ROOT, "test", "tests", "data", "topo_golden")


def golden_path(flow, base=None):
    return os.path.join(base or golden_dir(), slug(flow) + ".txt")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else golden_dir()
    os.makedirs(out_dir, exist_ok=True)
    for flow in CORPUS:
        if not os.path.exists(os.path.join(ROOT, flow)):
            print(f"{flow}: MISSING")
            continue
        s = run_flow_generation(flow)
        text = (snapshot_digest(s) if flow in DIGEST_FLOWS
                else snapshot_session(s))
        path = golden_path(flow, out_dir)
        with open(path, "w") as f:
            f.write(text)
        print(f"{flow}: {len(text.splitlines())} lines -> {path}")


if __name__ == "__main__":
    main()
