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

"""The Intel JCC-erratum mitigation reaches the machine code
(CMakeLists' BUDA_JCC_MITIGATION, docs/internal/jcc_erratum.md).

A mitigation flag lost on its way to the code generator changes nothing but
the speed, and under LTO that code is generated at the LINK, so nothing but
the built module can show the flag took.  With it on, the jumps left on a
32-byte boundary are a rounding error; without it about 12 % are.
"""

import pathlib
import platform
import shutil
import sys

import pytest

import buda
import jcc_audit


# ── the rule itself ───────────────────────────────────────────────────────

@pytest.mark.parametrize("start, end, bad", [
    (0x10, 0x12, False),   # inside one 32-byte line
    (0x1e, 0x20, True),    # ends exactly on the boundary
    (0x1f, 0x24, True),    # crosses it
    (0x20, 0x22, False),   # starts on it, which is fine
    (0x3a, 0x40, True),    # ends on the next one
])
def test_the_boundary_rule(start, end, bad):
    assert jcc_audit.on_boundary(start, end) is bad


def test_only_jumps_are_judged_and_prefixes_are_seen_through():
    # A compare ending on the boundary is not a jump; a prefixed jmp is.
    text = "\n".join([
        "  1e:\tcmp    %eax,%ebx",        # ends at 0x20 -- not judged
        "  20:\tjne    40 <f>",            # 0x20..0x22 -- fine
        "  22:\tnop",
        "  3e:\tnotrack jmp *%rax",        # 0x3e..0x40 -- on the boundary
        "  40:\tret",
        "0000000000000040 <g>:",           # a label, not an instruction
    ])
    rows = jcc_audit.instructions(text)
    assert [op for _a, op in rows] == ["cmp", "jne", "nop", "jmp", "ret"]
    assert jcc_audit.count(rows) == (2, 1)


# ── the build ─────────────────────────────────────────────────────────────

def _flag_in_use():
    """The flag CMake applied to the imported module's build, '' when none,
    None when the module was not built by CMake here (e.g. a wheel)."""
    cache = pathlib.Path(buda.__file__).parent / "CMakeCache.txt"
    if not cache.exists():
        return None
    for line in cache.read_text().splitlines():
        if line.startswith("BUDA_JCC_FLAG_IN_USE:"):
            return line.split("=", 1)[1]
    return ""


def test_the_built_module_carries_the_mitigation():
    flag = _flag_in_use()
    if flag is None:
        pytest.skip("the imported buda was not built by CMake in its directory")
    if not flag:
        pytest.skip("BUDA_JCC_MITIGATION is off, or this toolchain has no "
                    "flag for it")
    if platform.machine().lower() not in ("x86_64", "amd64"):
        pytest.skip("the audit reads x86-64 disassembly")
    if not sys.platform.startswith("linux"):
        # An Intel Mac's objdump is llvm-objdump over Mach-O, whose code
        # section is __text: `-j .text` would find no jump at all.
        pytest.skip("the audit reads an ELF module's .text")
    if shutil.which("objdump") is None:
        pytest.skip("objdump is not installed")
    jumps, bad = jcc_audit.audit(buda.__file__)
    assert jumps > 1000, "the disassembly held almost no jumps"
    # Measured under 0.1 % with the flag (the padding cannot reach a jump
    # the assembler never sees) and about 12 % without it.
    assert bad <= 0.01 * jumps, (
        f"{bad} of {jumps} jumps cross or end on a 32-byte boundary although "
        f"CMake applied {flag!r}: the flag did not reach the code generator")
