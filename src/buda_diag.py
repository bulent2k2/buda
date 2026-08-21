# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Message identity and severity (Phase 5 of the LEF/DEF interface plan).

Every EDA tool a methodology can live with gives its messages **stable ids**.
Not for decoration: an id is what lets a flow say `set_message_severity
BUDA-1203 ignore`, what a regression harness greps for, and what a bug report
can name without pasting a paragraph of prose that changed last release.

BUDA's diagnostics have always been prose, so a methodology could only match
them by substring — which breaks the moment anyone improves the wording.
Phase 0 already had to guess at severity with a regex over the text
(`_DIAG_ERR_RE`), which is exactly the symptom of not having this.

**The contract.**

    BUDA-<NNNN>: <severity>: <message>

* an id, once issued, NEVER changes meaning and is never reused;
* the number is arbitrary and grouped only by range (see `_RANGES`);
* severity is one of INFO / WARNING / ERROR / FATAL;
* the message text is free to improve — that is the point of having the id.

This module is deliberately small and additive.  Existing prose diagnostics
keep working; `emit()` is how a call site opts into identity, and the
per-command counters in `buda_cli` already know how to read both forms.
"""
import re
import sys

INFO, WARNING, ERROR, FATAL = "INFO", "WARNING", "ERROR", "FATAL"

_SEVERITIES = (INFO, WARNING, ERROR, FATAL)

# The wire format, in ONE place.  `buda_cli` counts warnings and errors by
# reading it back out of captured output, so if the writer and the reader
# each carried their own idea of the shape they could drift and an
# identified diagnostic would stop being counted — which is worse than never
# having identified it.
ID_RE = re.compile(r'^\s*(BUDA-\d{4}):\s*(INFO|WARNING|ERROR|FATAL)\b')

# Number ranges, so an id carries a hint of where it came from.  These are a
# filing convention, not a guarantee — the id is the identity.
_RANGES = {
    "setup":    (1000, 1099),
    "bundler":  (1100, 1199),
    "topology": (1200, 1299),
    "planner":  (1300, 1399),
    "nuts":     (1400, 1499),
    "verify":   (1500, 1599),
    "interchange": (1600, 1699),   # LEF / DEF / GDS readers and writers
    "advisory": (1700, 1799),      # the corridor manifest / DEF blockages
    "session":  (1900, 1999),      # CLI, logging, reporting
}

# The registry.  A message is DEFINED here — id, severity, and a one-line
# description of what it means — so `dump_messages` can print the catalogue a
# methodology needs in order to waive or gate on anything.
#
# Adding an entry is cheap; changing one's MEANING is not allowed.  The test
# suite pins uniqueness and the id->meaning mapping.
MESSAGES = {
    # ── topology generation ────────────────────────────────────────────────
    "BUDA-1210": (WARNING, "A hierarchical bundle names an endpoint block "
                           "with no placement, so that endpoint was dropped "
                           "from the bundle's routing interface."),
    "BUDA-1211": (WARNING, "A hierarchical bundle has no placed endpoint "
                           "pair left, so no candidates were generated and "
                           "its nets are unrouted."),
    # ── nuts ───────────────────────────────────────────────────────────────
    "BUDA-1503": (WARNING, "A restored checkpoint holds a routed design but "
                           "no routing grid (written before schema v29), so "
                           "re-solving it uses a different physical model "
                           "than the one it was routed under."),
    # ── verify ─────────────────────────────────────────────────────────────
    "BUDA-1502": (WARNING, "A selected topology names a block that is not in "
                           "the floorplan a resume would restore it against, "
                           "so a checkpoint of this design will not reload."),
    # ── interchange ────────────────────────────────────────────────────────
    "BUDA-1601": (ERROR, "A cell in the DEF has no footprint in the LEF."),
    "BUDA-1606": (WARNING, "A cell in the DEF has no footprint in the LEF "
                           "and was sized anyway, by explicit waiver."),
    "BUDA-1607": (WARNING, "A hierarchical container has no placed "
                           "descendant, so it could not be given an extent."),
    "BUDA-1608": (INFO,    "Netlist instances of undefined modules were "
                           "skipped as library cells."),
    "BUDA-1609": (WARNING, "A script-relative path was not found, but the "
                           "same path exists relative to the CWD — the "
                           "pre-unification root for import/export commands."),
    # #658 claimed 1609 for the path-root warning while this branch was open.
    # An id never changes meaning and that one landed first, so the netlist
    # reader's three renumber rather than the path warning moving.
    "BUDA-1610": (WARNING, "A port connection names no net this reader can "
                           "resolve, so the connection is an open."),
    "BUDA-1612": (WARNING, "A part-select connects a port whose declared "
                           "width is unknown, so only its low bit was "
                           "connected."),
    "BUDA-1613": (WARNING, "A DEF NONDEFAULTRULE could not be translated to "
                           "a BUDA rule and was skipped."),
    "BUDA-1614": (WARNING, "A GDS TEXT label lands outside every component, "
                           "so its net was not recovered."),
    "BUDA-1615": (WARNING, "Imported obstruction was thinner than one layout "
                           "unit and was dropped when its coordinates were "
                           "rounded to integers."),
    "BUDA-1602": (WARNING, "Imported design counts differ from the counts the "
                           "file declares."),
    "BUDA-1603": (WARNING, "A construct in the file has no representation in "
                           "BUDA's model and was recorded, not applied."),
    "BUDA-1604": (WARNING, "A DEF TRACKS statement names a layer that is not "
                           "declared."),
    "BUDA-1605": (INFO,   "Technology data was skipped because the script "
                          "already declared it."),
    "BUDA-1616": (WARNING, "The DEF's TRACKS pitch and the technology LEF's "
                           "PITCH disagree for a layer; the DEF's grid is "
                           "used."),
    # ── advisory writer ────────────────────────────────────────────────────
    "BUDA-1701": (WARNING, "Nothing to emit: the plan has no placed bus "
                           "segments."),
    # ── session / units ────────────────────────────────────────────────────
    "BUDA-1901": (FATAL,  "The design's coordinates and its track patterns "
                          "are on implausibly different scales."),
    "BUDA-1902": (ERROR,  "A design audit reported violations and "
                          "--strict-check is on."),
    "BUDA-1903": (WARNING, "A visualize command opened no window."),
    "BUDA-1905": (FATAL,  "An input file the script declared as REQUIRED is "
                          "missing, so the run stopped before the command "
                          "that would have read it."),
    "BUDA-1906": (FATAL,  "A command raised an engine error and the run "
                          "stopped."),
    "BUDA-1904": (WARNING, "A restored fan-in bundle has no per-bit "
                           "endpoints, so it resumes untapered — wider "
                           "than the design that was checkpointed."),
    "BUDA-1912": (WARNING, "An NDR rule's stored per-layer values are not "
                           "readable and were dropped, so the rule restores "
                           "layer-independent."),
    "BUDA-1913": (WARNING, "An NDR rule quantizes to no constraint on EVERY "
                           "layer it can reach, so the buses it governs "
                           "route exactly as ungoverned ones."),
    "BUDA-1914": (INFO,    "An NDR rule quantizes to no constraint on SOME "
                           "of the layers it can reach, so its metal there "
                           "is governed in name only."),
    "BUDA-1915": (WARNING, "A set_ndr scope matches no net in the design, so "
                           "the rule it names governs nothing."),
    "BUDA-1916": (INFO,    "A set_ndr scope is outranked by a longer prefix "
                           "on every net it matches, so it governs nothing."),
    "BUDA-1917": (WARNING, "A second open_bdb replaced an already-open "
                           "file-backed BDB, so persistence splits across "
                           "two files: rows persisted before this line stay "
                           "in the previous file, and everything from here "
                           "on lands in the new one."),
}

# Ids that were ISSUED and whose fault no longer exists.
#
# An id may never be reused and may never change meaning, so an id whose
# condition has been fixed cannot simply be deleted — a later message taking
# the number would silently redefine what an old flow log or a methodology's
# waiver refers to.  Retiring records the number as spent, keeps
# `dump_messages` able to say so (a gate on a retired id is dead, and its
# owner should learn that from the tool rather than from silence), and
# `format()` refuses to emit one.
RETIRED = {
    "BUDA-1911": "An NDR rule's per-layer values were not carried by the BDB "
                 "schema.  Retired: v28 persists them (ndr_rule.per_layer), "
                 "so a reopened session restores the whole declaration and "
                 "there is nothing left to warn about.",
    "BUDA-1611": "A part-select's low bit is not 0 on a module the reader "
                 "descends into.  Retired: port bits are now mapped through "
                 "a per-bit context, so an offset slice resolves EXACTLY and "
                 "there is nothing left to warn about.",
}


def catalogue():
    """The message catalogue, id-ordered — what a methodology reads to know
    what it may waive or gate on."""
    return [(mid, sev, text) for mid, (sev, text) in sorted(MESSAGES.items())]


def retired():
    """The spent ids, id-ordered.  Printed by `dump_messages` so a gate on a
    retired id can be found and removed rather than quietly never firing."""
    return sorted(RETIRED.items())


def format(msg_id, detail="", severity=None):
    """`BUDA-NNNN: SEVERITY: detail` — the one place the shape is decided.

    `severity` overrides the registered default.  That is not a loophole, it
    is the feature: `set_unit_check warn` means "I have seen BUDA-1901 and I
    accept it on this design", which is a severity downgrade of a fault whose
    IDENTITY is unchanged.  Expressing it as the same id at a lower severity
    is what lets a methodology keep gating on the id.  Reporting stays honest
    because `severity_of_line` reads the LINE, not the registry.
    """
    if msg_id in RETIRED:
        raise KeyError(f"message id {msg_id!r} is RETIRED and must not be "
                       f"emitted again: {RETIRED[msg_id]}")
    if msg_id not in MESSAGES:
        raise KeyError(f"unknown message id {msg_id!r} — add it to "
                       f"buda_diag.MESSAGES so it can be waived and gated on")
    sev = severity or MESSAGES[msg_id][0]
    if sev not in _SEVERITIES:
        raise ValueError(f"unknown severity {sev!r}, expected one of "
                         f"{', '.join(_SEVERITIES)}")
    return f"{msg_id}: {sev}: {detail}" if detail else f"{msg_id}: {sev}"


def emit(msg_id, detail="", file=None, severity=None):
    """Print an identified diagnostic.

    Deliberately a plain print: the CLI captures per-command stdout into the
    flow log and derives its counters from the text, so an identified message
    has to travel the same path as an unidentified one or it would vanish
    from the log it belongs in."""
    print(format(msg_id, detail, severity), file=file or sys.stdout)


def severity_of(msg_id):
    return MESSAGES[msg_id][0] if msg_id in MESSAGES else None


def severity_of_line(line):
    """The DECLARED severity of an output line, or None if it carries no id.

    This is what an id buys over prose.  The unidentified path has to guess
    from the words in the text, so a message whose *detail* happens to say
    "…without warning…" is counted as a warning, and a FATAL is counted as
    nothing at all because the word "error" does not appear in it.  A line
    that names its id says what it is, and this reads that answer instead of
    re-deriving it.
    """
    m = ID_RE.match(line)
    return m.group(2) if m else None


def id_of_line(line):
    """The message id on a line, or None — what a harness greps for."""
    m = ID_RE.match(line)
    return m.group(1) if m else None
