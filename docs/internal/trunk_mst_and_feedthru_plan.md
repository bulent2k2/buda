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

## 1. Trunk+MST hybrid completion (MST edge *replaces* a stub) — IMPLEMENTED

> **Status — DONE.** Implemented in `add_trunk_mst_candidates` per the plan below,
> with one addition the plan did not anticipate: the completed hybrid is accepted
> only if it verifies as a clean tree (`topology_is_clean_tree`: one SEG component
> **and** acyclic). Two geometries defeat a naive completion and are dropped rather
> than emitted — (a) a kept stub *collinear* with an incident MST edge (ConnTopology
> can't infer the collinear join → split), and (b) a kept stub that *crosses* another
> branch block (pass-through), leaving a redundant second path that completion closes
> into a loop. The root is chosen among **stub-owning** branch blocks (a pass-through
> block owns no stub, so rooting there would detach the cluster). Multi-rect / blocks
> with no stub-owning root keep the legacy flagged form. Tests:
> `test_trunk_mst_completed_no_feedthru`, `test_trunk_mst_root_double_tap_demoted`.

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

> **Status — PLANNED (implementation-ready).** The contract is already pinned by
> `test/tests/features/feedthru.feature` + `test_feedthru.py` (currently
> `xfail`-marked). This section is reconciled with the post-#44 code and the exact
> behaviour those scenarios assert. Line anchors are against `main@101139a`.

### 2.1 What feedthru means (reconciled with the completion work)

#43/#44 made every relay a **real wire** (the safe default): a block touched by two
bus segments is wired through with connectors, and `check_topo` hard-errors
(`FEEDTHRU_RELAY`) on any block that is silently relayed. Feedthru is the **opt-in
inverse**: the designer declares a (usually large) block as routable-through, and the
trunk is allowed to enter one face and exit the other **without** a physical
connection inside it — the block's own lower-level router bridges the gap later.

Authoritative behaviour, straight from `feedthru.feature`:

| Situation | Default (no feedthru) | Feedthru enabled for the block |
|---|---|---|
| Trunk **straddles** block `FT` (trunk pos ∈ `FT` bbox) | one **continuous** trunk segment crossing `FT`; `FT` counted in `pass_through_count`; `feedthru_blocks` empty | trunk is **split** into two segments at `FT`'s two crossed faces (a gap over `FT`); **no** stub to `FT`; `FT` name added to `Topology::feedthru_blocks` |
| Trunk pos **outside** `FT` bbox | normal stub to `FT` | **ignored** — still a normal stub (§5.5 rule 1) |

So feedthru only changes the **straddle / pass-through** case, turning the implicit
continuous pass-through into an explicit split-with-gap that is recorded.

### 2.2 Data model — a true block×layer grid (NOT the MinStubLength shape)

Feedthru is genuinely **per-(block, layer)**: a block may be routable-through on the
H-trunk layer but not the V-trunk layer, and the designer wants to scope a set of
blocks to a *subset* of layers. `MinStubLength`'s shape (independent `per_block` and
`per_layer` fallback axes) **cannot express this** — `per_block[A]=true` forces A
feedthru on every layer. So feedthru needs a **4-tier** config with an explicit
(block, layer) layer, resolved most-specific-first:

```cpp
struct FeedthruConfig {                    // topology.h, near MinStubLength (:101)
    std::map<std::pair<std::string,int>, bool> per_block_layer;  // (block, layer)  — most specific
    std::map<std::string, bool>                per_block;         // (block, *)
    std::map<int, bool>                        per_layer;         // (*, layer)
    bool                                       global = false;    // (*, *)        — least specific
};
// in class Floorplan (public, after the min-stub accessors ~topology.h:156):
void set_feedthru(bool v)                              { feedthru_.global = v; }
void set_feedthru_block(const std::string& n, bool v)  { feedthru_.per_block[n] = v; }
void set_feedthru_layer(int lid, bool v)               { feedthru_.per_layer[lid] = v; }
void set_feedthru_block_layer(const std::string& n, int lid, bool v) {
    feedthru_.per_block_layer[{n, lid}] = v;
}
bool get_feedthru(const std::string& n, int lid) const {   // most specific wins
    auto it = feedthru_.per_block_layer.find({n, lid});
    if (it != feedthru_.per_block_layer.end()) return it->second;
    if (feedthru_.per_block.count(n))   return feedthru_.per_block.at(n);
    if (feedthru_.per_layer.count(lid)) return feedthru_.per_layer.at(lid);
    return feedthru_.global;
}
// private member (~topology.h:190):  FeedthruConfig feedthru_;
```

