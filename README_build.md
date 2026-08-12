# BUDA Build Guide

The recommended way to build BUDA is using the repo-root build wrapper:

```bash
# Sourcing bin/activate sets up PATH and PYTHONPATH (once per shell):
source bin/activate

# Incremental build into build/
bb

# Clean rebuild
bb --clean

# Build and run fast tests (~8s)
bb test
```

## Manual CMake Build

Alternatively, build manually using CMake from the repository root:

```bash
mkdir -p build && cd build
cmake ..
make -j4
```

All build artifacts (e.g. `libbuda_core.so`, `buda_db`, `buda`) remain in `build/`.

## Installing with pip

A checkout is also installable with the standard Python tooling, which puts the
`buda`, `buda-fp` and `buda-viz` commands on your PATH and makes `import buda`
work with nothing on `PYTHONPATH`:

```bash
pip install .                 # into the active environment
pip install -e .              # editable: the Python layer stays live
```

This is a convenience for *using* BUDA, not the development path — `bb` above
is still that, and is unaffected by a pip install. Two differences worth
knowing before you rely on it: the pip build pins a portable instruction-set
baseline (`BUDA_ARCH=none`) rather than `bb`'s `-march=native`, so exact
overlap/opens counts can differ between the two; and in an editable install a
Python edit takes effect immediately while a C++ edit needs `pip install -e .`
again. Both are explained in [docs/internal/packaging.md](docs/internal/packaging.md).

For more details, see [CLAUDE.md](CLAUDE.md) or [GEMINI.md](GEMINI.md).