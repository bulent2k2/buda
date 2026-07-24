# Super-candidate grouping & group-pin

With `no_hanan_loci` **off** (the default), trunk loci are sampled on every
in-bbox Hanan line, so a single bundle's candidate pool contains many
**near-identical** candidates that differ only in the *nominal perp* of their
trunk within a **shared slide window** — b44's `TRUNK_H@y10830 / y11330 /
y11830`, all on trunk slide window `[10830,11830]`. They route within NUTS
realization-noise of each other: the nominal is a placement *hint*, not a real
degree of freedom.

This makes the candidate list the user inspects (`dump_topologies`, the topology
explorer) larger than the number of *distinct choices*. b44's one bundle:
**34–36 candidates, ~19 distinct families.**

## Two existing tools, and the gap this fills

- **`set_dedup_loci`** already *groups* these (`_topo_loci_canon`, `edit.py`) —
  but it **drops** all but the lowest-WL member. It is opt-in and documented
  **lossy**: NUTS anchors realization on each candidate's nominal, so dropping a
  member the planner might have picked changes the route. It is *not*
  byte-identical.
- **`set_prune_dominated`** drops WL-dominated candidates — a different, also
  lossy, reduction.

The gap: a **byte-identical** way to shrink what the user inspects *without*
removing any candidate or changing planner behavior. That is this feature.

## Design — group for display, restrict for planning (never drop)

Two layers, both byte-identical because the engine candidate list is untouched:

### A. `_loci_groups` + `dump_topologies --grouped` (presentation)
`_loci_groups(w, fp)` (`edit.py`) partitions a bundle's candidates into
nominal-locus **families** using the *same* key as `set_dedup_loci`
(`_topo_loci_canon`), but **keeps every candidate** — each family is a list of
indices, representative (lowest-WL) first. A candidate the canon can't fully
capture (TEG bridge, fan-in taper, dogleg jog, perp clamp, USER) is its own
singleton, exactly as dedup treats it.

`dump_topologies --grouped` prints one representative row per family, annotated
`family:+K@lo..hi` (K other variants spanning trunk perp `lo..hi`) with a
`cands=N → M families` header. Pure display — no candidate is dropped, no engine
state changes, so a non-`--grouped` dump and the whole pipeline are
byte-identical.

### B. `select_topology <b> group:<N>` (planner constraint)
Pins the **family containing candidate N** as a *super-candidate*: sets
`BundleInput::pinned_group` (the member indices) and clears the single
`topology_pinned`. The planner's candidate-selection loop
(`congestion_planner.cpp`, the `ci_lo/ci_hi` sweep) iterates the group's members
when `pinned_group` is non-empty, so it **refines which member wins** (perp /
skeleton) by the normal WL + congestion score, instead of the user hand-picking
one nominal.

- **Empty `pinned_group` (default) reproduces the historical `[ci_lo, ci_hi)`
  range exactly** — single-pin → one index, unpinned → full sweep — so planning
  is byte-identical when the feature is unused.
- **A group-pin of the natural-winner family is byte-identical to the unpinned
  plan**: restricting to a set that contains the global best, then scoring by the
  same cost, re-selects that same best member (test:
  `test_group_pin_of_natural_family_is_byte_identical`).
- A later single pin clears the group pin; `unpin_topology` clears both. The
  refine pass honors the group automatically (it re-plans through the same
  selection loop); ripup and BDB persistence of a group pin, and grouping in the
  interactive explorer, are follow-ups (a group pin is a `run_planner`-time,
  in-session constraint today).

## Why this is the byte-identical answer (and dedup is not)

`set_dedup_loci` removes members, so the planner can no longer reach a dropped
member and NUTS's nominal-anchored realization shifts — lossy by construction.
The group-pin removes **nothing**: every member stays in the pool, the planner
still evaluates them, and the constraint only *narrows the search* to a family
the user chose. Inspection shrinks (A); the routing DOF is preserved and the
planner resolves it (B).

## Files
- `src/congestion_planner.h` — `BundleInput::pinned_group`.
- `src/congestion_planner.cpp` — selection loop iterates the group (byte-identical when empty).
- `src/bind_routing.cpp` — `pinned_group` binding.
- `src/buda_session/edit.py` — `_loci_groups`; group branch in `_select_single_topology_internal`; `_unpin_topology_internal` clears it.
- `src/buda_cmds/planner_cmds.py` — `group:<N>` parse in `select_topology`.
- `src/buda_session/reports.py` — `dump_topologies --grouped` + `_locus_coord`.
- `test/tests/test_supercandidate.py` — grouping, restrict, refine, byte-identical, clear.
