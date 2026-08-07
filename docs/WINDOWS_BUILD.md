# Building BUDA On Windows

Configure, build, and test BUDA natively on Windows. Four validated paths:

| # | Path | Toolchain | Python | Status (measured) |
|---|---|---|---|---|
| 1 | **MSVC + Ninja** (§3) | VS 2022, MSVC 19.44 | native 3.13 | **green** — build, import, fast tier, flow |
| 2 | **MSVC + VS generator** (§4) | VS 2022, MSVC 19.44 | native 3.13 | **green** — build, import, fast tier, flow |
| 3 | **MinGW-w64** (§5) | MSYS2 UCRT64 GCC | native 3.13 | **green** — build, import, fast tier (1819 passed), flow |
| 4 | **Cygwin64** (§6) | Cygwin GCC 14 | Cygwin 3.9 | **working, with caveats** — build (`bin/bb`), full import stack, and an end-to-end `.buda` flow all green (run 19); test tier limited by a distro matplotlib/numpy skew |

Requirements and Windows-specific background: [WINDOWS_REQ.md](WINDOWS_REQ.md).

**Every command below is executed on a real Windows machine** by
`.github/workflows/windows-validate.yml` (windows-2022 runner, VS 2022, MSVC
19.44, Python 3.13 x64), through all four paths: build, import, fast test
tier, and a `.buda` flow. Re-validate any time: *Actions → Windows validation
→ Run workflow*. Paths 1–3 last green: run 17, 2026-08-07 (MSVC build ≈
2m30s, MinGW build ≈ 2m, fast tier ≈ 46–70s per path on a 4-core runner);
path 4 import stack first green the same run.

---

## Path 1 & 2 — MSVC (the reference paths)

### 1. Open A Developer Shell

Any shell that can find MSVC and the Windows SDK:

- `x64 Native Tools Command Prompt for VS 2022`, or
- `Developer PowerShell for VS 2022`, or
- a plain shell after `vcvars64.bat` (the validation workflow does this:
  locate VS with `vswhere.exe`, then `call ...\VC\Auxiliary\Build\vcvars64.bat`).

The **Visual Studio generator path (section 4) needs no developer shell** —
CMake locates MSVC itself (measured). Ninja does need one.

Verify: `cl`, `cmake --version`, `python --version`.

### 2. Python Environment

From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install pybind11 pytest pytest-bdd matplotlib numpy ninja
python -m pip install -r src/web/requirements.txt   # NOT optional for a full test run
python -m pip install pytest-xdist                  # optional: parallel tests

