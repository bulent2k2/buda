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
def_ndr <name> [width x<N>] [spacing x<N>] [shield bus|bit|per:<N> [net <label>]] [credit] [bond] [layers <csv>]
```

Declare a rule. **Declare-once**: a duplicate name, an unknown token, or a rule
that constrains nothing is a flow-stopping error — a typo must never silently
weaken a constraint.

| Argument | Type | Description |
|---|---|---|
| `name` | string | Rule name, used by `set_ndr` and reported everywhere |
| `width x<N>` | multiplier | Wire width as a multiple of the default. **Multiplier form only** — absolute µm needs per-layer slot geometry and is a later phase |
| `spacing x<N>` | multiplier | Minimum spacing to any neighbour, as a multiple of default |
| `shield …` | mode | `bus` = flank the whole bus, `bit` = flank every bit, `per:<N>` = a shield every N bits. Optional `net <label>` names the shield net (default `GND`) |
| `credit` | flag | Opt in to **rail crediting** (R5a): an END shield may be satisfied by an immediately adjacent power rail that is electrically identical to the shield net, instead of emitting a redundant wire. Requires a shield arrangement |
| `bond` | flag | Opt in to **shield bonding** (R6): strap every EMITTED shield to the power grid with a via wherever an identity-matching rail crosses it on an adjacent perpendicular layer. Requires a shield arrangement. Output-only — it changes no demand and no placement, so it can be turned on without re-planning |
| `layers <csv>` | layer names/ids | Restrict the rule (and so its nets) to these layers. The planner honours the restriction when choosing layers |

Shield-net identity is by supply family, case-insensitively: `GND`/`VSS`/`GROUND`
are one net for shielding purposes, `VDD`/`VCC`/`POWER` another. A POWER rail
can never satisfy a GROUND spec. Crediting and the `check_design` audit share
this one predicate, so they cannot disagree.

```
def_ndr clk2x width x2 spacing x2 shield bus net GND
def_ndr sig15 width x1.5 spacing x1.5 shield bus net GND credit
def_ndr bus25 width x2.5 spacing x2.5 shield per:2 net GND layers M5,M6
def_ndr gndbus width x1.5 shield bus net GND bond
```

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
| [`flow/ndr_bond.buda`](../../flow/ndr_bond.buda) | Two bonded shield rules — one matching the grid rails by label, one through the supply family (VSS shields on GND rails) |
| [`flow/ndr_shield_flat.buda`](../../flow/ndr_shield_flat.buda) | Three rules across the multiplier range (x1.5 / x2 / x2.5) and **all three** shield arrangements + crediting, governing three bundles beside ungoverned traffic |
| [`flow/ndr_shield_hier.buda`](../../flow/ndr_shield_hier.buda) | The same three rule shapes in a **hierarchical** flow, governing a cell-level template class (one template, three lockstep replicas) and top-level buses |
| [`flow/ndr_bottom_up.buda`](../../flow/ndr_bottom_up.buda) | A governed template marked `set_bottom_up`: its interconnect is solved once and copied — **shields included** — to every instance |

Run any of them with:

```
bin/buda flow/ndr_shield_flat.buda
```
