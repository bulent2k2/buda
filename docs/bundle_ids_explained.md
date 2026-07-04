# Bundle IDs Explained

## TL;DR

- In the **flat flow** and up through the hier flow's **bundler / topology-generation**
  stages, bundle IDs are a dense, consecutive `1, 2, …, N` — assigned sequentially by
  the bundler.
- After **`run_planner hier`**, the *runtime* bundle IDs are typically **no longer
  consecutive**: they can span a wider range with gaps (e.g. `6…178` with dozens of
  missing values). This is a **benign, by-design artifact** of per-instance template
  expansion, **not** a bug and **not** a correctness problem.
- Every bundle keeps **one stable ID** that is consistent across the runtime view, the
  persisted BDB, and resume (`load_pipeline`). Routing, NUTS, detailed NUTS, and
  wirelength are all unaffected by the gaps.

This document explains *why* the IDs look the way they do, so the numbering is not
mistaken for a defect.

---

## Where IDs come from

### Bundler stage — dense `1..N`

Both bundlers mint IDs by a simple running counter:

- Flat `Bundler`: `b.id = ++bundle_id_counter;` (`src/bundler.cpp:80`).
- `HierarchicalBundler`: `b.id = ++next_id;` in **both** grouping passes — cross-level
  (Phase 2a, `src/bundler.cpp:378`) and same-level (Phase 2b, `src/bundler.cpp:431`).

Because the counter only ever increments, the bundler's output is always a dense,
ascending, gap-free set `1, 2, …, N`. You can observe this directly with
`dump_hbundles` right after `run_bundler` / `run_hier_bundler`, and it stays dense
through `generate_topologies` / `generate_hier_topologies` and through the bundle
persistence into the BDB (`_persist_bundles` writes `row.id = str(hb.id)`,
`src/buda_cli.py`).

**Example — `flow/rnr/slowdown.buda`.** This flow stops right after
`generate_hier_topologies`, so its 100 HBundles carry IDs **`1..100`, consecutive and
gap-free** (22 at depth 1, 78 at depth 2). Nothing about it is anomalous.

### After `run_planner hier` — gaps appear

The hier planner does **template expansion** before it plans (`_expand_hier_bundles`,
`src/buda_cli.py`). Cell-level bundles are *templates*: one template is solved once per
cell type and instantiated at every occurrence of that cell. Expansion replaces each
cell-level template with **one per-instance wrapper per instance**, and each new wrapper
needs its own unique ID:

```
# src/buda_cli.py — _expand_hier_bundles
max_id  = max(existing bundle ids)
next_id = max_id + 1          # synthetic IDs start ABOVE every real bundle id
...
clone = self._clone_hbundle_with_id(b, next_id); next_id += 1
```

So after expansion:

- **Cross-block (top-level) bundles** are *not* expanded — they keep their **original
  low IDs**.
- **Cell-level templates** are consumed and replaced by per-instance wrappers that get
  **fresh synthetic IDs starting at `max_id + 1`** (e.g. `101, 102, …`).
- The original IDs that belonged to the consumed templates are now **holes** — no live
  wrapper carries them.

The result is a valid but **sparse** ID set: the surviving low IDs are the cross-block
bundles; the high IDs are the expanded instances; the gaps are where templates used to
be.

**Example — `flow/rnr/slowdown_rnr.buda`.** Same setup as `slowdown.buda` plus
`run_planner hier`. Post-expansion there are 100 wrappers whose IDs range roughly
**`6…178` with ~73 gaps** — the 22 cross-block bundles keep low IDs (`6`, `11`, …) and
the expanded instances occupy `101…178`, leaving the consumed template IDs empty.

---

## Why the gaps are harmless

Bundle IDs are **opaque unique keys**, not an index or a count. Nothing in the pipeline
requires them to be contiguous:

- **Uniqueness is what matters.** Expansion deliberately starts synthetic IDs *above*
  the max existing ID precisely so a per-instance wrapper never collides with a
  cross-block bundle. Assignment matching (`bid_to_wrapper`), NUTS `TrackSegment.bundle_id`,
  detailed-NUTS `net_id`, and viz's per-bundle artist registry all key off a **unique**
  ID — they never assume `1..N`.