$env:PYTHONUTF8 = '1'    # effectively REQUIRED — see WINDOWS_REQ.md (measured: 87 test failures without it)
```

(`.venv/` is git-ignored.) If you plan to use the GUI tools, confirm Tk:

```powershell
python -c "import tkinter; import matplotlib; matplotlib.use('TkAgg')"
```

### 3. Build With Ninja  *(recommended: single-config, simplest paths)*

```powershell
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Everything lands directly under `build\` (measured):

```text
build\buda_core.dll
build\buda_db.cp313-win_amd64.pyd
build\buda.cp313-win_amd64.pyd
```

The DLL and the `.pyd`s must stay co-located — that same-directory layout is
what lets the import find `buda_core.dll` (Windows has no RPATH).

Run the fast test tier — **no PYTHONPATH needed**: `pytest.ini` already puts
`build` and `src` on the path for this layout:

```powershell
python -m pytest -q                                   # fast tier
python -m pytest -q -m "not slow" -n auto --dist loadfile   # + mid tier, parallel
python -m pytest -q -o addopts=""                     # everything incl. slow
```

Run a `.buda` flow (here PYTHONPATH **is** needed — only pytest reads
`pytest.ini`):

```powershell
$env:PYTHONPATH = "$PWD\build;$PWD\src;$PWD\tools"
python src\buda_cli.py --no-viz flow\four_blocks.buda
```

`--no-viz` (`-nv`) suppresses the interactive matplotlib window —
`four_blocks.buda` ends in a `visualize` command, so without the flag this
opens a GUI.

### 4. Build With The Visual Studio Generator

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

Multi-config: artifacts land under `build\Release\` (measured), again
co-located. Here `PYTHONPATH` is needed for **everything**, tests included —
`pytest.ini` only covers the single-config `build` layout:

```powershell
$env:PYTHONPATH = "$PWD\build\Release;$PWD\build;$PWD\src;$PWD\tools"
python -m pytest -q
python src\buda_cli.py --no-viz flow\four_blocks.buda
```

---

## 5. Path 3 — MinGW-w64 (MSYS2 UCRT64 GCC, native CPython)

GCC producing **native Windows binaries**, driven from bash — the GCC branch
of the build (`-march`, `-ffp-contract=off`) with the native CPython, so
**every pip wheel works** (numpy, matplotlib, the web deps — no package
availability problem). Measured fully green (run 16): build ≈ 2m, import
clean, fast tier **1819 passed / 5 skipped / 35 xfailed in 46s**, flow runs.

**First, the misconception, measured:** "the mingw64 that comes with Git for
Windows" is the *shell*, not the toolchain. Git for Windows ships bash and
coreutils — **no gcc, no make, no pacman**. (On the GitHub runner, Git Bash
*does* resolve `gcc` — at `/c/mingw64/bin`, a separate toolchain the runner
image pre-installs, with ninja from Chocolatey and cmake from its own
installer; nothing under Git's tree. A dev box without those extras gets
ABSENT for all of them.) The toolchain comes from **MSYS2**.

### 5.1 Install MSYS2 and the UCRT64 toolchain

```powershell
winget install MSYS2.MSYS2          # or the installer from https://www.msys2.org
```

Then in an **MSYS2 UCRT64** shell (Start menu: "MSYS2 UCRT64"), update the
base system and install the toolchain — pacman's classic two-step ritual: the
first `-Syu` may close the window; if it does, reopen and run it again:

```bash
pacman -Syu                # core update (may ask to close the terminal; rerun after)
pacman -S --needed mingw-w64-ucrt-x86_64-gcc \
                   mingw-w64-ucrt-x86_64-cmake \
                   mingw-w64-ucrt-x86_64-ninja
```

**UCRT64, deliberately** (not MINGW64): modern CPython links the Universal
CRT, and mixing CRTs across the extension boundary is the class of silent bug
the MSVC lane needed nine validation runs to characterize. Note MSYS2 is a
rolling release — the validation measured GCC 14 one day and 16.1.0 the next;
both build clean.

### 5.2 Native CPython + packages

Use the **native Windows CPython** (python.org, `winget install
Python.Python.3.13`, or an existing install) — *not* an MSYS2 python. All
wheels install normally:

```powershell
python -m pip install --upgrade pip
python -m pip install pybind11 pytest pytest-bdd matplotlib numpy pytest-xdist
python -m pip install -r src/web/requirements.txt
```

Set `PYTHONUTF8=1` exactly as for MSVC (same measured requirement).

### 5.3 Configure, build, test

From a UCRT64 shell with the native CPython **first on PATH** (so CMake's
`python3` lookup and `PYBIND11_FINDPYTHON` resolve to the native
interpreter):

```bash
export PATH="$(cygpath -u 'C:\Path\To\Python313'):$PATH"   # native python first
cd /c/path/to/buda
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

If your python.org install provides no `python3.exe`, pass
`-Dpybind11_DIR="$(python -m pybind11 --cmakedir)"` (same fallback as the
MSVC path; the hosted-runner python provides the shim, measured).

Artifacts land under `build/` as `libbuda_core.dll` + two `.pyd`s. They are
**self-contained by design**: `CMakeLists.txt` static-links the GCC runtime
into all three targets (`-static-libgcc -static-libstdc++`, winpthread
whole-archived, `--exclude-libs=ALL`), because native CPython does not search
PATH for an extension's DLL dependencies — the MSYS2 runtime DLLs would be
invisible at import time. Measured by the workflow's objdump step: every
artifact imports only `python313.dll`, `KERNEL32`, the UCRT api-sets, and
`libbuda_core.dll` — zero MSYS2 DLLs.