**Precedence (block beats layer):** `(block,layer) > (block,*) > (*,layer) > (*,*)`.
This makes carve-outs work as a designer expects: `set_feedthru * M4 on` then
`set_feedthru A M4 off` leaves A-on-M4 **off** (the `(A,M4)` rule beats `(*,M4)`).
Each rule stores an explicit bool, so a more-specific `off` overrides a broader `on`
(and vice-versa); a less-specific rule never overrides a more-specific one. Document
this ordering in `BUDA_SCRIPT_REFERENCE.md`.

Add to `Topology` (`topology.h:74-91`, beside `bridge_segments`):

```cpp
std::vector<std::string> feedthru_blocks;   // names the trunk passes through (opt-in)
```
`feedthru_blocks` stays a **`vector<std::string>` of names** — the feature steps test
`blk in c.feedthru_blocks` (name membership); crossing geometry is recoverable from
the split segment endpoints, so no struct is needed for the MVP.

### 2.3 Generator changes (stage 2, `topology.cpp`) — the core — **IMPLEMENTED**

> **The model: a feedthru block is a busterm of the bundle.** Two earlier drafts got
> this wrong. The *first* walked the endpoint `blocks` vector but assumed a feedthru
> was a src/dst stub. The *second* (shipped briefly) over-corrected to scanning
> `floorplan_.get_all_blocks()` for *unrelated* crossed blocks — but per CLAUDE.md a
> trunk merely crossing an unrelated block is a **pass-through, not a feedthru**. The
> right model (and what ships now): a feedthru block is one the topology **actually
> connects to** — a bundle busterm in `blocks[i]` that the trunk passes straight
> through (`!has_stub[i]`). Splitting at its faces gives it *two* BUSTERM landings, i.e.
> it "connects ≥2 of the bundle's stubs via its own routing" — the CLAUDE.md feedthru
> definition exactly. This also makes the split safe for NUTS (see §2.4): the half-spine
> inner endpoints are busterm-anchored to the block's faces, not free to slide off.

Shipped in `add_trunk_h` (x-faces) and `add_trunk_v` (y-faces). After `x_lo/x_hi`
(resp. `y_lo/y_hi`) and the spine `y_trunk` (resp. `x_trunk`) are fixed, and gated on
`floorplan_.feedthru_active()` (cheap "any rule set?" guard so non-feedthru flows are
byte-identical):

- For each bundle block `i`: skip unless `!has_stub[i]` (the trunk passes through it —
  V also skips `stub_suppressed[i]`); skip unless `get_feedthru(blocks[i].block_name,
  h_layer_/v_layer_)`; skip multi-rect (`blocks[i].rects.size() > 1`, MVP). Clip the
  block's `orig_bbox` face extent to `[x_lo,x_hi]` (resp. `[y_lo,y_hi]`); if non-empty,
  record the gap, set `is_feedthru[i]`, and push the name to `t.feedthru_blocks` (sorted).
- **Split** the spine around the sorted gaps (emit `x_lo→g.first`, skip `[g.first,
  g.second]`, continue from `g.second`; the existing `if (x_lo < x_hi)` guard drops
  zero-length pieces). No gaps → emit the single segment exactly as before.
- `pass_through_count` now **excludes** feedthru blocks (`!has_stub[i] &&
  !is_feedthru[i] && …`): a fed-through block is an explicit split, not a silent relay.

**Edge cases handled:** a block at the trunk's extreme end (`x_lo`/`x_hi`) yields a
degenerate gap and is skipped, so the src and the outermost dsts are never split — they
connect normally; multi-rect feedthru is skipped (MVP); per-layer gating falls out of
`get_feedthru(name, trunk_layer)`; an unrelated block (not a bundle busterm) is never
even considered.

