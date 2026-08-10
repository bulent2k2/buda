# BUDA Script Reference — Stage 1 — Bundler

Grouping nets into buses: `run_bundler`, `run_hier_bundler`, `dump_hbundles`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 1 — Bundler

### `run_bundler`

```
run_bundler strict
run_bundler convergent
run_bundler divergent
run_bundler bidirectional
run_bundler combined
run_bundler convergent divergent      # the JOIN of two relations
run_bundler [strategy...] --dump
```

**Several strategy tokens name a SET of relations**, and the bundler takes
their *join*: nets merge when connected by a chain of **any** of them. A
strategy names one relation, which is enough until a design carries more than
one shape — `flow/rv` takes a 32-bit bus IN from 32 pads (fan-in) and sends
another OUT to 32 pads (fan-out), and no single strategy bundles both.
`strict` names no relation and so cannot be joined; a repeated token is
refused. One token behaves exactly as it always did.

Group all nets in the netlist into `Bundle` objects (default `strict`). Must be
called after all `add_net` / `add_bus` commands and before
`generate_topologies_for_bundle`.

**`--dump`** prints one line per created bundle — the flat-flow counterpart of
`dump_hbundles` — after the `Bundler created N hbundles.` summary:

```
  b-1    nets=4     "DRV:A|REC:B"  [bus_03_0, bus_03_1, bus_03_2…]
  b-2    nets=2     "DRV:A|REC:C"  [bus_07_0, bus_07_1]
```

Each line shows the bundle id, its net count, the grouping **reason** (the
driver/receiver signature), and the first few net names.

| Strategy | Grouping rule |
|---|---|
| `strict` | Driver instance **and** sorted receiver instances must match exactly (a true parallel bus). |
| `convergent` | Only sorted receiver instances must match; different drivers allowed (fan-in). |
| `divergent` | The mirror: only the **driver** instance must match; different receivers allowed (fan-**out**). Opt-in and deliberately **not** part of `combined` — shared-driver is a far weaker signal than shared-receiver, so ask for it by name. |
| `bidirectional` | Direction-agnostic: the signature is the sorted set of **all** endpoint instances (driver + receivers), so nets connecting the same group of blocks in any roles bundle together — `A→B` with its return `B→A`, or the cyclic `a→b,c` / `b→c,a` / `c→b,a`. |
| `combined` | The **join** of `convergent` and `bidirectional`: nets merge when connected by a *chain* of either relation (union-find). The only genuinely new point on the strategy lattice `strict ⊂ {convergent, bidirectional} ⊂ combined` — maximal bundling; restrict per net prefix with `set_bundling`. |

> ℹ️ `bidirectional` groups nets that connect the **same** blocks, so the single
> block-to-block trunk routes every net (routing is direction-agnostic) — it is
> sound. In the visualizer such a busterm is both a driver and a receiver and is
> drawn with its own symbol (green diamond).
>
> ℹ️ A `convergent` (or `combined`) bundle spanning multiple driver blocks
> routes as a **fan-in tree** rooted at the shared sink with every driver as a
> leaf, and the realization is **per-bit tapered**: each segment carries only
> the bits whose driver→sink path uses it, verified by `check_design`'s
> `NET_DRIVER_OPEN` and `BIT_SHORT` audits. See
> [`docs/internal/convergent_bundling.md`](../internal/convergent_bundling.md).
>
> ℹ️ A `divergent` bundle is the same object with the arrow reversed: a
> **fan-out tree** rooted at the shared driver with every receiver as a leaf,
> per-bit tapered by the same derivation (it walks driver→receiver, which is
> the direction a fan-out already runs in). Reason `FANOUT:root|TO:leaves`,
> the `FANIN:` twin. It is a real QoR trade rather than a free win — measured
> on `flow/rv`, 127 → 78 bundles at abstract WL −38.8% and detailed WL +5.0%,
> both endpoints clean — so measure before adopting it, and hold a prefix out
> with `set_bundling <prefix> no_divergent`.

Bundle width is computed automatically as `1.5 × (number of nets)` layout
units. The bundler prints the number of bundles created.

**Sidecar:** topology selections saved from a previous `visualize_topologies`
session are loaded later by `run_planner` and applied on top of the planner's
choices (architect overrides).

**Example:**
```
run_bundler strict
```

---

### `set_bundling`

```
set_bundling <prefix>|* <strict|no_convergent|no_bidirectional|no_divergent|combined>
```

