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

"""NUTS / DetailedNUTS placement snapshot — the Phase 0 byte-identity gate of
docs/internal/nuts_dnuts_refactor.md.

Runs each corpus flow to COMPLETION (full pipeline, viz/report commands
stripped) and captures the actual placement output of stages 4 and 9:

  * abstract — every TrackSegment of nuts_result.segments (sorted), all
    placement-relevant fields (track_position, spans, intervals, bounds,
    placed/is_jog flags) plus the overlap/violation counts;
  * detailed — every NetSegment and NetVia of detailed_result (sorted) plus
    the unplaced count.

wl_corpus.py compares wirelength/overlap TOTALS — strong but not airtight
(two placements can swap tracks and preserve every total).  This snapshot is
the true oracle: any refactor of nuts.cpp / detailed_nuts.cpp that moves a
single wire fails the golden with a line-level diff.

Large flows gate on per-layer sha256 digests instead of full text; a digest
mismatch names the (stage, layer), and the reviewable diff comes from
regenerating the full snapshot on the baseline tree (the wl_corpus workflow).

Deliberate re-baseline: rerun `PYTHONPATH=build:tools python3
tools/nuts_snapshot.py` and review the golden diff in the PR.

Host portability: the checked-in goldens were generated on an x86-64 host
(the Claude Code CI container, -O3 -march=native).  The six small flows are
placement-stable across hosts; the three HOST_SENSITIVE_FLOWS below
(b4_bus_077, big, rnr/mix) are the designs whose NUTS outcomes the
big2/tc3a test notes (PR #203) document as FP/ISA-divergent — a different
discrete track choice on another host is a hard mismatch that the 1e-6
FORMATTING quantum cannot absorb (it is not a tolerance).  An off-host
mismatch on those three therefore means "re-verify against the true baseline
tree locally" (regenerate goldens on the baseline commit, diff on the same
machine), NOT "code bug"; the golden test warns-only for them unless
BUDA_NUTS_GOLDEN_STRICT is set (set it on the golden-generation host's CI).

Usage:
  PYTHONPATH=build:tools python3 tools/nuts_snapshot.py            # rewrite goldens
  PYTHONPATH=build:tools python3 tools/nuts_snapshot.py <out_dir>  # write elsewhere
"""
import contextlib
import hashlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# The wl_corpus flow list, split by full-pipeline runtime so the golden test
# can tier them (all are 'mid' — full-pipeline runs are integration tests —
# but the SMALL group is cheap enough to run on every mid pass; the LARGE
# group covers the heavyweight placements).
CORPUS_SMALL = [
    "flow/four_blocks.buda",
    "flow/four_blocks_3_bundles.buda",
    "flow/dogleg1.buda",
    "flow/dogleg2.buda",
    "flow/channel_stress.buda",
    "demo/comprehensive_demo.buda",
    "flow/big_data_test/big2/b4_bus_077.buda",
]
CORPUS_LARGE = [
    "flow/big_data_test/big.buda",
    "flow/rnr/mix.buda",                       # hier + ripup_reroute
]
CORPUS = CORPUS_SMALL + CORPUS_LARGE

# The single-bus b4_bus_077 extraction stands in for full big2 DELIBERATELY:
# it pins the x5772 placement shape without the multi-minute runtime or the
# whole-design FP sensitivity big2's own tests handle with bounds, not goldens.
#
# Flows whose exact placements are FP/ISA-sensitive (see "Host portability"
# above): a golden mismatch on a host other than the generation host is
# environmental until proven otherwise.
HOST_SENSITIVE_FLOWS = {
    "flow/big_data_test/big2/b4_bus_077.buda",
    "flow/big_data_test/big.buda",
    "flow/rnr/mix.buda",
}

# Flows whose full snapshot is too large to commit as text: their golden is a
# per-(stage, layer) sha256 digest instead — byte-identity is gated just as
# hard, and mismatches localize to a stage + layer.
DIGEST_FLOWS = {
    "flow/big_data_test/big.buda",
    "flow/rnr/mix.buda",
}

# Commands never executed in the headless harness (interactive / redundant).
_SKIP = {"visualize", "visualize_topologies", "exit",
         "report_wirelength", "report_wl"}


def slug(path):
    return (path.replace("flow/", "").replace("demo/", "demo_")
                .replace("/", "_").removesuffix(".buda"))


