# BUDA Windows Requirements

This document describes the software requirements for building and testing BUDA
in a native Windows environment with MSVC. It documents the current project
state; some Windows support gaps are still called out explicitly.

For the cross-platform dependency reference (required / optional / bundled,
versions, purposes) see [build_test_dependencies.md](build_test_dependencies.md);
this page is the Windows-specific companion.

## Target Environment

The recommended native Windows environment is:

- Windows 10 or Windows 11, 64-bit.
- Visual Studio 2022 Build Tools or full Visual Studio 2022.
- Native MSVC compiler toolchain, not MinGW.
- 64-bit Python matching the selected build architecture.
- PowerShell for command examples.

WSL, MSYS2, Git Bash, and MinGW may be useful development environments, but they
are not the target described here.

## Required Software

### Visual Studio / MSVC

Install Visual Studio 2022 Build Tools or full Visual Studio 2022 with:

- `Desktop development with C++` workload.
- MSVC v143 compiler toolset.
- Windows 10 or Windows 11 SDK.
- C++ CMake tools for Windows, or install CMake separately.

BUDA builds a C++20 Python extension module through CMake and pybind11, so the
Python interpreter, compiler, and generated extension must all use the same
architecture. Use 64-bit Python with the x64 MSVC build.

### CMake

BUDA requires CMake 3.15 or newer:

```text
cmake_minimum_required(VERSION 3.15)
```

CMake 3.24 or newer is recommended for a smoother Python and Visual Studio
generator experience.

### Python

Use a 64-bit Python installation. Python 3.10 through 3.13 are reasonable
targets for the current codebase.

Recommended options:

- python.org CPython x64.
- Miniforge or Conda x64, if Tk/Tcl is installed and working.

The project currently assumes Python is available for:

- Locating pybind11 during CMake configure.
- Building the `buda` Python extension.
- Running the BUDA CLI in `src/buda_cli.py`.
- Running pytest and pytest-bdd tests.
- Running GUI tools based on tkinter and Matplotlib.

### Python Packages

Install these packages in the active Python environment:

```powershell
python -m pip install pybind11 pytest pytest-bdd matplotlib numpy
```

Package purposes:

- `pybind11`: C++/Python extension binding and CMake integration.
- `pytest`: Python test runner.
- `pytest-bdd`: Gherkin/Cucumber-style behavior tests.
- `matplotlib`: visualization and GUI plotting.
- `numpy`: array math for the visualizer (and a matplotlib dependency).

The Python standard library modules `sqlite3` and `tkinter` are also used.
`sqlite3` is normally included with CPython. `tkinter` requires a Python build
with Tcl/Tk support.

### Optional pytest-xdist (parallel tests)

`pytest-xdist` parallelizes the heavier test tiers (integration/pipeline tests):

```powershell
python -m pip install pytest-xdist
```

The `bb` wrapper that enables this automatically is a Bash script (see the
portability note below), so on native Windows run pytest directly with the same
flags:

```powershell
python -m pytest -m "not slow" -n auto --dist loadfile
```

`--dist loadfile` keeps each file on one worker so tests that share a fixture
path can't race across workers. See
[internal/test/parallelism.md](internal/test/parallelism.md).

### Optional Ninja

Ninja is optional but recommended for a simple single-configuration build:

```powershell
python -m pip install ninja
```

You can also install Ninja through Visual Studio, Chocolatey, Scoop, or another
Windows package manager.

### Git

Git is required to clone and manage the source tree. Git for Windows is
sufficient.

## Bundled Dependencies

SQLite is vendored in the repository:

- `src/sqlite3.c`
- `src/sqlite3.h`

No external SQLite development package is required for the core build.

## Current Windows Portability Notes

These are the main known issues to account for when building natively on
Windows.

### `python3` Lookup

`CMakeLists.txt` currently locates pybind11 by running `python3`. Native Windows
installations commonly provide `python` or the `py` launcher instead of
`python3`.

If configure fails with `python3` not found, either:

- Use an environment where `python3` resolves to the desired interpreter.
- Pass `pybind11_DIR` manually.
- Update the CMake logic to use CMake's discovered Python interpreter.

### Bash Build Wrapper

The `bb` build/test wrapper is a Bash script. It uses Unix tools and shell
features including:

- `make`
- `sysctl`
- process substitution
- `tee`
- `awk`

It is not a native PowerShell or CMD build script. Use direct CMake commands on
Windows until a native `bb.ps1` wrapper exists.

### Bash Runtime Wrapper

The top-level `buda` wrapper is also a Bash script and calls `python3`. On native
Windows, run `src/buda_cli.py` directly with Python or use a future PowerShell
wrapper.

### Extension Output Directory

Ninja usually places the compiled `buda` extension under `build`. Visual Studio
generators are multi-configuration and usually place it under `build\Release`
for release builds.

Set `PYTHONPATH` accordingly before running tests or scripts.

### GUI Requirements

GUI tools use tkinter and Matplotlib's `TkAgg` backend. If GUI startup fails,
verify that:

- `python -c "import tkinter"` succeeds.
- `python -c "import matplotlib; matplotlib.use('TkAgg')"` succeeds.
- The active Python environment has Tcl/Tk installed.

### Visualization IPC

`tools/viz_ipc.py` currently uses a Unix-style socket path under `/tmp`. This is
not ideal for native Windows. Core build and non-GUI tests may still work, but
interactive visualization IPC should be treated as a portability area until it
is converted to a Windows-friendly transport or path strategy.

