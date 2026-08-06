# Building BUDA On Windows

Configure, build, and test BUDA natively on Windows with MSVC. Requirements
and Windows-specific background: [WINDOWS_REQ.md](WINDOWS_REQ.md).

**Every command below is executed on a real Windows machine** by
`.github/workflows/windows-validate.yml` (windows-2022 runner, VS 2022, MSVC
19.44, Python 3.13 x64), through both build paths, the import, the fast test
tier, and a `.buda` flow. Re-validate any time: *Actions → Windows validation
→ Run workflow*. Last green: run 9, 2026-08-06 — build ≈ 2m30s, fast tier
≈ 60s per path on a 4-core runner.

## 1. Open A Developer Shell

Any shell that can find MSVC and the Windows SDK:

- `x64 Native Tools Command Prompt for VS 2022`, or
- `Developer PowerShell for VS 2022`, or
- a plain shell after `vcvars64.bat` (the validation workflow does this:
  locate VS with `vswhere.exe`, then `call ...\VC\Auxiliary\Build\vcvars64.bat`).

The **Visual Studio generator path (section 4) needs no developer shell** —
CMake locates MSVC itself (measured). Ninja does need one.

Verify: `cl`, `cmake --version`, `python --version`.

## 2. Python Environment

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

## 3. Build With Ninja  *(recommended: single-config, simplest paths)*

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

## 4. Build With The Visual Studio Generator

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

## 5. What To Expect From The Test Tier

Measured on run 8/9 of the validation: the fast tier passes (~1780 tests, 35
xfailed) with a handful of **named POSIX-only skips** — file-mode round-trip
(`test_bdb_edit_bus`), the stdout line-buffering probe (`test_log_ordering`),
and `SIGKILL` crash-recovery (`test_qor_sweep`); each marker states its
measured reason. If you see *many* failures instead, check `PYTHONUTF8` first
(section 2).

## 6. Troubleshooting

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

- Ninja layout: `build` (pytest gets it free via `pytest.ini`; anything else
  needs `PYTHONPATH`).
- VS layout: `build\Release` **and** `build`, always via `PYTHONPATH`.
- If your own code spawns Python subprocesses, **prepend** to the inherited
  `PYTHONPATH` with `os.pathsep` rather than replacing it — replacing it with
  a hardcoded `build` orphaned a subprocess under the VS layout (measured;
  see `test_audit3.py` for the pattern).

### Warning spam: `D9025: overriding '/DNDEBUG' with '/UNDEBUG'`

Expected and benign — `BUDA_ASSERTS` (default ON) keeps `assert()` active
over the Release configuration. One warning per translation unit.

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

They are Bash scripts in `bin/`. Use the explicit commands in this guide.
