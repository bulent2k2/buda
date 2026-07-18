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

"""Verify / re-baseline the topo_analysis goldens (test/tests/data/topo_golden).

The turnkey wrapper around tools/topo_snapshot.py for the REFERENCE HOST that
owns the goldens (docs/internal/hanan_loci_golden_regen.md is the operating
procedure for the `hanan_loci` default flip).  Two modes:

  --verify   Regenerate every corpus flow's snapshot IN MEMORY and compare it
             against the checked-in golden through the SAME canonical path the
             gate test uses (test_topo_analysis_golden.py::_check_flow):
             digest flows via topo_snapshot.snapshot_digest, text flows via
             canonicalize(live) == canonicalize(golden).  Per-flow verdict:

               OK              byte-identical to the checked-in golden
               OK (order-only) canonical-identical, bytes differ — a pure
                               candidate-ranking permutation; the gate test
                               PASSES (its comparison canonicalizes both
                               sides), so no re-baseline is needed
               CONTENT-DIFFERS canonical mismatch — the gate test FAILS; a
                               per-bundle candidate-pool delta is printed

             Nothing on disk is touched.  Exit 0 iff no flow is
             CONTENT-DIFFERS or MISSING.

  --write    Actually re-baseline: rewrite the checked-in goldens with exactly
             the bytes tools/topo_snapshot.py would write (order-canonical
             text / per-bundle digests).  REFUSES unless `git status
             --porcelain` is empty — commit the generation change (e.g. the
             hanan_loci default flip) FIRST so the golden rewrite is a clean,
             attributable commit of its own.  Prints the exact file list
             written.

Usage (repo root):
  PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify
  PYTHONPATH=build:tools python3 tools/regen_goldens.py --write
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "tools"), os.path.join(ROOT, "src"),
           os.path.join(ROOT, "build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import topo_snapshot as ts  # noqa: E402


def _live_golden_text(flow):
    """The exact bytes tools/topo_snapshot.py would write for this flow."""
    s = ts.run_flow_generation(flow)
    if flow in ts.DIGEST_FLOWS:
        return ts.snapshot_digest(s)
    return ts.canonicalize(ts.snapshot_session(s))


def _bundle_cand_counts(text):
    """{bundle_id: candidates} from 'bundle <id> nets=N candidates=K' lines."""
    out = {}
    for m in re.finditer(r"^bundle (\S+) nets=\d+ candidates=(\d+)$",
                         text, re.M):
        out[m.group(1)] = int(m.group(2))
    return out


def _digest_map(text):
    """{bundle_id: sha256} from a digest golden's 'bundle <id> <hex>' lines."""
    out = {}
    for m in re.finditer(r"^bundle (\S+) ([0-9a-f]{64})$", text, re.M):
        out[m.group(1)] = m.group(2)
    return out


def _content_delta(flow, want, live):
    """Human-readable per-bundle pool delta for a CONTENT-DIFFERS flow."""
    lines = []
    if flow in ts.DIGEST_FLOWS:
        wm, lm = _digest_map(want), _digest_map(live)
        changed = sorted(b for b in wm if b in lm and wm[b] != lm[b])
        added = sorted(set(lm) - set(wm))
        removed = sorted(set(wm) - set(lm))
        lines.append(f"    digest golden: {len(changed)} of {len(wm)} bundle "
                     f"digests differ"
                     + (f", +{len(added)} new bundles" if added else "")
                     + (f", -{len(removed)} bundles gone" if removed else ""))
        if changed:
            lines.append(f"    changed bundle ids: {', '.join(changed[:20])}"
                         + (" ..." if len(changed) > 20 else ""))
        return lines
    wc, lc = _bundle_cand_counts(want), _bundle_cand_counts(live)
    changed = [(b, wc[b], lc[b]) for b in wc if b in lc and wc[b] != lc[b]]
    lines.append(f"    {len(changed)} of {len(wc)} bundles change pool size, "
                 f"total candidates {sum(wc.values())} -> {sum(lc.values())}")
    for b, w, l in changed[:20]:
        lines.append(f"      bundle {b}: {w} -> {l} candidates")
    if len(changed) > 20:
        lines.append(f"      ... {len(changed) - 20} more")
    return lines


def verify():
    n_bad = 0
    for flow in ts.CORPUS:
        golden = ts.golden_path(flow)
        if not os.path.exists(os.path.join(ROOT, flow)):
            print(f"{flow}: MISSING FLOW")
            n_bad += 1
            continue
        if not os.path.exists(golden):
            print(f"{flow}: MISSING GOLDEN ({golden})")
            n_bad += 1
            continue
        live = _live_golden_text(flow)
        want = open(golden).read()
        if live == want:
            print(f"{flow}: OK")
            continue
        # The gate test's comparison: digest goldens are canonical by
        # construction; text goldens are canonicalized on BOTH sides.
        want_c = want if flow in ts.DIGEST_FLOWS else ts.canonicalize(want)
        if live == want_c:
            print(f"{flow}: OK (order-only — gate test passes; goldens "
                  f"predate the canonical sort)")
            continue
        print(f"{flow}: CONTENT-DIFFERS")
        for line in _content_delta(flow, want_c, live):
            print(line)
        n_bad += 1
    print(f"\nverify: {'ALL OK' if n_bad == 0 else f'{n_bad} flow(s) differ'}"
          f" ({len(ts.CORPUS)} flows)")
    return 0 if n_bad == 0 else 1


def write():
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("regen_goldens --write: REFUSING — working tree is not clean.\n"
              "Commit the generation change (e.g. the hanan_loci default "
              "flip) first, then re-run; the golden rewrite must be a clean "
              "commit of its own.\n\ngit status --porcelain:\n" + dirty)
        return 1
    written = []
    for flow in ts.CORPUS:
        if not os.path.exists(os.path.join(ROOT, flow)):
            print(f"{flow}: MISSING FLOW — skipped")
            continue
        text = _live_golden_text(flow)
        path = ts.golden_path(flow)
        with open(path, "w") as f:
            f.write(text)
        written.append(path)
        print(f"{flow}: {len(text.splitlines())} lines -> "
              f"{os.path.relpath(path, ROOT)}")
    print(f"\nwrote {len(written)} golden file(s):")
    for p in written:
        print(f"  {os.path.relpath(p, ROOT)}")
    print("\nNow run the gate: pytest test/tests/test_topo_analysis_golden.py"
          " -m '' -v\nRollback: git checkout -- test/tests/data/topo_golden/")
    return 0


def main(argv):
    if argv == ["--verify"]:
        return verify()
    if argv == ["--write"]:
        return write()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
