# Message identity, severity, and logs

*Phase 5 of [lefdef_interface_plan.md](lefdef_interface_plan.md), logging
half.  Implemented in `src/buda_diag.py`; the catalogue is printed by the
`dump_messages` command and pinned by `test/tests/test_diagnostics.py`.*

---

## 1. Why an id

Every EDA tool a methodology can live with gives its messages stable
identifiers.  Not for decoration — an id is what lets a flow say

    set_message_severity BUDA-1602 ignore     # (a downstream tool's syntax)

what a regression harness greps for, and what a bug report can name without
pasting a paragraph of prose that changed last release.

BUDA's diagnostics were prose only, so the only way to match one was a
substring — which breaks the moment anyone improves the wording.  The
symptom was already visible in the codebase before this phase: `buda_cli`
counted a command's warnings and errors with a **regex over the text**
(`_DIAG_WARN_RE` / `_DIAG_ERR_RE`), because there was nothing else to count.

That guess is wrong in both directions, and both were measured on real
output rather than imagined:

* a **FATAL** contains none of the words the matcher looks for, so the
  unit-scale stop counted as *zero* errors;
* a DEF **count mismatch** printed `<-- MISMATCH` with no marker at all, so
  a half-read floorplan produced a run with a clean warning count;
* conversely, any line whose detail merely mentions "warning" counted as
  one.

## 2. The contract

    BUDA-<NNNN>: <SEVERITY>: <message text>

* an id, once issued, **never changes meaning** and is never reused;
* the number is arbitrary and grouped only by range (below);
* severity is one of `INFO` / `WARNING` / `ERROR` / `FATAL`;
* **the message text is free to improve** — that is the point of the id.

Ranges are a filing convention, not a guarantee; the id is the identity.

| Range | Area |
|---|---|
| 1000–1099 | setup |
| 1100–1199 | bundler |
| 1200–1299 | topology |
| 1300–1399 | planner |
| 1400–1499 | NUTS |
| 1500–1599 | verify |
| 1600–1699 | interchange (LEF / DEF / GDS) |
| 1700–1799 | advisory writer |
| 1900–1999 | session (CLI, logging, reporting) |

A message is **defined in `buda_diag.MESSAGES`** — id, severity, and a
one-line description — and `format()` raises on an unknown id.  That is
deliberate: an id that exists only inside one `print` cannot be waived,
which is the exact failure the module exists to prevent.

## 3. Severity is on the line, not in the registry

`format(msg_id, detail, severity=…)` may override the registered default,
and the reader (`severity_of_line`) reads the **line**.  This is not a
loophole; it is how a declared policy is expressed:

    set_unit_check warn

means *"I have seen BUDA-1901 and I accept it on this design"* — a severity
downgrade of a fault whose identity is unchanged.  Emitting it as the same
id at a lower severity is what keeps a methodology's gate on BUDA-1901
meaningful; emitting it as a different message would hide it.

The same pattern gives the DEF importer's waiver its own id instead:
`allow_missing_footprints` is not a downgrade of BUDA-1601 by policy, it is
a *different situation* (the geometry is knowingly fictional), so it is
**BUDA-1606**.  The rule of thumb: same fault, user accepted it → same id,
lower severity.  Different fault → different id.

## 4. Counting

`buda_cli._count_diags` reads the declared severity when a line carries an
id and falls back to the prose matchers when it does not.  Unidentified
output therefore counts exactly as it did before this phase (test-pinned),
so no existing flow log changes.

`_is_error_line` accepts both the prose `Error: …` and an identified
`ERROR`/`FATAL`, so converting a call site to an id cannot demote its
message out of the terminal summary — a silent cost that would have made
every conversion a small regression.

## 5. What is identified so far

`dump_messages` prints the live list — this table is a snapshot of it, and
`test_diagnostics.py` fails if the two disagree (BUDA-1607 was added without
updating this table, which is exactly the drift the guard now prevents):

