# BUDA Script Reference — Non-default rules (NDR)

Per-net width / spacing / shielding constraints: `def_ndr`, `set_ndr`, `dump_ndr`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

Audience notes: the architect-facing contract is [NDR Requirements](../NDR_REQUIREMENTS.md), the methodology-facing options are [NDR UI](../NDR_UI.md), and the implementation is [internal/ndr_architecture.md](../internal/ndr_architecture.md).

---

## What an NDR is

By default every bit-wire is one signal track wide with default spacing. An
**NDR** is a named rule that widens a net's wire, reserves extra clearance
around it, and/or flanks it with shield wires — the treatment a clock
or a sensitive analog bus needs. Rules are declared once, attached to nets by
name prefix, and then govern the whole pipeline: the planner **prices** the
extra demand where it chooses layers, abstract NUTS reserves the footprint,
detailed NUTS places the wide bits and emits the shields, `check_design`
audits the result, and the rules persist in the BDB.

Declare rules **before** `run_bundler` / `run_hier_bundler`: attachment
happens at bundling, because a bundle that mixes rules is split into
rule-uniform parts (see [Rule-class split](#rule-class-split)).

### The demand model (why a governed bus costs what it costs)

Everything is counted in **SIGNAL slots**, at the GROUP level, by one shared
conversion — so no two stages can disagree about whether a bus fits:

| Rule field | Slots |
|---|---|
| `width xN` | `ceil(N)` slots per bit (a x1.5 wire pays 2 — conservative, never illegal) |
| `spacing xN` | `ceil(N) − 1` empty **guard** slots on each unshielded gap, including the run's two ends |
| `shield bus` | one shield wire at each END of the run (2 total) |
| `shield bit` | a shield in EVERY gap, ends included |
| `shield per:N` | a shield after every N bits, plus both ends |

A shielded gap carries the shield **instead of** guards — the shield wire both
occupies the slot and satisfies the clearance intent.

> **An emitted shield is labeled metal until you bond it.** A shield wire BUDA
> emits is a first-class routed object carrying the rule's shield-net identity,
> and it reserves the track — but by default nothing connects it to the power
> grid, so it does not provide electrical shielding on its own. Three ways out,
> cheapest first: `credit`, so an **existing** rail — which *is* grid metal —
> serves as the shield; `bond`, so BUDA straps each emitted shield to the grid
> at every crossing; or add the straps downstream yourself. Bonding covers the
> emitted wires only — the remaining limits are in
> [`internal/opens_ndr.md`](../internal/opens_ndr.md) §1.

Group-level means shared resources are counted once: an 8-bit flanked bus pays
**2** end shields, while two 4-bit flanked buses pay **4**. `dump_ndr` prints
each governed bundle's total demand and its slot-by-slot layout.

---

## `def_ndr`

```
def_ndr <name> [width x<N>|<dist>] [spacing x<N>|<dist>] [shield bus|bit|per:<N> [net <label>]] [credit] [bond [stride <N>]] [layers <csv>]
```

Declare a rule. **Declare-once**: a duplicate name, an unknown token, or a rule
that constrains nothing is a flow-stopping error — a typo must never silently
weaken a constraint.

| Argument | Type | Description |
|---|---|---|
| `name` | string | Rule name, used by `set_ndr` and reported everywhere |
| `width x<N>` | multiplier | Wire width as a multiple of the default. **Pattern-independent**: `x2` is two signal slots on every layer, so one rule ports across a stack whose layers have different pitches |
| `width <dist>` | distance | Wire width as an ABSOLUTE distance — bare = layout units, `um` = microns converted through the declared import scale. Resolves **per layer**: how many slots one physical width costs depends on the layer, which is why R1 asks for the form. See [Absolute values](#absolute-values) |
| `spacing x<N>` \| `<dist>` | multiplier or distance | Minimum spacing to any neighbour, in either form, quantized the same way |
| `shield …` | mode | `bus` = flank the whole bus, `bit` = flank every bit, `per:<N>` = a shield every N bits. Optional `net <label>` names the shield net (default `GND`) |
| `credit` | flag | Opt in to **rail crediting** (R5a): an END shield may be satisfied by an immediately adjacent power rail that is electrically identical to the shield net, instead of emitting a redundant wire. Requires a shield arrangement |
| `bond [stride <N>]` | flag + count | Opt in to **shield bonding** (R6): strap every EMITTED shield to the power grid with a via where an identity-matching rail crosses it on an adjacent perpendicular layer. `stride <N>` straps every Nth crossing instead of all of them (default 1 = every crossing); both **extremes are always anchored**, so no tail hangs unbonded off the last strap. Requires a shield arrangement. Output-only — it changes no demand and no placement, so it can be turned on, off, or re-strided without re-planning |
| `layers <csv>` | layer names/ids | Restrict the rule (and so its nets) to these layers. The planner honours the restriction when choosing layers — see [Restricting layers](#restricting-layers) for what happens when the restriction cannot be satisfied |

Shield-net identity is by supply family, case-insensitively: `GND`/`VSS`/`GROUND`
are one net for shielding purposes, `VDD`/`VCC`/`POWER` another. A POWER rail
can never satisfy a GROUND spec. Crediting and the `check_design` audit share
this one predicate, so they cannot disagree.

### Restricting layers

`layers <csv>` is **not** validated at declaration, deliberately. A rule
naming only vertical layers is legitimate — a bus that routes purely
vertically needs nothing else — and whether that is satisfiable depends on
where the endpoints sit, which the declaration cannot know. (This is the one
place it differs from `set_cell_layer_cap`, which *does* hard-error on a band
with no H or no V layer: a cell's band governs all of that cell's
interconnect, which needs both directions.)

The check therefore lives where the direction is known. If a governed
bundle's candidates need a segment in a direction the restriction supplies no
layer for, there is no legal assignment in any planner mode, and the planner
says so by name:

```
[Planner] WARNING: Bundle 3: NOTHING committed — this bundle gets no layer
assignment and no route. Its candidates need a HORIZONTAL segment, but its
allowed layers {M4,M6} contain no HORIZONTAL layer — ...
```

`check_design` then reports the bundle as `UNROUTED_BUNDLE`, so a bus with no
wire can never read as a clean design. Vehicle:
[`flow/ndr_layer_mask_starved.buda`](../../flow/ndr_layer_mask_starved.buda),
which routes the same restriction successfully on an aligned pair alongside
the failing one.

```
def_ndr clk2x width x2 spacing x2 shield bus net GND
def_ndr sig15 width x1.5 spacing x1.5 shield bus net GND credit
def_ndr bus25 width x2.5 spacing x2.5 shield per:2 net GND layers M5,M6
def_ndr gndbus width x1.5 shield bus net GND bond
def_ndr vssbit width x2 shield bit net VSS bond stride 3
```

### Absolute values

An absolute width or spacing names one **physical** width; how many SIGNAL
slots that costs is a property of the layer. So one declaration resolves to
different slot counts per layer — the thing a multiplier cannot express.

```
def_ndr fine3 width 3          # layout units
def_ndr thin  width 0.2um      # microns, via the declared import scale
```

The divisor is the layer's per-signal-slot **channel cost**
(`unit_pitch / n_signal_slots`), which amortizes the power rails across the
signal slots — the same quantity `eff_bus_width` charges, so a rule and the
width model agree by construction.

That makes an absolute value a statement about **how much routing channel the
width consumes**, which is what the planner books — not a promise about how
much metal the bit gets. A k-slot bit's metal is `k·w + (k−1)·sp` over the
layer's own slots, and the two quantities coincide only for some combinations
of pattern and declared value. On a period that is mostly power rail the gap
is large: `width 8` can resolve to a single slot and place a 2-unit wire.

After `run_detailed_nuts`, `dump_ndr` reports the delivered metal beside the
declared width wherever they differ:

```
[NDR]   on layer 6: rule 'w8' declares width 8 but the placed bits are 2 wide
        — the value was quantized by the layer's per-signal-slot CHANNEL cost,
        which is not its metal (opens_ndr.md §2)
```

`check_design` stays clean in that case, and correctly so: its `NDR_WIDTH`
check counts covered SIGNAL slot centres, the same channel-shaped quantity the
quantization used. **A silent run is not evidence the two agree** — it can
equally mean the declared value happened to land on a slot boundary. If you
are declaring a width for EM or resistance rather than for congestion, read
the `dump_ndr` lines and see
[opens_ndr.md §2](../internal/opens_ndr.md), whose repro
(`flow/ndr_abs_divisor.buda`) shows all four cases including the exact one.

**Rounding is UP.** A value between slot counts pays the larger, the
convention the multiplier form already uses (`x1.5` pays 2 slots —
conservative, never illegal). A value landing exactly on a slot boundary pays
exactly that, not one more.

**Ordering matters, and it is enforced.** An absolute value is meaningless
without the slot geometry it quantizes against, so **every layer the rule can
reach must already have a `def_track_pattern`** — the rule's `layers`
restriction if it has one, otherwise every declared layer. Declaring one
earlier is a hard error naming the unpatterned layers and the three ways out
(declare the patterns first, restrict with `layers <csv>`, or use the
multiplier form, which is order-independent).

Requiring *every* governed layer rather than merely one is deliberate: the
charged quantization is a **maximum** over the governed layers, and a maximum
taken over a subset is not conservative — a pattern declared later on an
omitted layer could need more slots, and routing there would under-charge the
declared width.

**Routing charges the layer it lands on.** The maximum above is the
declaration-time summary; once a segment has a layer, the planner, abstract
NUTS and detailed NUTS all price the rule as it resolves *there* — so a bus
crossing a coarse layer pays what that layer's geometry actually asks, not
what its tightest governed layer would. A layer whose single slot already
covers the declared width charges the rule as an ordinary bus (nothing to
add), which for width and spacing is exactly right; a **shielded** rule stays
governed at any pitch, since the shields are geometry the layer cannot supply
on its own.

`dump_ndr` and the declaration line report the declared value, the
conservative quantization the rule is summarized at, and the per-layer spread,
since an absolute rule has no single slot count. After planning, a governed
bundle also reports every layer whose real charge differs from that maximum:

```
[NDR] rule 'fine3': width 3, spacing x1 (ABSOLUTE, layout units) -> 2 slot(s)/bit
      + 0 guard(s)/gap charged (the max over governed layers;
      per layer slots/guards L3:2/0, L4:1/0, L5:2/0, L6:1/0)
[NDR] bundle 2 ('vert__0' x4) rule 'fine3': demand 8 slot(s) (layout BbBbBbBb)
[NDR]   on layer 6: demand 4 slot(s) — the absolute width resolves to
        1 slot(s)/bit at that layer's pitch
```

Vehicle: [`flow/ndr_abs_um.buda`](../../flow/ndr_abs_um.buda) — its `vert_`
bus routes vertically on purpose, so a governed segment lands on the coarse
pair and the per-layer difference is visible in the routed result.

### Realizability is checked LOUDLY

A **width** rule (`width_slots > 1`) needing more physically contiguous SIGNAL
slots per bit than any run in a governed layer's track pattern can offer is a
**hard error** at `run_detailed_nuts` (the first point where the grid is
known), naming the layer, the rule, and the arithmetic. A rule explicitly
restricted to a layer with no `def_track_pattern` errors up front. BUDA never
silently degrades an NDR to the default rule.

Shield-only and spacing-only rules are **not** width-checked this way — there
is no per-bit contiguity to prove. If their run does not fit the available
tracks at placement time, DNUTS reports it as a warning naming the rule and
the demand, and strands the bus's bits (all-or-nothing admission), which
`check_design` then counts as unplaced.

---

## `set_ndr`

```
set_ndr <prefix>|* <rule>|off
```

Attach a rule to every net whose name starts with `prefix`. **Longest prefix
wins**; `*` is the global default; `off` clears one scope. Mirrors
`set_bundling`'s resolution.

```
set_ndr *        default2x     # every net, unless a longer prefix says otherwise
set_ndr clk_     clk2x
set_ndr clk_fast_ clk4x        # wins over clk_ for clk_fast_* nets
set_ndr clk_     off           # clk_* falls back to the global default
```

### Rule-class split

A bundle must be rule-uniform, so after attachment the bundler splits any
bundle whose nets resolve to different rules into rule-uniform parts and says
so:

```
[NDR] split bundle 1 (12 bits) into 2 rule-uniform part(s) (4+8): rules [sig15, None]
```

**Hierarchical flows** split the same way, but on the TEMPLATE before
per-instance expansion, with a cell-local template's replicas split in
lockstep — so the split reaches every occurrence identically. A scope that
governs one occurrence of a cell class differently from another is a hard
error: a template is solved once and copied, so every occurrence must resolve
to the same rules on the same bits.

---

## `dump_ndr`

```
dump_ndr
```

Print the declared rules with their quantization, the attachment scopes, and —
once bundles exist — each governed bundle's demand and slot layout:

```
[NDR] rule 'clk2x': width x2 (2 slot(s)/bit), spacing x2 (1 guard(s)/gap), shield bus net GND, layers any
[NDR] scope 'clk_' -> 'clk2x'
[NDR] bundle 2 ('clk_0' x4) rule 'clk2x': demand 13 slot(s) (layout SBbGBbGBbGBbS)
```

Layout letters: `B` first slot of a bit, `b` a continuation slot of a wide bit,
`S` a shield wire, `G` a reserved (kept empty) guard slot.

---

## What the rest of the flow does with a rule

| Stage | Behaviour |
|---|---|
| `run_planner` | Charges the group demand in its band capacity model, so a region that cannot afford the bus in **aggregate** overflows at planning time. This is capacity pricing, **not** a proof that a seat exists — actual occupancy can still leave no wide-enough unreserved run, which strands at DNUTS. A rule's `layers` restriction constrains layer choice. The planner always prices the **uncredited** worst case — whether a seat can credit a rail is not knowable until placement |
| `run_nuts` | Reserves the NDR footprint, so bus-level packing and bit-level reality agree |
| `run_detailed_nuts` | Places each bit on its `width_slots` contiguous slots, keeps guard slots empty, and emits shield wires as first-class routed objects. With `credit`, an end shield adjacent to a matching rail that spans the run is credited instead of emitted. With `bond`, every emitted shield is then strapped to the grid and the strap count is reported |
| `check_design` | Adds `NDR_WIDTH` (a governed bit narrower than its rule), `NDR_SPACING` (foreign metal inside the reserved run — including CLOCK/CUSTOM pre-route rails), and `NDR_SHIELD` (placed shield count wrong, or — on a run with no culled bits — the shields not in the gaps the rule declares, checked role-by-role against the credited layout for every shield mode). With `bond`, also `NDR_BOND` — an emitted shield with no strap, i.e. no identity-matching rail crosses it on an adjacent perpendicular layer, so it is floating metal. That is a grid problem, not a routing one |
| `report_wirelength` | Reports shield metal on its own line — it is real metal the design pays for, but not signal wirelength, so quality metrics stay comparable across designs with and without NDRs |
| `visualize_topologies` | A governed bundle's header carries an NDR badge (rule + demand); the shield ghost overlay shows where the run's shields will sit; `debug` flags a candidate whose windows cannot host the demand |
| BDB | Rules and scopes persist (`ndr_rule` / `ndr_scope`) and restore on `open_bdb`; `load_pipeline` VOIDs a restored plan LOUDLY when its governing rule changed since the checkpoint |

A design that declares no rule routes **byte-identically** to one built before
NDR existed — the guarantee is corpus-verified at every landing.

---

## Worked vehicles

Runnable end-to-end examples, smallest first:

| Flow | What it shows |
|---|---|
| [`flow/ndr_demo.buda`](../../flow/ndr_demo.buda) | The minimum: one shielded double-width clock bus among default buses. All three constraint kinds on a small flat design |
| [`flow/ndr_bond.buda`](../../flow/ndr_bond.buda) | Two bonded shield rules — one matching the grid rails by label and strapping every crossing, one matching through the supply family (VSS shields on GND rails) at `stride 3` |
| [`flow/ndr_shield_flat.buda`](../../flow/ndr_shield_flat.buda) | Three rules across the multiplier range (x1.5 / x2 / x2.5) and **all three** shield arrangements + crediting, governing three bundles beside ungoverned traffic |
| [`flow/ndr_shield_hier.buda`](../../flow/ndr_shield_hier.buda) | The same three rule shapes in a **hierarchical** flow, governing a cell-level template class (one template, three lockstep replicas) and top-level buses |
| [`flow/ndr_bottom_up.buda`](../../flow/ndr_bottom_up.buda) | A governed template marked `set_bottom_up`: its interconnect is solved once and copied — **shields included** — to every instance |

Run any of them with:

```
bin/buda flow/ndr_shield_flat.buda
```
