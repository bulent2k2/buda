# `hanan_loci` default flip — reference-host golden regen procedure

The `hanan_loci` generation knob (wishlist-topo "Nominal-WL comparability",
piece (a)) samples n-pin trunk loci ON the in-bbox Hanan lines in addition to
the channel midpoints. It shipped **opt-in**
(`TopologyGenerator::allow_hanan_loci_ = false` in `src/topology.h`); when it
becomes the generation **default**, the `topo_analysis` goldens
(`test/tests/data/topo_golden/`, gated by
`test/tests/test_topo_analysis_golden.py`) change **CONTENT** — new candidate
blocks appear in the pools. Order-only shifts are already absorbed by the
order-canonical comparison (`tools/topo_snapshot.py::canonicalize`, PR #327);
the content regen stays **reference-host-owned** — goldens must only ever be
rewritten on the host that owns them, never on a drive-by container.

**STATUS (2026-08-13): DONE — the flip and its re-baseline are both on
`main`, and nothing here is outstanding.**  `src/topology.h` carries the
default (`allow_hanan_loci_ = true`) and the goldens match it.  Verified
under CI's pinned ISA (`BUDA_ARCH=x86-64-v2` — `ci.md` names CI the golden
reference host, so that is the environment the goldens are owned by, not any
one developer box):

| check | result |
|---|---|
| `regen_goldens.py --verify` | **ALL OK (10 flows)** |
| `test_nuts_placement_golden.py` with `BUDA_NUTS_GOLDEN_STRICT=1` | **9/9 pass**, incl. all four `HOST_SENSITIVE_FLOWS` |
| re-running BOTH regens (`--write` + `nuts_snapshot.py`) | **zero diff** — every golden rewritten byte-identically |

That last row is the real proof: a re-baseline today is a no-op, so there is
nothing left to regenerate.  The procedure below is kept as the **turnkey kit
for the next content-shifting change**, not as pending work.  (The stale
"one remaining step" wording outlived its cause and had already leaked into a
`pytest.xfail` on `test_nuts_busterm_face_anchor.py`, which was reporting
XPASS; that marker is now removed.)

The kit is built around `tools/regen_goldens.py`:

```
PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify   # read-only check
PYTHONPATH=build:tools python3 tools/regen_goldens.py --write    # re-baseline (clean tree only)
```

`--verify` regenerates every corpus flow's snapshot in memory and compares it
against the checked-in golden through the SAME canonical path the gate test
uses (digest flows via `snapshot_digest`, text flows via
`canonicalize(live) == canonicalize(golden)`), printing a per-flow verdict:

| verdict | meaning |
|---|---|
| `OK` | byte-identical to the checked-in golden |
| `OK (order-only)` | canonical-identical, bytes differ — a pure candidate-ranking permutation; the gate test passes, no action needed |
| `CONTENT-DIFFERS` | canonical mismatch — the gate test fails; a per-bundle pool delta is printed |

Exit code 0 iff nothing is `CONTENT-DIFFERS`/missing. `--write` rewrites the
goldens with exactly the bytes `tools/topo_snapshot.py` would write and
**refuses unless `git status --porcelain` is empty** — commit the flip first
so the golden rewrite is a clean, attributable commit of its own.

## What to expect: the measured classification

