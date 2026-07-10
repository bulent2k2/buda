# Floorplanner per-cell configuration UI — design proposal

Status: **proposed** (not yet implemented). Tracks the
[`opens.md`](opens.md) quick-win *"Floorplanner UI toggle for
`cell.bottom_up`"*: the flag is persisted (schema v17) and the engine/BDB
sides are done, but the GUI neither displays nor edits it.

This proposes not a one-off checkbox but a small **extensible per-cell
settings surface**, so the next per-cell configuration (route priority,
keepout margin, layer preference, orientation-lock, …) drops in as a single
descriptor rather than a new dialog. `cell.bottom_up` is the first — and
currently only — entry.

## Background: what exists

- **BDB API (ready, bound to Python)** — `set_cell_bottom_up(cell, on)`
  (throws if the cell is undefined), `cell_bottom_up(cell)` (false for
  unknown), `bottom_up_cells()` (sorted), and `CellRow.bottom_up`
  (`all_cells()` returns it). See `src/bdb.h` / `src/bind_db.cpp`.
- **CLI command** `set_bottom_up <cell> [on|off]`
  (`src/buda_cmds/bdb_cmds.py:380`) — the behavior the GUI must match. It is
  **not** a blind flag: turning it ON first runs a **congruence check** and
  refuses non-uniform cells.
- **The congruence check** — `_bottom_up_congruence_issues(cell, comps)`
  (`src/buda_session/hier.py:351`). Bottom-up copies are translation-only, so
  every instance of the cell must be a *pure translated copy* of the first:
  identity orientation `N`, equal outline dimensions, and an identical full
  subtree (each descendant matched by path suffix on cell type, orient, and
  bbox relative to the instance origin). It is a **pure function of
  `all_components()`** — no other session state — so it can be shared verbatim
  with the Floorplanner.
- **The Optimize dialog** (`tools/bdb_floorplanner.py`, opener
  `_open_optimize_dialog` at line 681, class `_OptimizeDialog` at line 1933)
  — the structural model: a modal `Toplevel` + `grab_set()`, `LabelFrame`
  sections, a **scroll-canvas table with parallel per-key var dicts** for
  per-block rows (the "Block Constraints" table, lines 2017–2088), a persisted
  settings dict, and a button gated by `_apply_ro_state` (line 162).
- **Cell selection today** — there is **no** first-class "selected cell type";
  selection is by component instance path (`state.selected`), and the cell is
  derived via `fpc.get_block_cell` / `fpc.count_cell_instances`
  (`tools/floorplanner_commands.py:974` / `984`). Per-cell writes already exist
  in `fpc` (`sync_cell_to_instances`, `make_block_unique`).

## The extension point: a declarative `CellSetting` registry

A registry of per-cell setting descriptors (new `tools/cell_settings.py`, or a
section of `floorplanner_commands.py`). Each descriptor is everything the
dialog, the command layer, and (optionally) a generic CLI need to render,
read, validate, and write one property:

```python
@dataclass
class CellSetting:
    key:      str       # logical/BDB key, e.g. "bottom_up"
    label:    str       # column header, e.g. "Bottom-Up"
    kind:     str       # "bool" now; "choice" | "float" later
    default:  object     # value that is "unset" (False for bottom_up)
    get:      Callable   # (state, cell) -> value
    set:      Callable   # (state, cell, value) -> None   (raises on invalid)
    # Gate on the TRANSITION, not a direction heuristic: (ok, reason) for
    # moving `cell` from `old` to `new`. Each descriptor owns its own rule,
    # so the clearing guarantee is a property of the descriptor, not a
    # registry-wide "non-default direction" convention that future float/
    # choice kinds would need exceptions to (owner review, #248 P3).
    eligible: Callable   # (state, cell, old, new) -> (ok: bool, reason: str)
    help:     str        # tooltip / status text

CELL_SETTINGS = [
    CellSetting(
        key="bottom_up", label="Bottom-Up", kind="bool", default=False,
        get=lambda st, c: st.bdb.cell_bottom_up(c),
        set=_set_bottom_up_checked,     # congruence-gated, then bdb.set_cell_bottom_up
        # Only the ON transition is gated; `off` clears unconditionally.
        eligible=lambda st, c, old, new: (
            (True, "") if not new else _bottom_up_congruent(st, c)),
        help="Plan/NUTS this cell's local interconnect once, copy to every instance.",
    ),
]
```

