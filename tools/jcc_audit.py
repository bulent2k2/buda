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
BUDA_JCC_MITIGATION has the assembler pad the jumps it can move off those
boundaries (docs/internal/jcc_erratum.md).  This counts what is left, so a
build can show the padding reached the machine code: under LTO that code is
generated at the LINK, and a flag lost on the way there would change nothing
but the speed.

    tools/jcc_audit.py build/buda.cpython-311-x86_64-linux-gnu.so [...]

What the flag pads, in both GNU as and LLVM, is conditional jumps (a
macro-fused compare-and-jump pair kept together) and DIRECT unconditional
jumps.  Indirect jumps, calls and returns are penalized by the erratum too,
but the flag leaves them where they are.  So the jumps are counted in two
groups:

  padded   jcc and direct jmp -- the flag must leave none of these on a
           boundary, and a mitigated build measures a handful: code the
           assembler never saw, such as the C runtime's startup objects
  exposed  indirect jmp (e.g. a switch table's `notrack jmp *%rax`) -- the
           flag cannot move these, so their count is information, not a
           defect

Each jump is judged by its own bytes; a fused pair is not judged as a unit,
so this is a necessary condition rather than the assembler's whole rule.
jrcxz and the loop family are not counted (no compiler here emits them, and
the flag does not pad them).  Instruction lengths come from the next
instruction's address.  An x86-64 ELF module and GNU objdump only (a Mach-O
module's code is in __text, which `-j .text` does not name).  Always exits 0;
the numbers are the result.
"""

import re
import subprocess
import sys

# "  2a5f84:\tjne    2a5fa0 <...>" with --no-show-raw-insn; a symbol label
# ("00000000002a5f80 <f>:") has no colon right after the address and does
# not match.
_INSN = re.compile(r"^\s*([0-9a-f]+):\s+(.*)$")

# Prefixes objdump prints as separate words before the mnemonic.  The segment
# prefixes matter here: they are how the assembler pads, so a mitigated
# build is full of `cs cs nopw` and `ds jmp`.
_PREFIXES = {"notrack", "bnd", "cs", "ds", "ss", "es", "fs", "gs",
             "data16", "addr32", "rex", "rex.W"}

# The conditional jumps, in AT&T mnemonics (objdump prints one spelling per
# condition code).  jrcxz/jecxz are deliberately absent.
_JCC = {"jo", "jno", "jb", "jae", "je", "jne", "jbe", "ja", "js", "jns",
        "jp", "jnp", "jl", "jge", "jle", "jg",
        "jc", "jnc", "jnae", "jnb", "jz", "jnz", "jna", "jnbe", "jpe", "jpo",
        "jnge", "jnl", "jng", "jnle"}

BOUNDARY = 32


def instructions(disassembly):
    """[(address, mnemonic, operands)] in address order, prefixes skipped."""
    rows = []
    for line in disassembly.splitlines():
        m = _INSN.match(line)
        if not m:
            continue
        words = m.group(2).split(None, 1)
        while words and words[0] in _PREFIXES:
            words = words[1].split(None, 1) if len(words) > 1 else []
        op = words[0] if words else ""
        rows.append((int(m.group(1), 16), op, words[1] if len(words) > 1
                     else ""))
    return rows


def on_boundary(start, end, boundary=BOUNDARY):
    """True if the instruction [start, end) crosses a `boundary`-byte line or
    ends exactly on one -- the two placements the erratum penalizes."""
    return start // boundary != (end - 1) // boundary or end % boundary == 0


def kind(op, operands):
    """'padded' for a jump the flag moves, 'exposed' for one it cannot, None
    for anything else."""
    if op in _JCC:
        return "padded"
    if op in ("jmp", "jmpq"):
        return "exposed" if operands.lstrip().startswith("*") else "padded"
    return None


def count(rows, boundary=BOUNDARY):
    """{'padded': (n, on_boundary), 'exposed': (n, on_boundary)} over `rows`
    from `instructions`.  The last row has no successor to give its length
    and is not judged."""
    out = {"padded": [0, 0], "exposed": [0, 0]}
    for (start, op, operands), (end, _op, _rest) in zip(rows, rows[1:]):
        k = kind(op, operands)
        if k is None:
            continue
        out[k][0] += 1
        if on_boundary(start, end, boundary):
            out[k][1] += 1
    return {k: tuple(v) for k, v in out.items()}


def audit(path):
    """count() over `path`'s .text."""
    out = subprocess.run(["objdump", "-d", "--no-show-raw-insn", "-j", ".text",
                          str(path)], capture_output=True, text=True,
                         check=True).stdout
    return count(instructions(out))


def _pct(n, d):
    return 100.0 * n / max(d, 1)


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 0
    for path in argv:
        c = audit(path)
        (pn, pb), (xn, xb) = c["padded"], c["exposed"]
        print(f"{path}: {pb} of {pn} jcc/direct jmp cross or end on a "
              f"{BOUNDARY}-byte boundary ({_pct(pb, pn):.2f} %); "
              f"indirect jmp, which the flag cannot move: {xb} of {xn}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
