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
For more details, see [CLAUDE.md](CLAUDE.md) or [GEMINI.md](GEMINI.md).