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
  (`src/buda_cmds/bdb_cmds.py:383`) — the behavior the GUI must match. It is
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
    get:      Callable   # (state, cell) -> value
    set:      Callable   # (state, cell, value) -> None   (raises on invalid)
    eligible: Callable   # (state, cell) -> (ok: bool, reason: str)
    help:     str        # tooltip / status text

CELL_SETTINGS = [
    CellSetting(
        key="bottom_up", label="Bottom-Up", kind="bool",
        get=lambda st, c: st.bdb.cell_bottom_up(c),
        set=_set_bottom_up_checked,     # congruence-gated, then bdb.set_cell_bottom_up
        eligible=_bottom_up_eligible,   # (False, "instances not congruent: …")
        help="Plan/NUTS this cell's local interconnect once, copy to every instance.",
    ),
]
```

Adding a future per-cell config = **append one descriptor** (+ its
get/set/eligible). The dialog builds one column per descriptor, `kind`
selecting the widget (`bool` → `Checkbutton`, `choice` → `Combobox`, `float`
→ `Spinbox`). This is deliberately more declarative than Optimize (whose
sections are hand-built) — the whole point of the request.

## Command layer (`tools/floorplanner_commands.py`) — GUI-free, testable

- `list_cell_settings(state) -> list[CellSettingsRow]` — one row per cell type
  (from `state.bdb.all_cells()`), each carrying the cell name, instance count
  (`count_cell_instances`), and per-key `{key: (value, eligible_ok, reason)}`.
  Eligibility is computed once from a single `all_components()` read.
- `set_cell_setting(state, cell, key, value)` — read-only guard (raise
  `PermissionError`, exactly as `write_bdb`), then dispatch to the descriptor's
  `set`, which persists straight to `state.bdb`.
- **Shared validation** — lift `_bottom_up_congruence_issues` into a free
  function (e.g. `bottom_up_congruence_issues(comps, cell)` in a shared module)
  that both `src/buda_session/hier.py` and `floorplanner_commands.py` import, so
  the GUI and CLI can never diverge on what "congruent" means.
- **Eligibility gates *enabling*, not *clearing*.** Mirror the CLI exactly: the
  congruence check runs only on the ON transition (`set_bottom_up … off` always
  clears). So `_set_bottom_up_checked(state, cell, value)` validates congruence
  **only when `value` is truthy**; setting it OFF is unconditional. This
  matters for the real failure mode Codex flagged: a cell marked `bottom_up`
  that *later* becomes incongruent (a rotate/move in the same session, or a
  stale BDB opened from disk) must always be clearable from the GUI — never
  stranded. Generalizing to non-`bool` `kind`s: `eligible` bounds the
  value-*increasing* / non-default direction; returning a setting to its
  default is always permitted.

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
  `value == default and not eligible`; otherwise actionable. Apply/Cancel; Apply
  calls `fpc.set_cell_setting` per changed row and reports a one-line summary of
  what changed / what was refused.
- **Button** — a "Cell Settings…" button next to Optimize (`self._opt_btn` in
  the Blocks pane), stored as `self._cellcfg_btn` and added to the
  `_apply_ro_state` disable list so read-only sessions grey it out (parity with
  Write / Optimize / Run Flow).

### Complementary quick-access (optional, same plumbing)

Because selecting a component already surfaces its cell in the **Selection**
panel (`_update_selection_label`, line 1498, with the ⚠ "Shared: … (×N)" line +
Make Unique button), drop a small **"Bottom-Up" checkbox there** for the
selected cell — one-cell quick access — reusing the same
`fpc.set_cell_setting` + eligibility gate. The dialog stays the extensible,
all-cells surface.

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