Per-net-prefix bundling permission, applied at the next `run_bundler` OR
`run_hier_bundler` (any strategy). The **longest matching prefix** wins; `*` sets the global
default. A merge via a relation happens only when the strategy enables it
**and both nets permit it** — so `set_bundling clk_ strict` keeps clock nets
out of every convergent/divergent/bidirectional merge while the rest of the design
bundles maximally under `combined`. `set_bundling <prefix> combined` restores
full permission for a sub-prefix under a stricter global default.

---

### `set_max_bundle_bits`

```
set_max_bundle_bits <N>          # static cap, design-wide
set_max_bundle_bits auto         # dynamic cap from the shortest busterm edge
set_max_bundle_bits <N> auto     # both (the larger part count wins)
set_max_bundle_bits <N> for <prefix>    # SCOPED: cap only these bundles
set_max_bundle_bits 0 for <prefix>      # SCOPED: exempt from the global cap
set_max_bundle_bits off for <prefix>    # clear one scope
set_max_bundle_bits off                 # clear everything (global + scopes)
```

Optional bundle bit bound, applied as a **split pass** after bundling (any
strategy). A bundle over the limit is split into **balanced** parts — 600
bits at `N=512` become 300+300, never 512+88 — cutting at bus boundaries
(`<bus>_<idx>` net-name groups) whenever a bus fits whole; a single bus
larger than the target is chunked evenly.

`auto` derives a per-bundle cap physically: for each endpoint block, the bits
**incident to it** (exactly what the per-bit taper lands on its face) must
fit `floor(min(w, h) / min_bit_pitch)` — the shortest busterm edge divided by
the densest pattern layer's bit pitch (falls back to the NUTS track pitch
when no layer has a pattern). The caps are enforced **per part**: the
partitioner closes a part before any block's cap would be exceeded (a
balanced size target alone cannot bound bits that cluster on one block),
falling back to net-level packing for a bus that violates a cap on its own. Every split is reported with its binding
constraint, and the parts' reasons carry a `|SPLIT:k/n` suffix.

#### Scoped caps (`for <prefix>`)

The plain cap is **design-wide**, which makes the whole design pay for one
bundle's problem. The motivating case is a **width-doomed bus**: a bundle
whose bits cannot fit the real signal-track supply at its seat on *any*
layer — the static width-infeasibility `check_design`'s supply-doomed seat
census reports (issue #536). Splitting that one bus fixes it; splitting
every wide bus in the design also fixes it, and costs far more wire.

Measured on `flow/rnr/mix2_fast_on_aligned_sql.buda`, whose 16-bit
`top_bus20_w16` is exactly that shape:

| | ovl / unpl / viol | abstract WL | doomed TOP seats |
|---|---|---|---|
| baseline | 2 / 16 / 1 | 76368 | 1 |
| `set_max_bundle_bits 8` (global) | 1 / 0 / 0 | 121313 (**+59%**) | — |
| `set_max_bundle_bits 8 for top_bus20_w16` | 4 / 12 / 1 | 76647 (**+0.4%**) | **0** |
| …plus a trailing `refine_selection` | 2 / 12 / 1 | 75828 (**−0.7%**) | **0** |

The scoped cap removes the doomed-seat class at ~1/150th of the global
knob's wirelength bill. It does **not** reproduce the global cap's clean
`1/0/0` — that came from re-bundling the whole design, not from this bus —
so the two are different trades, not two settings of one.

A bundle matches a scope when **any** of its nets carries the prefix, and the
**longest matching prefix wins** (the `set_bundling` idiom). A scope of `0`
means *exempt*: no static cap for those bundles even when a global one is
set. `auto` has no scoped form — it is already per-bundle, derived from each
bundle's own busterm edges. Splits from a scope are reported as
`static limit N (scoped)`.

In the **hier** flow a cell-local bundle exists once per occurrence — a
template plus one replica per extra instance — and each occurrence carries
that instance's own net names (`core_i1/…` vs `core_i2/…`). Since they are
one logical bundle that must split in lockstep (each replica part links to
the corresponding template part), the scope is resolved **once for the whole
occurrence group**, against the union of every occurrence's nets: naming any
one instance's prefix governs the class. Two scopes can then be equally
specific (`for c1_bus` and `for c2_bus` both match the group), in which case
the **most restrictive** cap wins — it also satisfies the looser request —
and `0` (unbounded) wins only when it is the sole longest match.

Choosing the cap is manual, and **deliberately so**. The pool a seat gets is
a property of *where it lands*, which re-bundling changes: on
`chip3a_bottomup`, halving all nine doomed buses cleared three of them
outright (unplaced 1658 → 1511) while adding overlaps and 5% WL, because the
parts landed on different windows than the wholes did.

Automating the choice was built and measured, and **rejected** — deriving
each cap from its seat's real supply and iterating does not converge (each
pass finds new doomed seats, including on buses it already capped), the cost
outgrows the benefit (unplaced 1658 → 1386 → 1346 → 1330 while wirelength
goes +7.6% → +24.3% → +25.6%), and it degenerates to `cap 1` on a
zero-supply seat that no split can fix. Full record in
[`wishlist-bundler.md`](../internal/wishlist-bundler.md) → *"Supply-driven
bundle bit cap"*.

