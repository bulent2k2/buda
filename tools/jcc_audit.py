#!/usr/bin/env python3
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

"""jcc_audit -- count the jumps in a built module that sit where the Intel
JCC erratum penalizes them.

On Skylake-derived cores (Cascade Lake among them) a jump that crosses a
32-byte boundary, or ends on one, is kept out of the decoded-instruction cache
by the erratum's microcode fix and runs markedly slower.  CMakeLists'
BUDA_JCC_MITIGATION has the assembler pad every jump off those boundaries
(docs/internal/jcc_erratum.md).  This counts what is left, so a build can
show the padding reached the machine code: under LTO that code is generated
at the LINK, and a flag lost on the way there would change nothing but the
speed.

    tools/jcc_audit.py build/buda.cpython-311-x86_64-linux-gnu.so [...]

It reads objdump's disassembly of .text and takes each instruction's length
from the next instruction's address.  It judges jcc and jmp alone, not a
compare fused into a jcc, so it is a necessary condition rather than the
assembler's full rule: measured, an unmitigated build here has about 12 % of
its jumps on a boundary and a mitigated one under 0.2 %.  An x86-64 ELF
module and GNU objdump only (a Mach-O module's code is in __text, which
`-j .text` does not name).  Always exits 0; the numbers are the result.
"""

import re
import subprocess
import sys

# "  2a5f84:\tjne    2a5fa0 <...>" with --no-show-raw-insn; a symbol label
# ("00000000002a5f80 <f>:") has no colon right after the address and does
# not match.
_INSN = re.compile(r"^\s*([0-9a-f]+):\s+(.*)$")

# Prefixes objdump prints as separate words before the mnemonic.
_PREFIXES = {"notrack", "bnd", "cs", "ds", "ss", "es", "fs", "gs",
             "data16", "addr32", "rex", "rex.W"}

BOUNDARY = 32


def instructions(disassembly):
    """[(address, mnemonic)] in address order, prefixes skipped."""
    rows = []
    for line in disassembly.splitlines():
        m = _INSN.match(line)
        if not m:
            continue
        words = m.group(2).split()
        while words and words[0] in _PREFIXES:
            words = words[1:]
        rows.append((int(m.group(1), 16), words[0] if words else ""))
    return rows


def on_boundary(start, end, boundary=BOUNDARY):
    """True if the instruction [start, end) crosses a `boundary`-byte line or
    ends exactly on one -- the two placements the erratum penalizes."""
    return start // boundary != (end - 1) // boundary or end % boundary == 0


def count(rows, boundary=BOUNDARY):
    """(jumps, jumps on a boundary) over `rows` from `instructions`.  The
    last row has no successor to give its length and is not judged."""
    jumps = bad = 0
    for (start, op), (end, _next) in zip(rows, rows[1:]):
        if op.startswith("j"):
            jumps += 1
            if on_boundary(start, end, boundary):
                bad += 1
    return jumps, bad


def audit(path):
    """(jumps, jumps on a boundary) in `path`'s .text."""
    out = subprocess.run(["objdump", "-d", "--no-show-raw-insn", "-j", ".text",
                          str(path)], capture_output=True, text=True,
                         check=True).stdout
    return count(instructions(out))


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 0
    for path in argv:
        jumps, bad = audit(path)
        print(f"{path}: {jumps} jumps, {bad} cross or end on a "
              f"{BOUNDARY}-byte boundary ({100.0 * bad / max(jumps, 1):.2f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
