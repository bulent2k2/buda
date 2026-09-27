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
the built artifacts can show the flag took.  With it on, the jumps it pads are
almost never left on a 32-byte boundary; without it about 12 % are.

Where the flag must be in force -- CI's pinned runner image -- set
BUDA_JCC_STRICT=1 and a build that applied no flag FAILS here instead of
skipping: a flag that silently stops being applied is exactly the loss this
file exists to catch.
"""

import os
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


def test_jumps_are_sorted_by_whether_the_flag_can_move_them():
    # The assembler pads with segment prefixes, so a mitigated build is full
    # of `cs cs nopw` and `ds jmp`; those must be read through.  An indirect
    # jmp is the erratum's too, but the flag leaves it alone.
    text = "\n".join([
        "  1e:\tcmp    %eax,%ebx",               # ends at 0x20 -- not a jump
        "  20:\tjne    40 <f>",                   # 0x20..0x22 -- fine
        "  22:\tcs cs nopw 0x0(%rax,%rax,1)",
        "  3a:\tds jmp 60 <f+0x20>",              # 0x3a..0x3e -- fine
        "  3e:\tnotrack jmp *%rax",               # 0x3e..0x40 -- on it
        "  40:\tjrcxz  48 <g>",                   # not counted
        "  42:\tjmpq   *0x10(%rip)",              # 0x42..0x48 -- fine
        "  48:\tret",
        "0000000000000048 <g>:",                  # a label, not an instruction
    ])
    rows = jcc_audit.instructions(text)
    assert [op for _a, op, _o in rows] == [
        "cmp", "jne", "nopw", "jmp", "jmp", "jrcxz", "jmpq", "ret"]
    assert jcc_audit.count(rows) == {"padded": (2, 0), "exposed": (2, 1)}


# ── the build ─────────────────────────────────────────────────────────────

_BUILD = pathlib.Path(buda.__file__).parent


def _cache_value(name):
    """A CMakeCache entry of the imported module's build: None when that
    build has no cache here (e.g. a wheel), '' when the entry is absent."""
    cache = _BUILD / "CMakeCache.txt"
    if not cache.exists():
        return None
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith(name + ":"):
            return line.split("=", 1)[1]
    return ""


def _artifacts():
    """(path, needs_c_flag) for the three build products: the two extension
    modules (LTO: code generated at the link) and libbuda_core (no LTO, and
    it carries the C sqlite amalgamation, so it needs the C compiler's flag
    too)."""
    out = [(pathlib.Path(buda.__file__), False)]
    out += [(p, False) for p in _BUILD.glob("buda_db*.so")]
    out += [(p, True) for p in _BUILD.glob("libbuda_core*.so")]
    return out


def test_the_built_artifacts_carry_the_mitigation():
    strict = os.environ.get("BUDA_JCC_STRICT") == "1"
    if platform.machine().lower() not in ("x86_64", "amd64"):
        pytest.skip("the audit reads x86-64 disassembly")
    if not sys.platform.startswith("linux"):
        # An Intel Mac's objdump is llvm-objdump over Mach-O, whose code
        # section is __text: `-j .text` would find no jump at all.
        pytest.skip("the audit reads ELF modules")
    if shutil.which("objdump") is None:
        pytest.skip("objdump is not installed")
    flag = _cache_value("BUDA_JCC_FLAG_IN_USE")
    if flag is None:
        pytest.skip("the imported buda was not built by CMake in its directory")
    if not flag:
        if strict:
            pytest.fail("BUDA_JCC_STRICT=1 but CMake applied no JCC flag: "
                        "see the 'BUDA JCC-erratum mitigation' configure line")
        pytest.skip("CMake applied no JCC flag (see the configure line)")
    c_flag = _cache_value("BUDA_JCC_FLAG_C")

    judged = 0
    for path, needs_c in _artifacts():
        if needs_c and not c_flag:
            continue    # the C compiler took no flag; sqlite is unpadded
        c = jcc_audit.audit(path)
        n, bad = c["padded"]
        assert n > 1000, f"{path.name}: the disassembly held almost no jumps"
        # Measured with the flag: a few dozen at most, all in code the
        # assembler never saw (the C runtime's startup objects); about 12 %
        # without it.
        assert bad <= 0.001 * n, (
            f"{path.name}: {bad} of {n} jcc/direct jmp cross or end on a "
            f"32-byte boundary although CMake applied {flag!r}: the flag did "
            f"not reach the code generator")
        judged += 1
    assert judged >= 2, "found fewer build artifacts than expected"