Tests and flows then run from any shell, exactly as the Ninja MSVC path
(§3): `python -m pytest -q` needs no PYTHONPATH, flows need
`build;src;tools`.

---

## 6. Path 4 — Cygwin64  *(experimental)*

Windows with a POSIX personality: a real `fcntl`, a real `python3`, and —
the point of this path — **the repo's own `bin/bb` wrapper just works**, CRLF
guards aside. Unlike MinGW, binaries are Cygwin-native (`cygbuda_core.dll`,
`buda.cpython-39-x86_64-cygwin.dll`) and link Cygwin's Python.

**Measured** (validation runs 13–19): `bin/bb` drives a complete GCC 14
build (≈ 6–8m), the full extension stack **imports clean** —
`cygbuda_core.dll`, `import buda_db`, `import buda` all green on Cygwin's
Python 3.9 — and a **`.buda` flow runs end to end** (run 19:
`four_blocks.buda` through bundler → topologies → planner → NUTS →
DetailedNUTS, `check_design` clean at every stage, 0 bits unplaced).
Known, measured limitations:

- **Python is 3.9.16** — the newest Cygwin ships, past upstream EOL and
  below the project's 3.13 floor. The tree parses under 3.9 (measured: full
  `ast` sweep), and the one measured *runtime* incompatibility — PEP 604
  `X | Y` unions in evaluated annotations, which raise `TypeError` at import
  on 3.9 (run 18) — is fixed with `from __future__ import annotations` in
  every module a repo-wide AST scan finds (four: `buda_session/nutsflow.py`,
  `web/server.py`, `tools/build_hier_demo.py`, `demo/ariane/def2buda.py`).
  Nothing guards new 3.10+ constructs from landing; re-run the validation
  workflow to re-measure.
- **No pip wheels exist for Cygwin** — native deps come from `setup.exe`
  packages or not at all. numpy 2.0.1 works; **the distro matplotlib (3.5.1)
  is broken out of the box** against that numpy (`_ARRAY_API not found`) —
  an upstream packaging skew, unfixable from here.
- **Web deps are unavailable**: pydantic-core needs a Rust build via maturin,
  whose bootstrap fails under Cygwin (measured — even with a Rust toolchain
  present). The 28 web tests will always skip.
- **Run the engine single-threaded** (`--threads 1`, or `BUDA_THREADS=1` for
  pytest): the multi-threaded solve paths segfault *intermittently* under
  Cygwin (measured: run 19's flow clean, run 20's identical-source flow and
  tier both died with SIGSEGV in `run_nuts`/mid-tier at the default 2
  threads). Suspected cause is Cygwin's small default pthread stack for the
  engine's worker threads — the main thread is unaffected. Under
  investigation; single-threading is the measured-safe configuration.

### 6.1 Install

Download `setup-x86_64.exe` from https://cygwin.com and install with these
packages (GUI picker or command line):

```powershell
.\setup-x86_64.exe -q -s https://mirrors.kernel.org/sourceware/cygwin/ `
  -R C:\cygwin64 -l C:\cygpkgs `
  -P gcc-g++,make,cmake,ninja,git,python3,python39-devel,python39-pip,python39-numpy,python39-tkinter
```

### 6.2 Checkout and environment

Two CRLF guards, both required (measured):

```bash
git config --global core.autocrlf false   # BEFORE cloning: bash reads CRLF scripts poorly
set -o igncr                              # this shell: ignore stray \r in sourced files
export PYTHONUTF8=1
```

(`SHELLOPTS` itself is **readonly inside bash** — `export SHELLOPTS=igncr`
fails with `readonly variable`. `set -o igncr` enables it for the current
shell; to make child bash processes inherit it, set `SHELLOPTS=igncr` in the
*Windows* environment before bash starts, which is what the validation
workflow does via its job `env:`.)

Then the pure-python test deps via pip (those wheels are pure-python, so
they install fine):

