# BUDA Windows Requirements

Software requirements for building and testing BUDA natively on Windows —
with MSVC (the reference toolchain), MinGW-w64 (validated alternative), or
Cygwin (working, with caveats).

**This page is validated, not aspirational.** Every claim marked *measured* was
executed on a real Windows machine (GitHub `windows-2022` runner: Windows
Server 2022, Visual Studio 2022 Enterprise, MSVC 19.44, CMake 4.x, Python
3.13.14 x64) by `.github/workflows/windows-validate.yml`, which builds through
**four** documented paths (Ninja and the Visual Studio generator on MSVC,
MSYS2 UCRT64 MinGW, and Cygwin64), imports the extensions, runs the fast test
tier, and executes a `.buda` flow end to end.
Re-validate any time: *Actions → Windows validation → Run workflow*. Last
green, all four lanes on `main`: run 49, 2026-09-28.

For the cross-platform dependency reference see
[build_test_dependencies.md](build_test_dependencies.md); this page is the
Windows-specific companion. The build steps live in
[WINDOWS_BUILD.md](WINDOWS_BUILD.md).

## Target Environment

- Windows 10 or 11 (validated on Server 2022), 64-bit.
- **One of three toolchains:**
  - *MSVC (reference):* Visual Studio 2022 (Build Tools or full IDE):
    `Desktop development with C++` workload, MSVC v143, a Windows 10/11 SDK.
  - *MinGW-w64 (validated, measured green from run 16; latest run 49):*
    MSYS2 with the **UCRT64** packages `mingw-w64-ucrt-x86_64-{gcc,cmake,ninja}` — UCRT64 because
    modern CPython links the Universal CRT and CRTs must match across the
    extension boundary. MSYS2 is rolling-release (GCC 14 → 16.1.0 between two
    validation runs; both clean). Note **Git for Windows' Git Bash is NOT
    this** — it ships bash/coreutils only, no compiler (measured; a `gcc`
    that appears in Git Bash comes from some other install on PATH).
  - *Cygwin GCC (working, with caveats):* `gcc-g++,make,cmake,ninja` from
    Cygwin setup; builds via the repo's own `bin/bb` with
    `BUDA_ARCH=x86-64-v2` (required), imports, runs a flow and the fast tier
    (measured, latest run 49). See WINDOWS_BUILD.md §6 for its measured
    limitations (Python 3.12 with no installable matplotlib, no web deps).