So: treat a scoped cap as an experiment to measure, not a fix to apply blind.

---

### `run_hier_bundler`

```
run_hier_bundler [depth <N>] [STRICT|BIDIRECTIONAL]
```

Group nets into hierarchy-aware bundles (HBundles) by querying the component hierarchy in the open BDB. Must be called after `open_bdb` and populating the database.

| Argument | Type | Description |
|---|---|---|
| `depth N` | keyword+int | Optional. Maximum hierarchy depth to traverse and group (defaults to `1`). |
| `STRICT` / `BIDIRECTIONAL` | keyword | Optional grouping strategy (default `STRICT`, matching `run_bundler`). `BIDIRECTIONAL` is direction-agnostic: it keys on the sorted set of **all** endpoint instances at the bundle depth, so a net and its reverse (and cyclic multi-receiver groups) bundle together — a single bundle may then hold both bidirectional pairs and plain one-way nets. Since a bidirectional bundle connects the **same** blocks, the direction-agnostic block-to-block trunk routes every net (sound, no warning). |

HBundles group signals that cross cell boundaries at different levels of the physical hierarchy, allowing the planner and routing engines to distinguish local intra-cell routing from top-level inter-cell interconnect.

Each net is bundled **exactly once**, at its most specific endpoint projection within `depth N`; the bundle's level is the depth of the endpoints' common ancestor (its routing context). Identical cell-local buses across instances of the same cell merge into one template bundle carrying all instance paths (expanded back per instance by `run_planner hier`).

**Example:**
```buda
run_hier_bundler depth 1
run_hier_bundler depth 1 bidirectional
```

---

### `dump_hbundles`

```
dump_hbundles [expanded] [depth N]
```

Print a one-line summary for every HBundle. Must be called after `run_hier_bundler`.

| Argument | Type | Description |
|---|---|---|
| `expanded` | keyword | Show the post-expansion per-instance wrappers in `self.bundles` (state after `run_planner hier`). Without this flag the pre-expansion snapshot captured at `run_hier_bundler` time is shown. |
| `depth N` | keyword+int | Filter output to bundles at hierarchy level N. |

**Output format per line:**

```
hb-{id}  D{level}  {kind}  "{short_reason}"  nets={n}  cands={c}  [{instances}]
```

| Field | Description |
|---|---|
| `hb-{id}` | Bundle integer ID |
| `D{level}` | Hierarchy depth of this bundle |
| `{kind}` | `cell:{cell_context}` if cell_context is set; `cross-level` if drv_spec_depth ≥ 0; else `cross-block` |
| `{short_reason}` | Abbreviated reason string (driver/receiver signature) |
| `nets={n}` | Number of nets in the bundle |
| `cands={c}` | Number of topology candidates (0 = topology not yet generated) |
| `[{instances}]` | Instance list — shown only for cell-level bundles |

By default, output reflects the **pre-expansion snapshot** (`_hier_bundles_orig`) captured at `run_hier_bundler` time — the canonical bundle IDs as the user sees them. After `run_planner hier`, adding `expanded` shows the runtime per-instance wrappers instead.

**Example output (pipeline test vehicle):**
```
hb-1  D0  cross-block  "DRV:src_i|REC:proc_i,"  nets=8  cands=7
hb-2  D0  cross-block  "DRV:proc_i|REC:snk_i,"  nets=8  cands=7
hb-3  D1  cross-block  "DRV:src_i/buf_i|REC:proc_i/pa_i,"  nets=8  cands=5
hb-4  D1  cell:proc_cell  "DRV:pa_i|REC:pb_i,"  nets=8  cands=4  [proc_i]
hb-5  D1  cell:proc_cell  "DRV:pb_i|REC:pc_i,"  nets=8  cands=4  [proc_i]
hb-6  D1  cross-block  "DRV:proc_i/pc_i|REC:snk_i,"  nets=8  cands=5
```

**Example:**
```buda
run_hier_bundler depth 1
dump_hbundles                 # show all 6 bundles
dump_hbundles depth 1         # show only depth-1 bundles
dump_hbundles expanded        # show expanded per-instance view (after run_planner hier)
```

---