```bash
python3 -m pip install --user pybind11 pytest pytest-bdd pytest-xdist
```

### 6.3 Build and run — the Linux instructions, plus one PATH line

```bash
cd /cygdrive/c/path/to/buda
./bin/bb                                   # the repo's own wrapper (measured green)
export PATH=$PWD/build:$PATH               # REQUIRED: see below
PYTHONPATH=$PWD/build:$PWD/src:$PWD/tools python3 src/buda_cli.py --threads 1 --no-viz flow/four_blocks.buda
BUDA_THREADS=1 python3 -m pytest -q        # expect matplotlib-dependent failures (distro skew above)
```

(`--threads 1` / `BUDA_THREADS=1` per the threading caveat above.)

The `PATH` line is the one real deviation from Linux: Cygwin's `dlopen`
follows Windows LoadLibrary search rules (application dir, system dirs,
PATH) and — unlike native CPython ≥ 3.8, which searches an extension's own
directory for its dependencies — does **not** look next to the importing
module. Without `build` on PATH the import dies with the misleading
`ImportError: No such file or directory` (measured, run 17): the *module* is
found; the dependent `cygbuda_core.dll` is what's missing.

`CMakeLists.txt` carries one Cygwin-specific fix: `PYBIND11_EXPORT` is
pre-defined as a PE `dllexport` for the module targets — Cygwin gcc defines
neither `_WIN32` nor an implicit dllexport, so without it ld auto-exports
every symbol (tripping a binutils pathology on pybind11's lambda-heavy
templates: `cannot export ...: symbol wrong type (4 vs 3)`) while the one
symbol Python actually needs, `PyInit_<mod>`, goes unexported.

---

## 7. What To Expect From The Test Tier

Measured on runs 9–16 of the validation, identical across the MSVC and MinGW
paths: the fast tier passes (~1819 tests, 35 xfailed) with a handful of
**named POSIX-only skips** — file-mode round-trip (`test_bdb_edit_bus`), the
stdout line-buffering probe (`test_log_ordering`), `SIGKILL`
crash-recovery (`test_qor_sweep`), and the `bin/buda` wrapper-arg tests
(`test_buda_wrapper_args` — native Windows' `bash` is the WSL stub, measured
run 18; they run under Cygwin); each marker states its measured reason.
If you see *many* failures instead, check `PYTHONUTF8` first (section 2).

## 8. Troubleshooting

Every entry below was actually hit during validation, in this order.

### `LNK1181: cannot open input file 'buda_core.lib'`

You are building a checkout that predates the `WINDOWS_EXPORT_ALL_SYMBOLS`
fix in `CMakeLists.txt`. A Windows DLL exports nothing by default and MSVC
emits no import library for an export-less DLL. Update, or apply
`set_target_properties(buda_core PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)`.

### `import buda` dies with exit code `-1073740791` (0xC0000409), no output

That code is `__fastfail`/`abort` from the CRT — **not** a Python exception:
nothing is catchable, `faulthandler` prints nothing, and the process's
*buffered stdout/stderr is discarded*, which can make earlier, already-
successful output vanish too and misleadingly implicate the shell. Debug such
crashes with `python -u` and per-line flushes.

On old checkouts the cause was the module init's
`setvbuf(stdout, nullptr, _IOLBF, 0)` — `_IOLBF` is unsupported by the MSVC
CRT and size 0 is an invalid parameter, which the release CRT punishes with
exactly this fast-fail. Guarded `#ifndef _WIN32` since the fix; see
`src/bindings.cpp`.

### `UnicodeEncodeError('charmap', ...)` in tests or flows

The engine logs `Δ`/`→`/`×`; Windows Python defaults stdio to the ANSI code
page. Set `PYTHONUTF8=1` (measured: this alone was 87 → 4 test failures).

### `ModuleNotFoundError: No module named 'buda'`

The compiled extension is not on the path *of the process that needs it*:

- Ninja layout (MSVC or MinGW): `build` (pytest gets it free via
  `pytest.ini`; anything else needs `PYTHONPATH`).
