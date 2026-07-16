# BUDA feature files — the narrative spec layer

These Gherkin `.feature` files are BUDA's **behavioral specification &
documentation layer**: the human-readable "what the system does and why," with
the ASCII geometry diagrams. They are executable where the pytest-bdd binding is
cheap, and documentation where the real coverage already lives in a hand-written
`test_*.py`. The plan of record and the arc → feature coverage map live in
[`docs/internal/feature_coverage_plan.md`](../../../docs/internal/feature_coverage_plan.md).

## Tag vocabulary

Every feature (or scenario) is tagged so a reader can tell landed from
aspirational at a glance:

- `@landed` — matches shipped behavior. Either bound to pytest-bdd and green, or
  a narrative spec whose executable coverage lives in a named `test_*.py` (given
  in the feature's header comment).
- `@future` — spec **ahead of** the code. Not bound, or `xfail` with a reason.
  Carries a `# see docs/…` pointer. Reading a `@future` file tells you what the
  system is *planned* to do, not what it does today.
- `@doc` — narrative mirror **by design**: the executable tests live in a named
  hand-written `test_*.py` (not pytest-bdd), because the behavior is unit-level
  (track math, sweep-line packing) that reads better as plain asserts. The
  `.feature` is the readable spec beside it.
- `@orphaned` — kept for historical/documentation value but bound to nothing and
  not maintained; a candidate for the attic. Never relied on for coverage.
- `@mid` / `@slow` — mirror the `pytestmark` tier of the bound step file so the
  feature signals its cost (fast-tier files carry no tier tag).

Tags are Gherkin comments/annotations; adding a tag above a `Scenario:` does not
change how `scenarios()` / `@scenario` binds (binding is by scenario **name**), so
tags are safe to add to already-bound files.

## How binding works here

`pytest-bdd` only processes a `.feature` file that a `test_*.py` references via
`scenarios('features/x.feature')` or `@scenario(...)`. There is **no
auto-discovery** — an unreferenced `.feature` is inert under pytest. That is why
narrative `@future` / `@doc` / `@orphaned` files can live here without being
collected as failing tests.

Bound features are wired from `test/tests/test_<name>.py`; the shared step
definitions and fixtures live in `test/tests/conftest.py`. Tiering
(`@pytest.mark.mid` / `.slow`) is applied in the Python step file via
`pytestmark`, not in Gherkin.

## Maintenance rule

When an arc lands, add or update its feature file **in the same PR** and refresh
the coverage map in `feature_coverage_plan.md`. When a `@future` scenario's code
ships, flip its tag to `@landed` and bind or name its coverage.