def run_flow(path):
    """Execute a flow to completion, in-process, viz stripped.

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
                if c.split()[0] in _SKIP:
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)
    return s


def _f(v):
    """Canonical float formatting: 1e-6 quantum (the FP-determinism tolerance
    used by the solver itself), so formatting noise can never fake a diff."""
    return f"{v:.6f}"


def abstract_lines(nuts_result):
    """One line per TrackSegment, sorted by (bundle, seg): every field that
    detailed NUTS or the persist layer consumes."""
    rows = []
    for t in nuts_result.segments:
        rows.append((t.bundle_id, t.seg_idx,
                     f"a {t.bundle_id} {t.seg_idx} L{t.layer} "
                     f"{'H' if t.horiz else 'V'} "
                     f"{'P' if t.placed else 'U'}{'J' if t.is_jog else ''} "
                     f"pos={_f(t.track_position) if t.placed else 'NaN'} "
                     f"w={_f(t.width)} "
                     f"span=[{_f(t.span_lo)},{_f(t.span_hi)}] "
                     f"itvl=[{_f(t.interval_lo)},{_f(t.interval_hi)}] "
                     f"bound=[{_f(t.track_lo_bound)},{_f(t.track_hi_bound)}]"))
    return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


def detailed_lines(detailed_result):
    """One line per NetSegment / NetVia, sorted."""
    rows = []
    for n in detailed_result.net_segments:
        rows.append(((0, n.bundle_id, n.seg_idx, n.bit_index),
                     f"d {n.bundle_id} {n.seg_idx} b{n.bit_index} L{n.layer} "
                     f"pos={_f(n.track_position)} w={_f(n.width)} "
                     f"span=[{_f(n.span_lo)},{_f(n.span_hi)}]"))
    for v in detailed_result.net_vias:
        rows.append(((1, v.bundle_id, v.from_seg, v.to_seg, v.bit_index),
                     f"v {v.bundle_id} {v.from_seg}->{v.to_seg} b{v.bit_index} "
                     f"L{v.from_layer}->L{v.to_layer} "
                     f"at=({_f(v.x)},{_f(v.y)})"))
    return [r[1] for r in sorted(rows, key=lambda r: r[0])]


def snapshot_session(s):
    """Full placement snapshot of a completed session, as diffable text."""
    out = []
    if s.nuts_result is None:
        out.append("abstract: none")
    else:
        out.append(f"abstract: {len(s.nuts_result.segments)} segments "
                   f"overlaps={s.nuts_result.num_overlaps} "
                   f"violations={s.nuts_result.num_violations}")
        out.extend(abstract_lines(s.nuts_result))
    if s.detailed_result is None:
        out.append("detailed: none")
    else:
        out.append(f"detailed: {len(s.detailed_result.net_segments)} netsegs "
                   f"{len(s.detailed_result.net_vias)} vias "
                   f"unplaced={s.detailed_result.num_unplaced}")
        out.extend(detailed_lines(s.detailed_result))
    return "\n".join(out) + "\n"


def snapshot_digest(s):
    """Per-(stage, layer) sha256 digests — the compact golden for big flows."""
    by_key = {}
    if s.nuts_result is not None:
        for ln in abstract_lines(s.nuts_result):
            by_key.setdefault(("abstract", ln.split()[3]), []).append(ln)
    if s.detailed_result is not None:
        for ln in detailed_lines(s.detailed_result):
            key = ln.split()[4] if ln.startswith("d ") else "vias"
            by_key.setdefault(("detailed", key), []).append(ln)
    out = []
    if s.nuts_result is not None:
        out.append(f"abstract: {len(s.nuts_result.segments)} segments "
                   f"overlaps={s.nuts_result.num_overlaps} "
                   f"violations={s.nuts_result.num_violations}")
    if s.detailed_result is not None:
        out.append(f"detailed: {len(s.detailed_result.net_segments)} netsegs "
                   f"{len(s.detailed_result.net_vias)} vias "
                   f"unplaced={s.detailed_result.num_unplaced}")
    for (stage, key), lines in sorted(by_key.items()):
        text = "\n".join(lines)
        out.append(f"{stage} {key} {len(lines)} "
                   f"{hashlib.sha256(text.encode()).hexdigest()}")
    return "\n".join(out) + "\n"


def golden_dir():
    return os.path.join(ROOT, "test", "tests", "data", "nuts_golden")


def golden_path(flow, base=None):
    return os.path.join(base or golden_dir(), slug(flow) + ".txt")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else golden_dir()
    os.makedirs(out_dir, exist_ok=True)
    for flow in CORPUS:
        if not os.path.exists(os.path.join(ROOT, flow)):
            print(f"{flow}: MISSING")
            continue
        s = run_flow(flow)
        text = (snapshot_digest(s) if flow in DIGEST_FLOWS
                else snapshot_session(s))
        path = golden_path(flow, out_dir)
        with open(path, "w") as f:
            f.write(text)
        print(f"{flow}: {len(text.splitlines())} lines -> {path}", flush=True)


if __name__ == "__main__":
    main()