- VS layout: `build\Release` **and** `build`, always via `PYTHONPATH`.
- If your own code spawns Python subprocesses, **prepend** to the inherited
  `PYTHONPATH` with `os.pathsep` rather than replacing it — replacing it with
  a hardcoded `build` orphaned a subprocess under the VS layout, and a
  `':'`-joined literal fused into one bogus entry on Windows (both measured;
  see `test_audit3.py` and `test_buda_threads_flag.py` for the pattern).

### MinGW: `ImportError: DLL load failed ... The specified module could not be found`

The artifact (or one it links) depends on MSYS2 runtime DLLs
(`libstdc++-6`, `libgcc_s_seh-1`, `libwinpthread-1`) that native CPython
cannot find — it does not search PATH for extension dependencies. Current
`CMakeLists.txt` static-links the runtime into **all three** targets
(measured: fixing `buda_core` alone just moved the failure into
`buda_db.pyd`). Verify with `objdump -p <artifact> | grep 'DLL Name'`: only
`python313.dll`, `KERNEL32`, UCRT api-sets, and `libbuda_core.dll` should
appear.

### MinGW: `multiple definition of '_Unwind_Resume'` linking a module

The static runtime was linked into `buda_core.dll` but its symbols leaked
into the DLL's export table (MinGW auto-export), so a module linking the
import library *plus* its own static `libgcc_eh` sees the symbol twice.
`--exclude-libs=ALL` on the DLL is the fix (in-tree; measured).

### Cygwin: `ld: cannot export _ZZN8pybind11...: symbol wrong type (4 vs 3)`

Binutils' auto-export tripping over pybind11 lambda symbols. Auto-export
must be OFF for the module targets, which requires giving ld at least one
explicit export — in-tree this is `PYBIND11_EXPORT=__attribute__((dllexport))`
on the module targets (Cygwin gcc defines neither `_WIN32` nor any implicit
dllexport, so pybind11's default degrades to an ELF visibility attribute
that PE ignores).

### Cygwin: `ImportError: dynamic module does not define module export function (PyInit_...)`

The module built but exports nothing (the flip side of the previous entry —
e.g. `--exclude-all-symbols` without an explicit export). Same fix.

### Cygwin: `ImportError: No such file or directory` on `import buda`

Misleading: the module file exists and is found — a **dependent DLL**
(`cygbuda_core.dll`) is what's missing. Cygwin's `dlopen` searches
application dir, system dirs, and PATH — never the importing module's own
directory. Fix: `export PATH=$PWD/build:$PATH` (§6.2/6.3). Measured, run
17 — where the import-smoke diagnostic *passed* while plain
`python3`/`pytest` failed, because the diagnostic ctypes-loads the core DLL
by absolute path first and later imports piggyback on the already-loaded
copy.

### Warning spam: `D9025: overriding '/DNDEBUG' with '/UNDEBUG'`

Expected and benign — `BUDA_ASSERTS` (default ON) keeps `assert()` active
over the Release configuration. One warning per translation unit (MSVC
only; the GCC paths use `-UNDEBUG` without complaint).

### CMake cannot find pybind11 / `python3`

Configure normally just works with pip-installed pybind11 (measured — the
`python3` pre-lookup fails soft). If your environment has no `python3.exe`
(python.org installs; beware the Microsoft-Store `python3` alias), pass the
directory explicitly:

```powershell
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release `
      -Dpybind11_DIR="$(python -m pybind11 --cmakedir)"
```

### tkinter / TkAgg fails

`python -c "import tkinter"` and
`python -c "import matplotlib; matplotlib.use('TkAgg')"` must both succeed;
repair Tcl/Tk in the active Python (with conda: install `tk`).

### Architecture mismatch

x64 everywhere: x64 developer shell, `-A x64`, 64-bit Python. Mixing x86/x64
yields configure, link, or import failures.

### `bb` / `buda` "not recognized"

They are Bash scripts in `bin/`. On the MSVC and MinGW paths use the
explicit commands in this guide; under Cygwin (§6) they run as-is.
