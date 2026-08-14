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

"""Pick the right `bin/` wrapper launcher for this platform.

Every wrapper exists twice: the bash original (`bin/<name>`) and a PowerShell
twin (`bin/<name>.ps1`).  Tests that exercise wrapper behaviour must invoke
the one this platform can actually run:

- POSIX (Linux, macOS, Cygwin — where ``sys.platform == "cygwin"`` and bash
  is real): the bash wrapper.
- Native Windows: the PowerShell twin.  ``bash`` there resolves to the
  System32 WSL stub, which prints "Windows Subsystem for Linux has no
  installed distributions" and runs nothing (measured, windows-validate runs
  18 and 23) — so the bash wrapper is not merely untested there, it is
  unrunnable.  ``pwsh`` (PowerShell 7) is preferred over ``powershell``
  (5.1); both run the twins.

The two implementations honour the same contracts (e.g. the
``BUDA_TEST_ANCHOR`` hook of ``buda``/``buda.ps1``, the ``BUDA_VIZ_FINAL``
env signal of ``btcl``/``btcl.ps1``), so the same assertions apply to
whichever launcher is selected — the tests validate the contract, and the
platform picks the implementation.
"""
import shutil
import sys


def wrapper_command(root, name):
    """argv prefix to run ``bin/<name>`` on this platform, or None.

    ``root`` is the repo root as a ``pathlib.Path``.  A None return means no
    capable interpreter exists here — callers should skip with a reason.

    ``BUDA_WRAPPER_PS=1`` forces the PowerShell twin on any platform with
    pwsh — how the .ps1 contracts are exercised on a POSIX dev box or Linux
    CI without waiting for a Windows runner (the #735 btcl.ps1 char-splat
    bug shipped precisely because the twins could only be run on Windows).
    """
    import os
    force_ps = os.environ.get("BUDA_WRAPPER_PS") == "1"
    if sys.platform == "win32" or force_ps:
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return None
        return [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(root / "bin" / f"{name}.ps1")]
    if not shutil.which("bash"):
        return None
    return ["bash", str(root / "bin" / name)]


def wrapper_missing_reason(name):
    """The skip reason when :func:`wrapper_command` returned None."""
    if sys.platform == "win32":
        return (f"bin/{name}.ps1 needs PowerShell (pwsh or powershell), "
                "and neither is on PATH")
    return f"bin/{name} needs bash, which is not on PATH"
