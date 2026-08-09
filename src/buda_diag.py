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
    # ── interchange ────────────────────────────────────────────────────────
    "BUDA-1601": (ERROR, "A cell in the DEF has no footprint in the LEF."),
    "BUDA-1606": (WARNING, "A cell in the DEF has no footprint in the LEF "
                           "and was sized anyway, by explicit waiver."),
    "BUDA-1602": (WARNING, "Imported design counts differ from the counts the "
                           "file declares."),
    "BUDA-1603": (WARNING, "A construct in the file has no representation in "
                           "BUDA's model and was recorded, not applied."),
    "BUDA-1604": (WARNING, "A DEF TRACKS statement names a layer that is not "
                           "declared."),
    "BUDA-1605": (INFO,   "Technology data was skipped because the script "
                          "already declared it."),
    # ── advisory writer ────────────────────────────────────────────────────
    "BUDA-1701": (WARNING, "Nothing to emit: the plan has no placed bus "
                           "segments."),
    # ── session / units ────────────────────────────────────────────────────
    "BUDA-1901": (FATAL,  "The design's coordinates and its track patterns "
                          "are on implausibly different scales."),
    "BUDA-1902": (ERROR,  "A design audit reported violations and "
                          "--strict-check is on."),
}


def catalogue():
    """The message catalogue, id-ordered — what a methodology reads to know
    what it may waive or gate on."""
    return [(mid, sev, text) for mid, (sev, text) in sorted(MESSAGES.items())]


def format(msg_id, detail="", severity=None):
    """`BUDA-NNNN: SEVERITY: detail` — the one place the shape is decided.

    `severity` overrides the registered default.  That is not a loophole, it
    is the feature: `set_unit_check warn` means "I have seen BUDA-1901 and I
    accept it on this design", which is a severity downgrade of a fault whose
    IDENTITY is unchanged.  Expressing it as the same id at a lower severity
    is what lets a methodology keep gating on the id.  Reporting stays honest
    because `severity_of_line` reads the LINE, not the registry.
    """
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