- CMake ≥ 3.15 (the project's `cmake_minimum_required`).
- 64-bit Python. **3.13 is the validated version** for the MSVC and MinGW
  paths (the *native* CPython in both cases); CI elsewhere runs 3.11; Cygwin's
  `python3` is 3.12 (measured from run 39; it was 3.9.16 through run 38).
  The interpreter, compiler, and extension must all be x64.
- Git for Windows.

WSL is not the target described here (it simply follows the Linux docs).

## Required Python Packages

```powershell
python -m pip install pybind11 pytest pytest-bdd matplotlib numpy
python -m pip install -r src/web/requirements.txt
```

`src/web/requirements.txt` (fastapi/uvicorn/httpx) is **not optional for a full
test run**: without it pytest silently skips the 28 web-server/WebSocket/
checkpoint/edit tests while still reporting success — the exact silent-shrink
failure mode the Linux CI grew a guard against (`docs/internal/ci.md`).

The standard-library `sqlite3` and `tkinter` modules are used; python.org
Windows installers include both.

## Required Environment: `PYTHONUTF8=1`

Treat UTF-8 mode as a requirement, not a nicety. The engine's log lines use
`Δ`, `→`, `×` and friends, and Python on Windows still defaults stdio to the
legacy ANSI code page. *Measured:* without it, **87** fast-tier tests fail,
almost all as
`UnicodeEncodeError('charmap', "[NUTS] books-vs-metal: ... (worst Δ=373)")`;
with it, those 87 drop to the handful of named Windows skips listed below.

```powershell
$env:PYTHONUTF8 = '1'          # current session
setx PYTHONUTF8 1              # persistently, new shells
```

## Optional

- **pytest-xdist** — parallel test runs (`python -m pip install pytest-xdist`).
- **Ninja** — the simplest single-configuration build
  (`python -m pip install ninja`).
- **Node.js** — executes the JS front end's display geometry in
  `test_web_js_port.py`; without node those tests skip.
- **A Scala 3 toolchain** — executes the Scala.js port's logic in
  `test_web_scala_port.py` (optional by design: `BUDA_SCALA_PORT_TEST`
  unset/`auto` = run if found, `1` = required, `0` = off). Its classpath
  handling uses `os.pathsep`, so a Windows `BUDA_SCALA_CP` works. Provision
  without sbt: `mvn dependency:get -Dartifact=org.scala-lang:scala3-compiler_3:3.3.4`.

## Bundled

SQLite is vendored (`src/sqlite3.c/h`) — no system SQLite. *Measured:* it
compiles clean under MSVC. There is no external EDA-library dependency.

## CMake Options on Windows

| option | Windows behavior |
|---|---|
| (optimization) | `/O2 /fp:precise` — `/fp:precise` is the FP-determinism twin of the GCC side's `-ffp-contract=off` |
| `BUDA_ARCH` | **not applicable** — it drives `-march` on the non-MSVC branch only |
| `BUDA_ASSERTS` (default ON) | keeps `assert()` active via `/UNDEBUG`; the resulting per-file warning `D9025: overriding '/DNDEBUG' with '/UNDEBUG'` is **expected and benign** |
| `BUDA_PROFILE` | supported: `/Z7` + `/DEBUG` (embedded debug info) |
| `BUDA_SANITIZE` | **hard-errors on MSVC by design** (GCC-only; MSVC has no ASAN shared-module story) |

## Windows-Specific Facts About This Codebase

All discovered by the validation runs; each is now handled in-tree.

- **`buda_core` symbol export.** ELF exports everything by default; a Windows
  DLL exports nothing, and MSVC emits no import `.lib` for an export-less DLL.
  `CMakeLists.txt` sets `WINDOWS_EXPORT_ALL_SYMBOLS` on `buda_core` — without
  it the extension modules fail to link (`LNK1181: cannot open input file
  'buda_core.lib'`, *measured*).
- **DLL search.** There is no RPATH on Windows; the import works because
  `buda_core.dll` and the `.pyd` files are emitted into the **same directory**
  (both generators do this, *measured*), and CPython searches an extension's
  own directory for its DLL dependencies.
- **MinGW artifacts are self-contained.** Same-directory search does NOT
  extend to the MSYS2 GCC runtime DLLs (they live in `/ucrt64/bin`, and
  CPython ≥ 3.8 does not consult PATH), so `CMakeLists.txt` static-links the
  GCC runtime into **all three** targets on MinGW, with `--exclude-libs=ALL`
  to keep the runtime's symbols out of the DLL export table (each half
  *measured* as an import failure or link failure without it — runs 13–16).
  The workflow's objdump step verifies: only `python313.dll`, `KERNEL32`,
  UCRT api-sets, and `libbuda_core.dll` are imported.
- **Cygwin needs an explicit PE export.** Cygwin gcc defines neither `_WIN32`
  nor any implicit dllexport, so pybind11's `PYBIND11_EXPORT` degrades to an
  ELF visibility attribute PE ignores; `CMakeLists.txt` pre-defines it as
  `__attribute__((dllexport))` for the module targets — which both exports
  `PyInit_<mod>` and disables ld's auto-export (whose lambda-symbol
  pathology otherwise kills the link, *measured*, runs 12–16).
