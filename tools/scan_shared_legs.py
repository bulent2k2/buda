#!/usr/bin/env python3
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

"""Which HALF of the shared-leg shape does a design actually carry?

`set_trim_mst_legs` cuts the duplicated overlap off two MST legs meeting one
node along the same axis.  The pair can meet at their STARTS (antenna_repros
entry 5a) or at their ENDS (5b) — and which one a design gets is not a
property of the defect: `compute_mst` emits every edge with `u < v` and
`realize_mst_edge` routes `u -> v`, so it falls out of that node's index, whose
lever is which block is the bundle's DRIVER.

This counts each half per flow, plus the case that only exists once BOTH are
trimmed: a leg with a shorter sibling at each end, which two independently
legal cuts can pinch to zero length between them.  That is why the trim's
min-stub floor is `max(floor, 1)` — `set_min_stub_length` is 0 unless a design
declares one.

It answers three questions the corpus gate cannot, because the trim is opt-in
and almost nothing opts in:

  * is 5b a real shape or only a constructed one?      (END > 0 anywhere?)
  * would turning the knob on change this design?      (either count > 0)
  * is the zero-length guard load-bearing or defensive? (BOTH > 0 anywhere?)

Counts raw GENERATED geometry, so a flow that turns the trim on reports what
survived it — run it against a flow with the knob off to see what is there to
cut.  Read-only: nothing is mutated and no flow is re-planned.

Usage (from the repo root):
    PYTHONPATH=build:src:tools python3 tools/scan_shared_legs.py [flow ...]
"""
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FLOWS = [
    # the two vehicles, one per half
    "flow/mst_shared_leg_prefix.buda",
    "flow/mst_shared_leg_suffix.buda",
    # a NEGATIVE: too few blocks for a standalone MST candidate to matter
    "flow/four_blocks.buda",
    # real designs
    "flow/rnr/mix.buda",
    "flow/rnr/mix2_topdown_refine.buda",   # the one corpus flow that opts in
    "flow/big_data_test/big.buda",
    "flow/big_data_test/big2/big2.buda",
]


def solve(flow):
    """Run a flow, returning its session — or raising if the flow FAILED.

    A bare `except SystemExit: pass` would be wrong here in a way that matters
    for exactly this tool.  The command handlers signal failure with
    `SystemExit(1)` (a missing input, an unknown command), and swallowing that
    returns a half-populated session whose candidate counts are low or zero —
    which this scanner would then report as `END=0  BOTH=0`, i.e. as EVIDENCE
    FOR the conclusion, when the truth is that nothing was measured.  A clean
    `exit 0` in a flow is a different thing and is fine to continue from.
    """
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            s.do_command(f"source {flow}")
        except SystemExit as e:
            if e.code not in (0, None):
                raise RuntimeError(
                    f"{flow} exited {e.code} — counts from it would be "
                    f"meaningless, so they are not reported") from e
    return s


def ray(anchor, far):
    """The leg seen as a ray FROM `anchor`: (is_horizontal, sign, length).

    None for a degenerate or diagonal segment.  Anchoring on a POINT rather
    than on `seg.start` is what lets one predicate see both halves.
    """
    h = anchor.y == far.y
    v = anchor.x == far.x
    if h == v:
        return None
    d = (far.x - anchor.x) if h else (far.y - anchor.y)
    return None if d == 0 else (h, d > 0, abs(d))


def scan(topo):
    """(start_shared, end_shared, both) leg counts for one candidate."""
    segs = topo.segments
    s_hits = e_hits = both = 0
    for i, b in enumerate(segs):
        rb_s, rb_e = ray(b.start, b.end), ray(b.end, b.start)
        hit_s = hit_e = False
        for j, a in enumerate(segs):
            if i == j:
                continue
            # A sibling lies INSIDE this leg when it leaves the shared point
            # the same way and is strictly shorter.
            ra = ray(a.start, a.end)
            if (ra and rb_s and (a.start.x, a.start.y) == (b.start.x, b.start.y)
                    and ra[:2] == rb_s[:2] and ra[2] < rb_s[2]):
                hit_s = True
            rae = ray(a.end, a.start)
            if (rae and rb_e and (a.end.x, a.end.y) == (b.end.x, b.end.y)
                    and rae[:2] == rb_e[:2] and rae[2] < rb_e[2]):
                hit_e = True
        s_hits += hit_s
        e_hits += hit_e
        both += hit_s and hit_e
    return s_hits, e_hits, both


def main():
    flows = sys.argv[1:] or DEFAULT_FLOWS
    print(f"{'flow':<46}{'cands':>7}{'START':>7}{'END':>6}{'BOTH':>6}")
    print("-" * 72)
    tot = [0, 0, 0, 0]
    failed = []
    for flow in flows:
        path = flow if Path(flow).is_absolute() else str(ROOT / flow)
        name = flow[5:] if flow.startswith("flow/") else flow
        try:
            s = solve(path)
        except RuntimeError as e:
            # LOUD and skipped, never counted as a zero.  A flow that did not
            # run is not a flow with nothing to find.
            print(f"{name:<46}{'FAILED — ' + str(e).split('—')[0].strip():>26}")
            failed.append(name)
            continue
        cands = st = en = bo = 0
        for w in s.bundles:
            for t in w.input.candidates:
                if "MST" not in t.type:
                    continue
                cands += 1
                a, b, c = scan(t)
                st += a
                en += b
                bo += c
        print(f"{name:<46}{cands:>7}{st:>7}{en:>6}{bo:>6}")
        for k, v in enumerate((cands, st, en, bo)):
            tot[k] += v
    print("-" * 72)
    print(f"{'total':<46}{tot[0]:>7}{tot[1]:>7}{tot[2]:>6}{tot[3]:>6}")
    if failed:
        # Said BEFORE any verdict, and the exit code carries it too: a partial
        # sweep must not read as a clean one.
        print(f"\n{len(failed)} flow(s) did NOT run: {', '.join(failed)}.\n"
              "The totals above cover the rest only — no conclusion about what\n"
              "is absent can rest on them.")
        return 1
    if tot[3] == 0:
        print("\nBOTH is 0: the zero-length guard in trim_shared_leg_overlaps is\n"
              "DEFENSIVE on this set — the mirror makes that case reachable, but\n"
              "no flow measured here produces it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