- **One stable ID per bundle, end to end.** The runtime ID equals the persisted BDB ID
  equals the ID rehydrated by `load_pipeline`. That stability is what makes resume
  byte-identical (`test/tests/test_bdb_resume_gaps.py::test_hier_resume_to_dnuts_flow_level`
  asserts `net_segments[].bundle_id` matches between a single-session run and a resumed
  one).
- **Template semantics live in the schema, not the numbering.** Expanded rows are
  marked `is_replicated=1` with `parent_id = <template id>`; templates are
  `is_replicated=0`. Consumers (`load_pipeline expanded`, the resume tests) select on
  those flags, never on literal ID values.

In short: the gaps carry no meaning and break nothing.

---

## Why they are *not* trivially "re-densified" to `1..N`

It is tempting to renumber the post-expansion bundles to a clean `1..N`. That turns out
to be a **non-trivial, resume-sensitive change**, for one structural reason:

1. **Runtime ID == persisted ID is load-bearing.** Resume equality (above) only holds
   because the persisted expanded ID equals the runtime ID. So renumbering the runtime
   IDs *forces* renumbering the persisted BDB IDs too — a display-only remap would make
   resume diverge (or introduce a confusing second ID per bundle).

2. **`bundle.id` is a sole primary key, and templates occupy the low IDs.** In the BDB,
   `bundle.id` is `TEXT PRIMARY KEY` (`src/bdb.cpp:103`), and `add_bundle` is an UPSERT
   on that key. The cell-**template** rows (`is_replicated=0`) remain persisted at their
   original low IDs (`1..100`) and are **required** by `load_pipeline` — the
   pre-expansion view loads the `not is_replicated` rows, and the expanded view uses the
   template IDs as `parent_id` anchors (`src/buda_cli.py`, `_load_pipeline_from_bdb`).
   Reusing a freed template's ID slot for a routed instance would UPSERT-overwrite the
   live template row, and `clear_expanded_bundles` would then delete it — **breaking
   resume**.

Therefore a *real* densification to `1..N` requires **relocating the cell-templates to a
disjoint ID namespace** and re-keying their `bundle_net` / `bundle_busterm` /
`topology` / `topology_segment` FK rows, so routed instances can safely own `1..N`. That
is a meaningful change with real exposure in exactly the hier/resume paths, and it must
be covered by the full hier + resume suite (and possibly a regen of the
`*.bdb.sql` fixtures) before it can be trusted.

**Decision (2026-07):** leave the IDs as-is. The non-consecutive post-expansion IDs are
a benign artifact, every bundle keeps a single stable ID, and densifying buys only
cosmetics at the cost of resume risk. This document exists so the numbering is
understood rather than "fixed."

---

## Quick reference

| Stage | Command | Bundle IDs |
|---|---|---|
| Bundler | `run_bundler` / `run_hier_bundler` → `dump_hbundles` | **dense `1..N`** |
| Topology gen | `generate_topologies` / `generate_hier_topologies` | **dense `1..N`** |
| BDB persist (bundler) | (`_persist_bundles`) | **dense `1..N`** (same as runtime) |
| Flat planner | `run_planner` | **unchanged** (no expansion) |
| **Hier planner** | **`run_planner hier`** | **sparse** — cross-block keep low IDs, expanded instances get `max_id+1…`, consumed template IDs become gaps |
| NUTS / detailed / resume | `run_nuts`, `run_detailed_nuts`, `load_pipeline` | same (stable) IDs as after the planner |

### Relevant code

- `src/bundler.cpp:80, 378, 431` — sequential ID assignment (dense).
- `src/buda_cli.py` — `_expand_hier_bundles` (synthetic IDs `= max_id + 1`),
  `_add_expanded_bundle` (persist expanded rows, `is_replicated=1`, `parent_id=template`),
  `_persist_bundles` / `_persist_planner_output` (persist keyed by runtime ID),
  `_load_pipeline_from_bdb` (rehydrate; expanded vs. pre-expansion views).
- `src/bdb.cpp:103` — `bundle.id TEXT PRIMARY KEY`; `add_bundle` UPSERT on it.
- `test/tests/test_bdb_resume_gaps.py` — resume equality that pins runtime ID to
  persisted ID.

### See also

- [Hier Bundler](HIER_BUNDLER.md), [Hier Planner](HIER_PLANNER.md)
- [Cross-Level Bundling](cross_level_bundling.md)
- [HBundle Pipeline session notes](session_hbundle_pipeline.md)
- [BDB Reference](BDB_REFERENCE.md)