Adding a future per-cell config = **append one descriptor** (+ its
get/set/eligible). The dialog builds one column per descriptor, `kind`
selecting the widget (`bool` → `Checkbutton`, `choice` → `Combobox`, `float`
→ `Spinbox`). This is deliberately more declarative than Optimize (whose
sections are hand-built) — the whole point of the request.

The transition-based `eligible(state, cell, old, new)` is what keeps the
"clearing is always allowed" guarantee (Codex P2) descriptor-local instead of
a registry rule: `bool` gates only `new is True`; a future `float` keepout
margin or `choice` layer preference each define exactly which of *their*
transitions require validation, with no ambiguous "increasing / non-default"
heuristic to except.

## Command layer (`tools/floorplanner_commands.py`) — GUI-free, testable

- `list_cell_settings(state) -> list[CellSettingsRow]` — one row per cell type
  (from `state.bdb.all_cells()`), each carrying the cell name, instance count
  (`count_cell_instances`), and per-key `{key: (value, can_activate, reason)}`,
  where `can_activate = eligible(state, cell, value, <activating value>)` (for
  `bool`, the activating value is `True`). Computed once from a single
  `all_components()` read per call, so a large BDB pays the subtree scan once
  when the dialog opens, not per widget.
- `set_cell_setting(state, cell, key, value)` — read-only guard (raise
  `PermissionError`, exactly as `write_bdb`), then dispatch to the descriptor's
  `set`, which persists straight to `state.bdb`.
- **Shared validation, named module.** Lift `_bottom_up_congruence_issues` into
  a free function `bottom_up_congruence_issues(comps, cell)` in
  **`src/buda_session/util.py`** (already the mixin-shared-helpers home), and
  have `hier.py`'s method and `floorplanner_commands.py`'s
  `_bottom_up_congruent` both call it, so the GUI and CLI can never diverge on
  what "congruent" means. The import works from both sides today:
  `bdb_floorplanner.py` puts `tools/` and `src/` on `sys.path`, and the fpc
  tests run under pytest's `pythonpath = build src` (owner review confirmed).
- **Eligibility gates *enabling*, not *clearing*.** Mirror the CLI exactly: the
  congruence check runs only on the ON transition (`set_bottom_up … off` always
  clears). So `_set_bottom_up_checked(state, cell, value)` validates congruence
  **only when `value` is truthy**; setting it OFF is unconditional. This
  matters for the real failure mode Codex flagged: a cell marked `bottom_up`
  that *later* becomes incongruent (a rotate/move in the same session, or a
  stale BDB opened from disk) must always be clearable from the GUI — never
  stranded. The transition-based `eligible` above makes this each descriptor's
  own rule, not a registry-wide direction heuristic.
- **GUI eligibility is UX, not the safety net.** `run_planner hier` re-checks
  congruence at expansion time (`docs/internal/hier_bottom_up_planning.md`), so
  a bottom-up flag that slipped through (e.g. a BDB hand-edited outside the GUI)
  is still caught before it can mis-route — the dialog's gate just prevents the
  user from *creating* that state, it isn't the last line of defense.