**Known limitation:** on a multicast bundle that also generates `TRUNK_*+MST` hybrids,
splitting the spine can make a hybrid fail the clean-tree check (from #44) and be
dropped on the feedthru path. Acceptable for the MVP (hybrids are an optimization, and
only feedthru flows are affected); revisit if needed.

### 2.4 Verifier (`verify.cpp::check_topo`) — **IMPLEMENTED**

Because a feedthru block is now a busterm with **two** BUSTERM landings (one per
half-spine), the `FEEDTHRU_RELAY` check (`verify.cpp:160-225`, fires on ≥2 BUSTERM
segments on one single-rect block whose wires don't geometrically touch) *would* flag
it — correctly, since the two halves connect only through the block's interior. That is
exactly the declared, opt-in relay, so `check_topo` now **skips** the violation when the
block name is in `topo.feedthru_blocks` (beside the existing multi-rect/TEG skip). An
*undeclared* relay on the same geometry (metadata stripped) is still flagged — the skip
is gated on the declaration, so silent relays from raw MST edges remain caught.

Still add the opt-in skip for completeness, so a feedthru block used as an **MST relay**
(≥2 stubs landing on it) is allowed rather than hard-errored — one line beside the
existing TEG skip at `verify.cpp:210`:

```cpp
if (fp.get_feedthru(bname, segs[sidx[0]].layer_id)) continue;  // opted-in relay
```

### 2.5 Detailed NUTS (stage 9, `detailed_nuts.cpp`) — refinement

For sign-off geometry each bit must reach **both** crossed faces so the block's
internal router can bridge them. The split already lands the two trunk halves on the
faces, so per-bit `span_lo/hi` (`detailed_nuts.h:38-39`) are correct by construction —
this stage is only needed if we later want an explicit "internal bridge" annotation.
Deferrable past the MVP; the feature scenarios do not exercise dNUTS.

### 2.6 CLI command — one unified block×layer grid

A **single** command (chosen over the MinStubLength-style trio so the full grid,
including per-pair carve-outs, is expressible):

```
set_feedthru <blocks> <layers> [on|off]
```

- `<blocks>`: comma-separated block names (`A,B,C`) **or** `*` / `all` (no spaces
  inside the token — it is one positional arg).
- `<layers>`: comma-separated layer names or ids (`M4,M5` or `4,5`) **or** `*` / `all`.
- `[on|off]`: optional, default **`on`** (also accept `true`/`false`, `1`/`0`).

```
set_feedthru FT *           # FT, all layers, on
set_feedthru * M4,M5        # all blocks, layers M4+M5
set_feedthru A,B M4         # blocks A,B on M4 only
set_feedthru A M5 off       # carve out one (block,layer) pair
set_feedthru * * on         # global default on
```

**Parsing** (new `elif cmd == "set_feedthru"` branch in `do_command`, plus
`"set_feedthru"` in the `KNOWN_COMMANDS` allowlist at `buda_cli.py:43-57`):

1. `blocks_tok = args[0]`; `blocks_wild = blocks_tok.lower() in ("*", "all")`; else
   `blocks = blocks_tok.split(",")`, validated against `fp.get_all_blocks()`
   (the name set used at `buda_cli.py:1465` / `:2770`) — warn on unknown, mirroring
   `add_keepout`'s warn-and-skip.
2. `layers_tok = args[1]`; `layers_wild = layers_tok.lower() in ("*", "all")`; else
   for each `t in layers_tok.split(",")`: `int(t)` if `t.isdigit()` else
   `self._layer_name_map.get(t)` — the exact name→id resolution `add_keepout`
   (`:1708-1741`) and `set_min_stub_length_layer` (`:1683-1689`) already use.
3. `val = True` unless `args[2]` parses to off/false/0.
4. **Dispatch** to the four `Floorplan` setters by wildcard combination:
   | blocks | layers | call |
   |---|---|---|
   | `*` | `*` | `fp.set_feedthru(val)` |
   | `*` | list | `for lid: fp.set_feedthru_layer(lid, val)` |
   | list | `*` | `for n: fp.set_feedthru_block(n, val)` |
   | list | list | `for n,lid: fp.set_feedthru_block_layer(n, lid, val)` |

   (No new range parser needed; comma-`split` suffices. If `m-n` layer ranges are ever
   wanted, lift the inline range parser from `select_topologies` at
   `buda_cli.py:2413-2434` into a shared helper.)

Document the command + the precedence rule in `BUDA_SCRIPT_REFERENCE.md` and the
`CLAUDE.md` setup-command table.

### 2.7 Bindings + visualizer

- **Bindings** (`bind_routing.cpp`): expose the four `Floorplan` feedthru setters +
  `get_feedthru` beside `set_min_stub_length*` at `bind_routing.cpp:120-124`, and add
  `.def_readwrite("feedthru_blocks", &Topology::feedthru_blocks)` on the `Topology`
  binding (so the feature steps can read it).
- **Visualizer** (`buda_viz.py`): draw a feedthru block with a dashed outline and the
  trunk gap over it (the feature file's `+-·-·+` notation); optional per-type toggle.

### 2.8 Tests

- **Shipped:** `test_feedthru_config.py` (8 tests — resolver precedence: global,
  per-layer, per-block, per-pair, block-beats-layer, carve-out, pair-beats-block) and
  `test_feedthru_topology.py` (8 tests — the trunk-split over a real `A → [mid, B]`
  multicast where `mid` is a *destination* the trunk passes through: disabled=continuous
  pass-through, per-block/global/per-layer split at `mid`'s faces, **unrelated block
  never fed through**, stubbed/extreme dst not split, **two BUSTERM landings on `mid` +
  `check_topo` clean**, undeclared-relay-still-flagged).
- The split path is gated entirely on `feedthru_active()`/`get_feedthru`, so the whole
  existing suite is unperturbed (fast tier: **467 passed**).
- **Deferred:** `features/feedthru.feature` + `test_feedthru.py` stay **xfail**. The
  feature is idealized and does not match generator output — its scenarios assume a
  2-block `A → [B]` net yields `TRUNK_H@y150`, but the generator emits a straight
  `I_H` there (trunks need genuine multicast), assume a trunk lands exactly at `y=150`
  (real trunks land on Hanan lines, e.g. `y=140`), and scenario 6 needs a
  `feedthru_penalty` ranking knob that does not exist. Realigning the spec is its own
  item (see below).

### 2.9 Phasing & status

1. **Config + bindings + CLI** — ✅ **DONE**: 4-tier `FeedthruConfig` + setters +
   `get_feedthru` (§2.2), unified `set_feedthru` command (§2.6), bindings (§2.7),
   `test_feedthru_config.py`, docs.
2. **Generator trunk-split + `feedthru_blocks`** — ✅ **DONE**: §2.3 with the corrected
   busterm-only model (a fed-through block is a bundle busterm the trunk passes through,
   not an unrelated crossed block), `Topology::feedthru_blocks` + binding + hier-clone
   propagation, `test_feedthru_topology.py`.
3. **Verifier skip (§2.4)** — ✅ **DONE**: `check_topo` skips `FEEDTHRU_RELAY` for blocks
   declared in `topo.feedthru_blocks`; undeclared relays still flagged.
4. **Visualizer (§2.7)** — *not started* (cosmetic: dashed feedthru block + gap).
5. **dNUTS internal-bridge annotation (§2.5)** — *deferred* (the half-spines are
   busterm-anchored to the block's faces, so spans are correct by construction).

**Deferred follow-ups (own items):**
- **Feature realignment** — rewrite `feedthru.feature` to multicast-trunk geometries
  (and assert on the real trunk y, not a hardcoded `150`), then un-xfail.
- **Straight/I-shape feedthru** — extend the split to `I_H`/`I_V` (and `L`/`Z`) so a
  point-to-point net through a busterm it passes through also splits. Today only the
  multicast `TRUNK_H`/`TRUNK_V` builders split.
- **`feedthru_penalty` ranking** — model the implicit internal-routing cost so a
  feedthru candidate ranks below an equivalent stub candidate (feature scenario 6).
- **Multi-rect feedthru** and the `TRUNK_*+MST` hybrid interaction (§2.3 limitation).

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