- **`setvbuf` guard.** The `buda` module's stdout line-buffering
  (issue #31) is `#ifndef _WIN32`: `_IOLBF` is unsupported by the MSVC CRT and
  a size of 0 is an invalid parameter that fast-fails the process
  (`0xC0000409`) during import, *measured* — see `src/bindings.cpp`.
- **No inter-process floorplanner lock.** `fcntl` does not exist on Windows;
  `tools/floorplanner_commands.py` falls back to a no-op lock (its documented
  degraded mode). **Concurrent floorplanner sessions on one BDB are NOT
  mutually excluded on Windows.** Real locking would use `msvcrt.locking`;
  not implemented.
- **Known Windows skips** (native Windows, i.e. `sys.platform == "win32"`
  — the MSVC and MinGW lanes, not Cygwin; each marker states its measured
  reason):
  - *POSIX-only:* the file-mode round-trip in `test_bdb_edit_bus`, the stdout
    line-buffering probe in `test_log_ordering`, and the `SIGKILL`
    worker-crash recovery in `test_qor_sweep`.
  - *Linux-gated QoR goldens* (`_WIN_QOR_SKIP`, measured run 25): exact-count
    and golden placement assertions that MSVC's placement diverges from —
    ten markers (more tests where parametrized) across
    `test_nuts_placement_golden`, `test_datapath_multi_trunk_qor`,
    `test_flow_scripts`, `test_refine_selection`, `test_bit_antenna_audit`,
    `test_cell_layer_reserve`, `test_heal_shorts` and `test_render_design`
    (`grep -rn _WIN_QOR_SKIP test/tests` lists them). Only the marked tests
    skip; the rest of each module still runs.

  Everything else in the fast tier passes, *measured*.
- **Visualization IPC is Unix-only.** `tools/viz_ipc.py` uses `AF_UNIX` under
  `/tmp`; CPython does not expose `AF_UNIX` on Windows. Core build, tests,
  CLI flows and the web backend do not depend on it.

## The `python3` Lookup, Precisely

`CMakeLists.txt` asks the `python3` on PATH where pybind11's CMake files are.
It finds that interpreter with `find_program` (PATH only, searched afresh at
every configure) and runs it by absolute path, with a 60s `TIMEOUT`; the
lookup is scoped under CMP0109 NEW, so only an *executable* `python3` is
taken, never a readable-but-not-executable stray ahead of the real one. (The
absolute path avoids the Cygwin lost-child hang measured in runs 40–45,
WINDOWS_BUILD.md §6; the executable rule avoids a `Permission denied`
reproduced in review with a `chmod 644` `python3` first on PATH.) The
failure is still soft: with no `python3` on PATH the lookup is skipped with a
`-- BUDA: no python3 on PATH ...` status line, and a non-zero result is
printed rather than swallowed; either way configure falls through to
`find_package(pybind11)` with `PYBIND11_FINDPYTHON`.
*Measured:* on an environment whose Python provides a `python3.exe` shim
(GitHub's setup-python, conda), configure just works — no `-Dpybind11_DIR`
needed. A python.org installer provides no `python3.exe`, and worse, Windows
ships a `python3` App-Execution-Alias that opens the Microsoft Store; if
configure cannot find pybind11, pass
`-Dpybind11_DIR="$(python -m pybind11 --cmakedir)"` (see WINDOWS_BUILD.md
troubleshooting).

## Wrappers: Bash On POSIX, PowerShell On Native Windows

The launcher/build wrappers live in **`bin/`** twice: the Bash originals
(`bin/bb`, `bin/buda`, `bin/fp`, `bin/activate`, …) for POSIX shells
(Linux, macOS, Cygwin), and **PowerShell twins** (`bin/bb.ps1`,
`bin/buda.ps1`, `bin/activate.ps1`, …) for native Windows — where `bash`
resolves to the System32 WSL stub and the Bash originals are unrunnable
(*measured*, windows-validate run 18). Dot-source `bin/activate.ps1` to get
the bare names (`bb`, `buda`, …) as session functions plus PATH/PYTHONPATH,
or run the `.ps1` files directly. `bb.ps1` needs the MSVC environment for
its Ninja build, exactly like the documented Ninja path. Deliberate gaps:
the macOS `.app` relaunch branches and `bb web` (sbt) are not ported. The
explicit `cmake`/`pytest`/`python` commands in
[WINDOWS_BUILD.md](WINDOWS_BUILD.md) remain the reference path.