Persistence rides the existing model: the flag is a direct `state.bdb` write
(independent of the engine's in-memory placement). A binary BDB persists
immediately; a `*.bdb.sql` session lands it in the temp binary and
**Write / Save As** serializes it back (the write-back path shipped in the
Floorplanner-save work).

## The dialog (`tools/bdb_floorplanner.py`) — mirrors Optimize

- **Opener** `_open_cell_settings_dialog(self)` — guard on `self.state`,
  construct `_CellSettingsDialog(self.root, self.state)`, `wait_window`, then
  refresh tree/status.
- **`_CellSettingsDialog`** — modal `Toplevel` + `grab_set()`; a scroll-canvas
  table (the Block-Constraints pattern) with a header built from
  `CELL_SETTINGS` labels plus "Cell" and "Instances" columns, one row per cell
  type. For a `bool` setting → a `Checkbutton`. **Eligibility disables only the
  *enable* direction, never *clearing*** (Codex #248 P2): a cell that is OFF and
  ineligible shows a disabled checkbox with the reason inline (can't turn on);
  but a cell that is already ON and has *become* ineligible (a rotate/move this
  session, or a stale BDB) keeps its checkbox **enabled so it can be unchecked**
  — with a warning marker (e.g. "⚠ incongruent — clear only") — because the CLI
  lets `off` clear unconditionally and the GUI must not strand the user in a
  state only the CLI/DB edit can undo. So the render rule is: disabled iff
  `value == default and not can_activate` (the `can_activate` bit
  `list_cell_settings` already computed); otherwise actionable. Apply/Cancel;
  Apply calls `fpc.set_cell_setting` per changed row and reports a one-line
  summary of what changed / what was refused.
- **Button** — a "Cell Settings…" button next to Optimize (`self._opt_btn` in
  the Blocks pane), stored as `self._cellcfg_btn` and added to the
  `_apply_ro_state` disable list so read-only sessions grey it out (parity with
  Write / Optimize / Run Flow).

### Complementary quick-access (optional, same plumbing)

Because selecting a component already surfaces its cell in the **Selection**
panel (`_update_selection_label`, line 1498, with the ⚠ "Shared: … (×N)" line +
Make Unique button), drop a small **"Bottom-Up" checkbox there** for the
selected cell — one-cell quick access — reusing the same
`fpc.set_cell_setting`. The dialog stays the extensible, all-cells surface.

**Don't pay the congruence scan on every selection** (owner review, #248 P3):
`_bottom_up_congruence_issues` is a full `all_components()` walk + subtree
compare, and the Selection panel refreshes on every click, so computing
eligibility there would add visible click-selection lag on a large BDB. Render
the checkbox state immediately from the cheap `cell_bottom_up()`, and defer the
congruence check to the click handler (where the CLI also pays it) — or cache
per-cell eligibility and invalidate it on a placement mutation. The dialog path
has no such cost: it computes eligibility once on open, and its modal `grab_set`
prevents mid-dialog placement edits (the out-of-scope note below covers live
re-validation).

## Tests (`test/tests/test_floorplanner_cell_settings.py`)

- `list_cell_settings` returns the correct value + eligibility for a
  **congruent** cell (two translated instances) and an **ineligible** one (a
  rotated/mirrored or non-uniform instance) with a human-readable reason.
- `set_cell_setting(state, cell, "bottom_up", True)` sets a congruent cell
  (`bdb.cell_bottom_up` becomes true) and **refuses** a non-congruent cell (the
  flag stays off, an issue is surfaced).
- **Clearing is unconditional** (Codex #248 P2): mark a congruent cell ON, make
  it incongruent (rotate/move an instance), then `set_cell_setting(state, cell,
  "bottom_up", False)` still clears it — never gated by congruence.
- Read-only session → `set_cell_setting` raises `PermissionError`.
- Extensibility smoke: a second dummy `CellSetting` renders/round-trips through
  `list_cell_settings` / `set_cell_setting` without touching the dialog code.

The command-layer functions carry the logic and the tests; the dialog stays a
thin, schema-driven view (headless Tk is not unit-tested, matching the existing
Optimize coverage boundary).

## Out of scope / future

- Additional per-cell descriptors (route priority, keepout margin, layer
  preference, orientation-lock) — each a new `CellSetting`.
- A generic CLI `set_cell_config <cell> <key> <value>` driven by the same
  registry, so scripts and the GUI share one vocabulary.
- Live re-validation of eligibility as placement is edited in the same session
  (the dialog computes eligibility on open; re-opening re-checks).