Measured 2026-07-18 on `main` (post PR #327/#329) by locally flipping
`allow_hanan_loci_ = false → true` in `src/topology.h`, rebuilding, and
regenerating all snapshots to a temp location (checked-in goldens untouched).
The flip is **purely additive**: every pre-flip candidate block survives
byte-identical (0 removed, 0 mutated), only NEW candidate blocks appear — so
there are no order-only casualties, exactly as the canonical comparison
predicts.

| golden (flow) | kind | verdict under default-on | pool delta |
|---|---|---|---|
| `four_blocks.txt` | text | CONTENT-DIFFERS | 3/4 bundles, 60 → 74 cands (+14) |
| `four_blocks_3_bundles.txt` | text | unchanged | 36 → 36 |
| `dogleg1.txt` | text | unchanged | 24 → 24 |
| `dogleg2.txt` | text | CONTENT-DIFFERS | 1/3 bundles, 36 → 47 (+11) |
| `double_detour.txt` | text | unchanged | 12 → 12 |
| `channel_stress.txt` | text | unchanged | 461 → 461 (62 bundles, all 2-pin) |
| `demo_comprehensive_demo.txt` | text | CONTENT-DIFFERS | 4/5 bundles, 154 → 245 (+91) |
| `big_data_test_big.txt` | digest | CONTENT-DIFFERS | 70/80 bundle digests; 2571 → 4059 (+1488) |
| `big_data_test_big2_b4_bus_077.txt` | text | CONTENT-DIFFERS | 1/1 bundle, 17 → 22 (+5) |
| `rnr_mix.txt` | digest | CONTENT-DIFFERS | 26/100 bundle digests; 1237 → 1688 (+451) |

So: exactly **6 of 10 goldens content-differ**; the 4 unchanged flows contain
only shapes the knob does not touch (2-pin L/Z/U — the knob only extends
`generate_npin`'s trunk locus set). Growth matches the wishlist-topo measured
pool growth (~1.3–1.6×: comprehensive_demo +59%, mix +36%, b4_bus_077 +29%);
`big.buda` grows +58%, the top of the predicted band (many n-pin bundles). No
flow's diff exceeds pool growth — no surprises.

Two adjacent expectations for the flip PR (not this kit's business, but the
reference host will see them):

* `test_topo_hanan_loci.py::test_hanan_loci_is_opt_in_default_pool_unchanged`
  asserts the knob is opt-in; the flip PR must update/retire that test.
* Checked-in flows that pin candidates **by index** (`select_topology`) will
  renumber (the wishlist-topo item measured 16 fast-tier failures incl. the
  goldens); the flip PR owns those re-pins.

## The reference-host procedure

All commands from the repo root, on the golden-owning host.

```bash
# 0. Fresh, clean checkout of the flip branch's base
git checkout main && git pull
git status --porcelain           # must be empty
bin/bb                           # build current default

# 1. HOST-VALIDITY CHECK (pre-flip): must report no CONTENT-DIFFERS
PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify
#    Expected: every flow OK or "OK (order-only)"; exit 0.
#    "OK (order-only)" on text goldens is normal — they predate the
#    PR #327 canonical sort; the gate test canonicalizes both sides.
#    Any CONTENT-DIFFERS here means THIS HOST does not reproduce the
#    goldens (toolchain / -march=native drift) — STOP, do not re-baseline.

# (optional but recommended) 1b. Pre-canonicalize the goldens in their own
#    commit, so the flip commit's golden diff shows PURE content changes:
PYTHONPATH=build:tools python3 tools/regen_goldens.py --write
git add test/tests/data/topo_golden && git commit -m "topo goldens: rewrite in canonical order (no content change)"
#    (--write refuses on a dirty tree, so do this before applying the flip.)

# 2. Apply the flip (the one-line default change) and rebuild
#    src/topology.h:  bool allow_hanan_loci_    = false;  ->  = true;
$EDITOR src/topology.h
bin/bb

# 3. Verify the flip's blast radius matches the recorded classification
PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify
#    Expected: CONTENT-DIFFERS on exactly the 6 flows in the table above,
#    with matching per-bundle pool deltas; the other 4 flows OK.
#    A different list = the tree has drifted since this doc's measurement —
#    re-review before re-baselining.

# 4. Commit the flip FIRST (with its test updates), then re-baseline
git add -A && git commit -m "topo: hanan_loci becomes the generation default"
PYTHONPATH=build:tools python3 tools/regen_goldens.py --write
#    Prints the exact golden file list written (all 10 files are rewritten;
#    only the 6 content-differing ones change bytes vs a canonicalized base).

# 5. Gate: the golden tests (and the rest of fast+mid) must be green
pytest test/tests/test_topo_analysis_golden.py -o addopts="" -m "not slow" -v
bin/bb mid

# 6. Commit the goldens
git add test/tests/data/topo_golden && git commit -m "topo goldens: re-baseline for hanan_loci default-on"
```

## The abbreviated procedure for `claude/hanan-loci-default-flip`

The flip branch already carries steps 2–5's tree changes, so the reference
host only validates and re-baselines:

```bash
# 0. Host-validity check on PRE-flip main (the designed tripwire):
git checkout main && git pull && bin/bb
PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify   # must be all OK / OK (order-only)

# 1. The flip branch:
git checkout claude/hanan-loci-default-flip && git pull && bin/bb
PYTHONPATH=build:tools python3 tools/regen_goldens.py --verify
#    Expected: CONTENT-DIFFERS on exactly 5 flows — four_blocks, dogleg2,
#    comprehensive_demo, big digest, b4_bus_077 — and rnr_mix reads OK
#    (its flow is pinned out with `no_hanan_loci` by owner decision, so
#    its pool matches the checked-in golden; the measured-classification
#    table above predates the pin-out and lists it as a 6th shifter).
#    The other 4 flows OK.  A different list = host or tree drift; STOP.

# 2. Re-baseline (clean tree required) and gate:
git status --porcelain                                            # must be empty
PYTHONPATH=build:tools python3 tools/regen_goldens.py --write
pytest test/tests/test_topo_analysis_golden.py -o addopts="" -m "not slow" -v

# 2b. The NUTS placement goldens shift too (tools/nuts_snapshot.py —
#     discovered by the flip branch's mid-tier run, which the pre-flip audit
#     did not cover): the changed pools re-select topologies in
#     flow/four_blocks.buda and demo/comprehensive_demo.buda (mid tier), and
#     the slow-tier big.buda digest will shift for the same reason
#     (rnr/mix's stays valid — the flow is pinned out).  Same ownership
#     rule: re-baseline ONLY on this host, and review the diff (expect
#     changes confined to those flows).
PYTHONPATH=build:tools python3 tools/nuts_snapshot.py
git diff --stat test/tests/data/nuts_golden

bin/bb mid          # now fully green
bin/bb slow         # the flip's full-tier gate (pending item 6 of the audit)

# 3. Commit onto the SAME branch and push:
git add test/tests/data/topo_golden test/tests/data/nuts_golden
git commit -m "goldens: re-baseline topo + nuts placement for hanan_loci default-on"
git push origin claude/hanan-loci-default-flip
```

## Rollback

Nothing before `--write` touches the goldens. To undo a re-baseline that has
not been committed:

```bash
git checkout -- test/tests/data/topo_golden/
```

After a commit, revert the golden commit (and the flip commit if abandoning
the flip):

```bash
git revert <golden-commit> <flip-commit>
```

## Notes

* `tools/regen_goldens.py` shares every byte of generation/serialization/
  canonicalization with `tools/topo_snapshot.py` and the gate test — it
  imports `topo_snapshot` and calls the same `run_flow_generation` /
  `snapshot_session` / `snapshot_digest` / `canonicalize` functions, so a
  `--verify` all-OK is exactly the gate test's pass condition.
* Generation-stage snapshots are pure integer arithmetic (machine-stable);
  the `-march=native` float divergence documented for post-NUTS stages does
  not apply. A pre-flip `CONTENT-DIFFERS` on a candidate host is still the
  designed tripwire for "this host cannot own the goldens".
* This kit was sanity-gated on a non-reference container: pre-flip `--verify`
  all-OK, the scratch flip classification above, flip reverted, `--verify`
  all-OK again — goldens never rewritten there.
