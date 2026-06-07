# BDB Hierarchical Occurrence Semantics — Design Options

## Context

A BDB *cell* is a reusable building block with a canonical size (width × height).
A BDB *component* is one physical occurrence of a cell placed in the layout.

The original `add_inst` command places exactly one occurrence and expects callers
to enumerate every descendant explicitly.  As designs grow, this becomes verbose:
a three-tier hierarchy with 4 × 4 × 2 = 32 leaves requires 52 `add_inst` calls.

The proposed `add_inst_to_cell` command lets authors describe the *structural*
contents of a cell once.  When a cell is instantiated at the top level, every
descendant occurrence is created automatically — the same "cell occurrence"
model used by standard LEF/DEF, Verilog netlists, and GDS hierarchy.

---

## Option A — Eager expansion at root placement *(implemented)*

**New table:** `cell_children(parent_cell, inst_name, child_cell, x, y)`  
stores the structural definition of each cell's contents.

**`add_inst_to_cell parent_cell inst_name child_cell x y`**  
writes one row to `cell_children`.  No component rows are created yet.

**`add_inst inst_name cell_name parent x y`**  
works as before (creates the component, resolves absolute coords from parent),
then **immediately** walks `cell_children[cell_name]` recursively and writes
all descendant component rows with correct absolute coordinates, depths, and
`parent_id` links.  Parents are marked `is_leaf=0` automatically.

**Idempotency:** descendant INSERTs use `INSERT OR IGNORE`, so re-running a
script that calls `add_inst` on the same name is safe (the root INSERT will
throw on duplicate; descendants are silently skipped if they already exist).

**Pros**
- Zero changes to downstream consumers (`all_components`, `add_blocks_from_bdb`, etc.)
- After any `add_inst` call the `component` table is always fully consistent
- Simple mental model: "place this cell and its whole subtree appears"

**Cons**
- N top-level placements of the same cell produce N × |subtree| component rows
  (no structural sharing — acceptable at typical EDA design scale)
- Adding `add_inst_to_cell` after the root is placed does NOT retroactively
  expand already-placed roots; callers must place roots after all cell structure
  is defined

---

## Option B — Lazy / virtual expansion

`cell_children` stores the structure as in Option A, but the `component` table
holds **only root-level placements**.  Methods like `all_components()` and
`comps_in_rect()` expand the hierarchy on the fly by joining `cell` and
`cell_children`.

**Pros**
- DB stays compact regardless of reuse depth or fanout
- One cell definition serves all N occurrences with no redundant rows

**Cons**
- Every consumer must be rewritten to handle virtual (non-stored) rows
- The `parent_id` integer FK model breaks; path-based identity is needed
- Query complexity increases significantly; harder to expose to Python cleanly

---

## Option C — Explicit `flatten_bdb` step

`add_inst_to_cell` writes to `cell_children`.  Root `add_inst` writes only the
root component row.  A separate command `flatten_bdb` (or an automatic call
before `add_blocks_from_bdb`) walks the full cell tree and populates all
descendant component rows in a single pass.

**Pros**
- Clean separation: define structure, then materialise
- Easy to re-flatten after `resize_cell` or structural edits
- Downstream consumers unchanged

**Cons**
- DB state is only valid post-`flatten_bdb`; calling `all_components` before
  flattening returns an incomplete view
- Scripts must remember to call `flatten_bdb` in the right place

---

## Decision

**Option A** was chosen.  It keeps the simplest invariant — the `component`
table is always up-to-date — and the storage overhead is negligible for the
design sizes this tool targets.  The only ordering constraint is that all
`add_inst_to_cell` calls for a cell must precede `add_inst` calls that place
that cell.