| Id | Severity | Meaning |
|---|---|---|
| BUDA-1210 | WARNING | a hier bundle's endpoint block has no placement; that endpoint was dropped |
| BUDA-1211 | WARNING | a hier bundle has no placed endpoint pair left; no candidates generated |
| BUDA-1502 | WARNING | a selected topology names a block absent from the frame a resume restores it in |
| BUDA-1601 | ERROR | a cell in the DEF has no LEF footprint |
| BUDA-1602 | WARNING | imported counts differ from the file's declared counts |
| BUDA-1603 | WARNING | a construct was recorded, not applied |
| BUDA-1604 | WARNING | DEF `TRACKS` names an undeclared layer |
| BUDA-1605 | INFO | technology data skipped: the script already declared it |
| BUDA-1606 | WARNING | missing LEF footprint, sized anyway by waiver |
| BUDA-1607 | WARNING | a container has no placed descendant, so it has no extent |
| BUDA-1608 | INFO | netlist instances of undefined modules skipped as library cells |
| BUDA-1609 | WARNING | a script-relative path was not found but exists relative to the CWD (the pre-unification root) |
| BUDA-1610 | WARNING | a port connection names no resolvable net, so it is an open |
| BUDA-1612 | WARNING | a part-select's port has no declared width, so only its low bit was connected |
| BUDA-1613 | WARNING | a DEF NONDEFAULTRULE could not be translated to a BUDA rule and was skipped |
| BUDA-1614 | WARNING | a GDS TEXT label lands outside every component, so its net was not recovered |
| BUDA-1701 | WARNING | nothing to emit: no placed bus segments |
| BUDA-1901 | FATAL | coordinates and track patterns are on different scales |
| BUDA-1902 | ERROR | a design audit reported violations and `--strict-check` is on |
| BUDA-1903 | WARNING | a `visualize` command opened no window (INFO when the suppression was asked for: `--no-viz` / `buda::start -viz 0`) |
| BUDA-1904 | WARNING | a restored fan-in bundle has no per-bit endpoints (pre-v27 checkpoint), so it resumes untapered and wider than what was saved |
| BUDA-1905 | FATAL | an input file a `require_file` declared is missing, so the run stopped before the command that would have read it |
| BUDA-1906 | FATAL | a command raised an engine error and the run stopped (the traceback is in the flow log; `BUDA_TRACEBACK=1` prints it) |
| BUDA-1912 | WARNING | an NDR rule's stored per-layer values are not readable and were dropped, so the rule restores layer-independent |
| BUDA-1913 | WARNING | an NDR rule quantizes to no constraint on EVERY layer it can reach, so the buses it governs route exactly as ungoverned ones |
| BUDA-1914 | INFO | an NDR rule quantizes to no constraint on SOME of the layers it can reach, so its metal there is governed in name only |

**Retired ids.**  An id may never be reused and never changes meaning, so one
whose fault has been fixed is recorded as *spent* rather than deleted — a
later message taking the number would silently redefine what an old flow log
or a methodology's waiver refers to.  `dump_messages` prints these too,
because a gate on a retired id otherwise just stops firing, which reads
exactly like a design that stopped having the problem.

| Id | Retired because |
|---|---|
| BUDA-1611 | a part-select's low bit is not 0 on a descended module. Port bits are now mapped through a per-bit context, so an offset slice resolves EXACTLY and there is nothing left to warn about |
| BUDA-1911 | an NDR rule's per-layer values were not carried by the BDB schema. v28 persists them (`ndr_rule.per_layer`), so a reopened session restores the whole declaration |

The list is short on purpose.  Converting a diagnostic is a promise to keep
its id stable forever, so the ones converted first are the ones a
methodology would actually gate on.  `test_diagnostics.py` asserts that
every **registered** id is emitted somewhere in `src/` — a catalogue nothing
emits is decoration.

## 6. Non-overwriting logs

A re-run used to destroy `log/<cell>_flow.log` in place, so the evidence for
"it worked an hour ago" was gone the moment you tried to reproduce it.

The **current** run keeps the canonical path — every test and doc reference
points at it, and rotating forward would be a rename dressed up as a feature
— and the previous run moves to `<name>.1`.  Exactly one generation is
kept: comparing a run against the one before it is the case that comes up,
and `--log` / `--tag` already archive every run for the case that does not.

Rotation is best-effort (`os.replace`, `OSError` swallowed): losing the
previous log is bad, losing the run is worse.
