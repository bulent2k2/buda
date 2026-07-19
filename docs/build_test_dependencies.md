# Build & Test Dependencies

Everything needed to build BUDA (C++ core → Python extension) and run its test
suite, split into **required**, **optional**, and **bundled** (nothing to
install). BUDA has **no external EDA-library dependency** — the DEF/LEF/Verilog/
GDS importers and SQLite are all in-tree.

## Required — build

The C++20 core is compiled into two pybind11 extension modules (`buda_db`,
`buda`) linking one shared `buda_core`; see the build table in `CLAUDE.md`.

| Dependency | Version | Notes |
|---|---|---|
| **C++20 compiler** | GCC 10+ / Clang 12+ / MSVC 2019+ | `CMAKE_CXX_STANDARD 20`, required. |
| **CMake** | ≥ 3.15 | `cmake_minimum_required(VERSION 3.15)`. |
| **Python** (+ dev headers) | 3.11+ (project targets 3.13+) | Host interpreter for the extension modules; needs `Python.h`. |
| **pybind11** | any recent | `find_package(pybind11 REQUIRED)`; located via `python3 -c "import pybind11"`. |

Compiler flags (from `CMakeLists.txt`): `-O3 -march=native -ffp-contract=off
-Wall -Wextra -fPIC` on GCC/Clang (`/O2 /fp:precise` on MSVC). `-ffp-contract=off`
is deliberate — it keeps the double-based congestion/NUTS math bit-reproducible
across FMA-capable CPUs so the golden-placement tests stay exact (see the
comment in `CMakeLists.txt` and `docs/internal/test/`).

## Required — runtime & tests

| Dependency | pip name | Used by |
|---|---|---|
| **NumPy** | `numpy` | matplotlib; and directly in `buda_viz` (home-fit geometry). |
| **matplotlib** | `matplotlib` | the visualizer (`buda_viz`) and the Floorplanner GUI. |
| **Tk** | *(system Tk; `tkinter` ships with Python)* | interactive GUI windows (viz, Floorplanner). Tests use the headless **Agg** backend, so Tk is **not** needed to run the suite — only for live windows. |
| **pytest** | `pytest` | the whole test suite. |
| **pytest-bdd** | `pytest-bdd` | the `.feature` files under `test/tests/features/`. |

SQLite is **bundled** — see below — so there is no `sqlite3`/`libsqlite` to
install.

## Optional

| Dependency | pip name | Enables | Without it |
|---|---|---|---|
| **pytest-xdist** | `pytest-xdist` | parallel `bb -m` / `bb -s` (`-n auto --dist loadfile`, ~3× on 4 cores). | Tiers run serially; `bb` prints a hint. See [internal/test/parallelism.md](internal/test/parallelism.md). |
| **pyobjc (Cocoa)** | `pyobjc-framework-Cocoa` | **macOS only** — names the app in the Dock / menu bar / Cmd-Tab after the design and swaps the Dock icon (`Foundation.NSProcessInfo` / `NSBundle`, `AppKit.NSApplication`). | The GUI still runs; it just shows as `python3`. Every call is guarded. See [internal/macos_app_bundles.md](internal/macos_app_bundles.md). |
| **setproctitle** | `setproctitle` | sets the process title (`ps`/`top`) to the design name. | No process-title rename; guarded no-op. |

## Bundled (nothing to install)

- **SQLite** — the amalgamation `src/sqlite3.c` / `sqlite3.h` is compiled into
  `buda_core`. The BDB (physical-design database) is SQLite; no system library
  or `pysqlite` is used.
- The **DEF / LEF / Verilog / GDSII** importers and exporters are hand-written
  in-tree (`bdb.cpp`, `gds_io.cpp`) — no OpenDB / Si2 / Cadence dependency. The
  OpenAccess bridge (roadmap) would be the only externally-gated interchange.

## Install cheat-sheet

```bash
# Required to build + test:
pip install pybind11 numpy matplotlib pytest pytest-bdd

# Optional:
pip install pytest-xdist                 # parallel test runs (any OS)
pip install pyobjc-framework-Cocoa       # macOS: name the app in the Dock/menu bar
pip install setproctitle                 # nicer `ps`/`top` process title

# System packages (examples): a C++20 compiler, CMake ≥3.15, python3-dev, and
# Tk for live GUIs — e.g. Debian/Ubuntu:
#   apt install build-essential cmake python3-dev python3-tk
```

## Reference versions

Known-good on the CI/dev hosts (treat as a floor, not a pin):

| | Version |
|---|---|
| Python | 3.11.15 (project targets 3.13+) |
| matplotlib | 3.11 |
| NumPy | 2.4 |
| pytest | 9.1 |
| pytest-xdist | 3.8 |
| CMake | ≥ 3.15 |

Regenerate with:

```bash
python3 -c "import sys,matplotlib,numpy,pytest; \
print('py',sys.version.split()[0],'mpl',matplotlib.__version__, \
'numpy',numpy.__version__,'pytest',pytest.__version__)"
```

## Sanitized builds (`BUDA_SANITIZE`)

Opt-in ASAN+UBSAN instrumentation for bug hunts (OFF by default; the normal
build is untouched):

```bash
cmake -S . -B build-san -DBUDA_SANITIZE=ON
cmake --build build-san -j4
LD_PRELOAD=$(gcc -print-file-name=libasan.so) \
  ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=print_stacktrace=1 \
  PYTHONPATH=build-san:src python3 src/buda_cli.py flow/… --no-viz
```

The option deliberately disables pybind's per-module LTO and post-build
strip (`NO_EXTRAS`) and adds `-g`: mixed compile/link optimization levels
under LTO miscompile libstdc++ hashtables on GCC 13 (the audit's "PRE-1"
artifact), and a stripped module has no sanitizer stack traces. Caveat: the
`LD_PRELOAD` interceptor CHECK-fails on the first C++ exception thrown from
a dlopen'd module on gcc-13/glibc, so drive sanitized runs through
throw-free repro scripts and corpus flows rather than the full pytest
suite. Background: `docs/internal/audit_2026-07.md` (Environment notes).
GCC only: configuring with Clang fails fast — Clang's static-by-default
sanitizer runtime leaves instrumented shared modules with unresolved
`__asan_*`/`__ubsan_*` symbols at import (a port needs `-shared-libsan`
plus preloading `libclang_rt.asan-<arch>.so`, unvalidated here).
