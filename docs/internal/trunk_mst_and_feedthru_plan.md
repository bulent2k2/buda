# Plan — Trunk+MST Completion & Opt-in Feedthru

Implementation plan for the two design-deferred items called out in
[topology_tree_gen_design.md §17](topology_tree_gen_design.md#17-deferred-work--follow-ups-post-single-tap-completion-2026-06-23).
Both build directly on the **single-tap relay completion** merged in PR #43
(`complete_relay_junctions`: one busterm tap per relay, the rest wired as SEG
junctions; `connect()` off-line detour for collapsing orthogonal landings).

- **Item 1 — Trunk+MST hybrid completion** turns the redundant trunk+MST graph
  into a clean trunk-rooted tree so the existing completion can run without
  creating cycles.
- **Item 2 — Opt-in feedthru** turns the `FEEDTHRU_RELAY` hard error into a
  per-block / per-layer configurable option (the §5 design, finally built).

The two are independent and can land as separate PRs. Item 1 is the smaller,
better-specified change and should go first; item 2 is larger (new config plumbing
through stages 2/3/4/9 + visualizer).

---

## 1. Trunk+MST hybrid completion (MST edge *replaces* a stub)

### 1.1 The problem, precisely

`add_trunk_mst_candidates` (`topology.cpp:1602`) builds a hybrid by:

1. copying a `TRUNK_H/V` topology — which already carries a **stub to every branch
   block** (a branch block = one whose `orig_bbox` does not straddle `trunk_pos`),
   then
2. appending **MST shortcut edges** between those same branch blocks
   (`compute_mst(nodes)` over the branch set, `closest_points` landings).

So a branch block `v` reached by an MST edge `u–v` is now connected **twice**: once
by its own trunk stub, once by the edge from `u`. That is a cycle
(trunk → stub_v → v → edge → u → stub_u → trunk). Running `complete_relay_junctions`
on it would add a perpendicular connector tying the edge to the stub and **close the
loop**, so completion is deliberately skipped (`topology.cpp:1700-1714`) and
`check_topo` flags the uncompleted relays as `FEEDTHRU_RELAY`.

### 1.2 The fix — build a trunk-rooted tree

Make the MST edge **replace** the child block's trunk stub instead of augmenting it.
Concretely, model the hybrid as a spanning tree rooted at the trunk:

- The trunk spine is the root.
- Each branch block attaches to the tree exactly once — either **directly**
  (its trunk stub) or **via an MST edge** to a sibling that is already attached
  (closer to the trunk).
- When an MST edge `u–v` is kept and `v` is the child (farther from the trunk),
  **drop `v`'s trunk stub**. `u` keeps its connection (stub or its own parent edge).

The MST that `compute_mst` already returns is a tree over the branch blocks; we only
need to **root** it and decide, per edge, which endpoint is the child whose stub is
removed. Root selection: the branch block **nearest the trunk** (min
`manhattan_nearest(rect, trunk-projection)`, the same metric already computed at
`topology.cpp:1646-1649`) is the tree root; orient every edge away from it (BFS/DFS
from the root). For each directed edge `parent → child`, the child's stub is the one
to remove.

After stub removal the graph is a tree (no cycles), so `complete_relay_junctions`
runs unchanged and the single-tap model applies: each block keeps one busterm tap,
edge/stub junctions become SEG.

### 1.3 Algorithm changes (`add_trunk_mst_candidates`)

```
existing: compute branch_idx, nodes, mst_edges, copy trunk into new_t, append edges
new:
  1. root = argmin_i manhattan_nearest(node_i.rect, trunk-projection)   # closest branch block
  2. orient mst_edges away from root (BFS over the undirected MST) -> parent[child]
  3. child_blocks = { v : edge parent->v kept }                        # blocks losing their stub
  4. when copying the trunk into new_t, SKIP the stub segment(s) of every block in
     child_blocks (identify a stub by its busterm endpoint on that block; a block's
     stub is the trunk-incident segment annotated to it).
  5. append the MST edge segments as today (closest_points landings, min-stub guards)
  6. annotate_endpoints(new_t, blocks)
  7. complete_relay_junctions(new_t, blocks, floorplan_, h_layer_, v_layer_)   # now safe
```

Step 4 is the only fiddly part: the trunk builders (`add_trunk_h/v`) emit one stub
segment per branch block, landing on the block face. To "skip a stub" we either
(a) regenerate the trunk without those stubs, or (b) post-filter `new_t.segments`,
dropping the segment whose endpoint lands on a `child_block` face and whose other
end meets the trunk line. Option (b) is local and avoids touching the trunk builders;
prefer it, with a helper `remove_stub_for(Topology&, block_name, trunk_pos, is_h)`.

Edge cases:
- **Child block also straddles nothing but has multiple rects** — use the
  representative rect already chosen in `nodes` (the one nearest the trunk).
- **A kept edge whose child has no removable stub** (shouldn't happen for branch
  blocks, but guard): if no stub is found, fall back to the current behaviour for
  that block (leave it; completion will still single-tap it) and emit a debug note.
- **Degenerate / min-stub-violating edges** — keep the existing `valid=false`
  rejection so we never emit a dangling shortcut.

### 1.4 Verification & tests

- **Flip the deferred test.** `test_trunk_mst_relay_currently_flagged_deferred`
  (`test/tests/test_mst_completion.py`) currently asserts ≥1 trunk+MST hybrid is
  `FEEDTHRU_RELAY`-flagged. Replace with `test_trunk_mst_completed_no_feedthru`:
  for every `+MST` candidate, `check_topo` reports **zero** `FEEDTHRU_RELAY`, the
  topology is one SEG-connected component, every block has ≤1 busterm tap, and no
  zero-length segments — reuse `_busterm_taps` / `_seg_components` helpers added in
  PR #43.
- **No cycle / tree invariant.** Add `test_trunk_mst_is_acyclic`: the SEG-junction
  graph over segments has `components == 1` and `edges == nodes - 1` (tree), or
  equivalently assert the block-level connection graph is acyclic.
- **Wirelength honesty.** Assert the completed `+MST` candidate's
  `estimated_wirelength` is ≥ the raw trunk's (stub removed, edge added) and that
  the planner still accepts it (`run_planner` smoke on a small flow).
- **Regression sweep.** Run the STAIRCASE/PLUS/GRID probe from PR #43 plus the fast
  tier. Re-run `dump_topologies --problems` on `tc3a_flat` to confirm `+MST`
  candidates no longer show as feedthru-problematic.

### 1.5 Effort / risk

Small–medium, **localized to `add_trunk_mst_candidates`** plus one test flip.
No new public API, no CLI, no downstream-stage changes. Main risk is the stub-removal
matching (step 4) misidentifying a stub; mitigated by the acyclic + single-component
assertions, which fail loudly if a stub is wrongly kept (cycle) or wrongly dropped
(open).

---

## 2. Opt-in feedthru (build the §5 design)

### 2.1 Scope

Implement the §5 `FeedthruConfig` so a block can be **declared routable-through**: a
trunk that crosses an opted-in block does **not** stub to it and is **not** flagged
`FEEDTHRU_RELAY`; instead the crossing is recorded so the visualizer and detailed
NUTS treat the block's interior as the place the connection is completed later.

This is the inverse policy knob to PR #43's single-tap completion: completion makes
every relay a real wire (the safe default); feedthru lets the designer say "no, this
block's own router will bridge it — leave it disconnected on purpose."

### 2.2 Data model

Per §5.2–5.3, add to `Floorplan` (`topology.h/cpp`):

```cpp
struct FeedthruConfig {
    bool global = false;
    std::map<std::string,bool> per_block;
    std::map<int,bool>         per_layer;
};
// resolution: per_block > per_layer > global
bool Floorplan::get_feedthru(const std::string& block, int layer_id) const;
void Floorplan::set_feedthru(bool);
void Floorplan::set_feedthru_block(const std::string&, bool);
void Floorplan::set_feedthru_layer(int, bool);
```

Add `std::vector<FeedthruCross> feedthru_blocks;` to `Topology` (new field), where a
`FeedthruCross` records `{block_name, enter_point, exit_point, layer}` for each trunk
segment that passes through an opted-in block. This mirrors how `bridge_segments`
already lives beside `segments`.

### 2.3 Generator changes (stage 2, `topology.cpp`)

In the trunk builders (`add_trunk_h`/`add_trunk_v`) and the pass-through detection at
`topology.cpp:160`:

- When the trunk projection `pos` falls **inside** block `b.bbox` and
  `get_feedthru(b.name, trunk_layer)` is true → **do not** emit the stub; record a
  `FeedthruCross` (enter/exit = the two faces where the trunk crosses `b`) in
  `Topology::feedthru_blocks`. (Today this is already a "pass-through"; feedthru just
  makes it explicit + recorded instead of an implicit relay.)
- When `pos` is **outside** `b.bbox` → ignore feedthru, emit the normal stub
  (§5.5 rule 1).

### 2.4 Verifier changes (`verify.cpp::check_topo`)

`FEEDTHRU_RELAY` must become **conditional**: a relay/pass-through on a block listed
in `Topology::feedthru_blocks` (i.e. `get_feedthru` true for that block+layer) is
**expected** and not a violation. Add a distinct informational kind if useful
(`FEEDTHRU_OK`) or simply suppress the violation. Keep flagging non-opted-in
through-blocks — that is still the safety net.

### 2.5 Detailed NUTS changes (stage 9, `detailed_nuts.cpp`)

Per §5.5: a feedthru block's crossing positions must be included in the bit-wire span
so each bit reaches **both** crossed faces (the internal router bridges between them).
For each `FeedthruCross` on a bus segment, extend the per-bit `span_lo/hi` to the
enter/exit faces (do not place tracks *inside* the block; just ensure the wire lands
on both faces). No new track placement — only span endpoints.

### 2.6 CLI + bindings + visualizer

- **CLI** (`buda_cli.py`): `set_feedthru [true|false]`,
  `set_feedthru_block <name> [true|false]`, `set_feedthru_layer <id> [true|false]`
  → `BudaSession.floorplan` setters. Register in `do_command` + document in
  `BUDA_SCRIPT_REFERENCE.md` and the `CLAUDE.md` command table.
- **Bindings** (`bind_routing.cpp` — Floorplan lives in the routing module): expose
  the four `Floorplan` feedthru methods and `Topology::feedthru_blocks`.
- **Visualizer** (`buda_viz.py`): draw feedthru crossings distinctly (e.g. a dashed
  segment across the block + a small marker), and ideally a per-type toggle, so a
  designer can see which blocks are being routed through on purpose.

### 2.7 Tests

- Un-xfail `test/tests/test_feedthru.py` (3 specs already written to the §5 design)
  and extend:
  - config resolution order (`per_block > per_layer > global`);
  - opted-in crossing → no stub, `feedthru_blocks` populated, **no** `FEEDTHRU_RELAY`;
  - opted-out crossing of the same geometry → stub present (or `FEEDTHRU_RELAY` if a
    relay), proving the knob actually gates behaviour;
  - `pos` outside bbox → stub regardless of feedthru (§5.5 rule 1);
  - detailed-NUTS: feedthru bus segment's bits span to both faces.
- A `.feature` file (`feedthru.feature`) per the Gherkin stub in §14.

### 2.8 Effort / risk

Medium–large: touches stages 2, 3-verify, 4/9, CLI, two binding files, and the
visualizer. Lower algorithmic risk than item 1 (it is mostly plumbing a config flag
and *suppressing* work), but broad surface area → land behind the existing xfail
specs and grow tests stage by stage. Recommend a **stacked sub-sequence**: (a) config
+ generator + verifier (makes the xfail topology tests pass), (b) detailed-NUTS span,
(c) CLI + viz.

---

## Sequencing

1. **Item 1 first** — small, self-contained, flips one deferred test, removes a real
   `FEEDTHRU_RELAY` source. One PR.
2. **Item 2 next** — independent; can start in parallel but is larger. Land as a
   short stack (config/generator/verifier → dNUTS → CLI/viz) so each step has green
   tests.
3. After both, re-measure `tc3a_flat` (§17 item 3) and revisit whether the 40
   unplaced bits were a feedthru-vs-completion modelling artifact.

> Re-run `dump_topologies [hint] [--problems]` and the STAIRCASE/PLUS/GRID
> connectivity probe after each item to confirm: single tap per block, one
> SEG-connected component, and `FEEDTHRU_RELAY` only where genuinely intended.
