cmake_minimum_required(VERSION 3.15)
project(buda VERSION 3.0.0 LANGUAGES CXX C)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)
if(APPLE)
    set(PLATFORM_RPATH "@loader_path")
else()
    set(PLATFORM_RPATH "$ORIGIN")
endif()

if(MSVC)
    add_compile_options(/O2 /fp:precise)
else()
    # -ffp-contract=off disables FMA contraction so the double-based congestion /
    # NUTS math rounds the same regardless of which fused-multiply-add / vector
    # instructions -march=native selects.  Without it, exact overlap/opens counts
    # drift across CPU microarchitectures and machine-specific golden assertions
    # (e.g. big2's DNUTS-open baseline) fail on a different host than they were
    # validated on.  Kept alongside -march=native (perf) — tests also assert
    # bounds rather than exact goldens as a second line of defence.
    # Target ISA.  "native" is the developer default (fastest on the machine
    # doing the build).  CI must NOT use it: hosted runners are a heterogeneous
    # fleet, so "native" means a different instruction set per run, and the
    # placement goldens above are exactly what that perturbs -- a golden
    # generated on one runner can fail on the next for no code reason.  Set
    # -DBUDA_ARCH=x86-64-v2 (or another explicit baseline) to pin it, or
    # -DBUDA_ARCH=none to omit -march entirely.
    set(BUDA_ARCH "native" CACHE STRING "-march= value; 'none' omits the flag")
    add_compile_options(-O3 -ffp-contract=off -Wall -Wextra -fPIC)
    if(NOT BUDA_ARCH STREQUAL "none")
        add_compile_options(-march=${BUDA_ARCH})
    endif()
    if(BUDA_ARCH STREQUAL "none")
        message(STATUS "BUDA target ISA: -march omitted (BUDA_ARCH=none)")
    else()
        message(STATUS "BUDA target ISA: -march=${BUDA_ARCH}")
    endif()
endif()

# assert()s are cheap correctness guards (e.g. ConnTopology's slide-window
# invariants) and we keep them ON by default.  This project always optimizes
# (-O3 above) regardless of build type, so CMAKE_BUILD_TYPE only controls
# whether -DNDEBUG is added (Release/RelWithDebInfo/MinSizeRel compile asserts
# OUT).  That choice was leaking from each build dir's *cached* CMAKE_BUILD_TYPE,
# so the same source asserted in one checkout and silently skipped the guard in
# another.  Make the policy explicit and cache-independent: the -U/-D below is a
# directory compile option, emitted AFTER the per-config flags, so it wins over
# any -DNDEBUG a build type adds.  Opt into an assert-free build with
# -DBUDA_ASSERTS=OFF.
option(BUDA_ASSERTS "Keep assert() active regardless of build type" ON)
if(MSVC)
    add_compile_options($<IF:$<BOOL:${BUDA_ASSERTS}>,/UNDEBUG,/DNDEBUG>)
else()
    add_compile_options($<IF:$<BOOL:${BUDA_ASSERTS}>,-UNDEBUG,-DNDEBUG>)
endif()

# Opt-in sanitized build for bug hunts / CI sweeps (2026-07 audit pre-pass):
#   cmake -B build-san -DBUDA_SANITIZE=ON && cmake --build build-san
#   LD_PRELOAD=$(gcc -print-file-name=libasan.so) \
#     ASAN_OPTIONS=detect_leaks=0 PYTHONPATH=build-san:src python3 …
# Lessons this option encodes (see docs/internal/audit_2026-07.md,
# "Environment notes"):
#   - NO LTO in sanitized builds: GCC 13.3 miscompiles hashtable
#     equal_range when LTO objects and the link use different opt levels
#     (the "PRE-1" segfault) — NO_EXTRAS below keeps pybind from adding
#     its own IPO, so compile and link stay plain and consistent.
#   - KEEP SYMBOLS: pybind's post-build strip would gut the sanitizer
#     stack traces (NO_EXTRAS skips it); -g is added for triage.
#   - -fno-sanitize-recover=null makes null-deref UB fatal rather than a
#     log line, so a suite run fails loudly at the defect.
# Note: the LD_PRELOAD approach CHECK-fails on the first C++ throw from a
# dlopen'd module on gcc-13/glibc — usable for throw-free repro scripts
# and the corpus flows, not for the full pytest suite (see the audit doc).
option(BUDA_SANITIZE "Build with ASAN+UBSAN (no LTO, symbols kept)" OFF)
if(BUDA_SANITIZE)
    # GCC only (validated).  Clang statically links the sanitizer runtime
    # into EXECUTABLES, so instrumented shared modules are left with
    # unresolved __asan_*/__ubsan_* symbols that the documented libasan
    # preload does not cover — the import fails before a sanitized run can
    # start.  A Clang port needs -shared-libsan (plus preloading
    # libclang_rt.asan-<arch>.so) and has not been validated; fail LOUD
    # here rather than configure a build that cannot import (Codex P2 on
    # PR #336).  MSVC has no ASAN shared-module story either.
    if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        message(FATAL_ERROR
            "BUDA_SANITIZE is validated with GCC only (found "
            "${CMAKE_CXX_COMPILER_ID}). Configure with g++, or port the "
            "Clang path (-shared-libsan + libclang_rt.asan preload) and "
            "validate an instrumented import before relaxing this check.")
    endif()
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer
                        -fno-sanitize-recover=null -g)
    add_link_options(-fsanitize=address,undefined)
    set(BUDA_PYBIND_EXTRAS NO_EXTRAS)
else()
    set(BUDA_PYBIND_EXTRAS "")
endif()

# Opt-in profiling build (2026-07 planner-runtime work): identical -O3
# codegen with symbols KEPT for stack-sampling profilers (gdb batch bt,
# perf) — pybind's post-build strip would gut them (NO_EXTRAS skips it),
# -g adds line info, frame pointers make sampled stacks walkable:
#   cmake -B build-prof -DBUDA_PROFILE=ON && cmake --build build-prof -j
#   PYTHONPATH=build-prof:src python3 …   (then sample with gdb/perf)
# Composes with BUDA_SANITIZE (which already keeps symbols).
option(BUDA_PROFILE "Keep symbols + debug info for profilers (still optimized)" OFF)
if(BUDA_PROFILE AND NOT BUDA_SANITIZE)
    if(MSVC)
        # /Z7 embeds the debug info in the objects (no PDB juggling for a
        # throwaway profiling build); MSVC profilers unwind via that info,
        # so no frame-pointer flag is needed (and /Oy- is x86-only noise).
        add_compile_options(/Z7)
        add_link_options(/DEBUG)
    else()
        add_compile_options(-g -fno-omit-frame-pointer)
    endif()
    set(BUDA_PYBIND_EXTRAS NO_EXTRAS)
endif()

execute_process(
    COMMAND python3 -c "import pybind11; print(pybind11.get_cmake_dir())"
    OUTPUT_VARIABLE PYBIND11_CMAKE_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
list(APPEND CMAKE_PREFIX_PATH "${PYBIND11_CMAKE_DIR}")
set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 REQUIRED)

# SQLite amalgamation is C, not C++
set_source_files_properties(src/sqlite3.c PROPERTIES LANGUAGE C)

# ── libbuda_core: shared C++ implementation library ───────────────────────
# Contains BDB, BustermGen, Bundler, BundleRefiner — the types that pybind11
# must register in exactly one module (buda_db).  Both Python extensions link
# against this library so they share a single compiled copy and therefore a
# single std::type_info per class.  This is what allows pybind11 to correctly
# pass buda.BDB objects into buda C++ functions that accept BDB& (e.g.
# FloorplannerEngine::write_bdb) without a type-info mismatch / segfault.
add_library(buda_core SHARED
    src/bdb.cpp
    src/sqlite3.c
    src/busterm.cpp
    src/bundler.cpp
    src/bundle_refiner.cpp
    src/gds_io.cpp
)
target_include_directories(buda_core PUBLIC src)
set_target_properties(buda_core PROPERTIES
    INSTALL_RPATH "${PLATFORM_RPATH}"
    BUILD_RPATH   "${CMAKE_CURRENT_BINARY_DIR}"
)
if(WIN32)
    # ELF exports every symbol by default; a Windows DLL exports NOTHING unless
    # told to — and MSVC does not even emit the import .lib for an export-less
    # DLL, so the extension modules fail to link with
    #   LNK1181: cannot open input file 'buda_core.lib'
    # The sources carry no __declspec annotations (this is a Linux/macOS-first
    # tree), so generate a .def exporting all symbols instead.  Measured on
    # windows-2022 / MSVC 19.44: without this the build dies at [26/30]; the
    # ~64K-exports .def limit is nowhere near reached.  See docs/WINDOWS_REQ.md.
    set_target_properties(buda_core PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
endif()

# ── buda_db: standalone database-layer module ─────────────────────────────
# Registers BDB, row types, BustermGen, HierBusterm.  Can be imported without
# the routing pipeline.  buda_cli.py imports buda which in turn imports buda_db.
set(BUDA_DB_FILES
    src/bindings_db.cpp
    src/bind_db.cpp
)
pybind11_add_module(buda_db ${BUDA_PYBIND_EXTRAS} ${BUDA_DB_FILES})
target_include_directories(buda_db PRIVATE src)
target_link_libraries(buda_db PRIVATE buda_core)
set_target_properties(buda_db PROPERTIES
    INSTALL_RPATH "${PLATFORM_RPATH}"
    BUILD_RPATH   "${CMAKE_CURRENT_BINARY_DIR}"
)

# ── buda: full routing-pipeline module ───────────────────────────────────
# Imports BDB types from buda_db (registered once there).  Links against
# buda_core so that BDB C++ symbols come from the same shared object as
# buda_db, giving pybind11 matching type_info pointers for cross-module
# argument conversion.
set(SOURCE_FILES
    src/bindings.cpp
    src/bind_bundler.cpp
    src/bind_routing.cpp
    src/bind_nuts.cpp
    # ── as-is from v2 ─────────────────────────────────────────────
    src/topology.cpp
    src/conn_topology.cpp
    src/topology_analysis.cpp
    src/topo_edit.cpp
    src/layering.cpp
    src/congestion_planner.cpp
    src/nuts.cpp
    src/nuts_dogleg.cpp
    src/routing_grid.cpp
    src/detailed_nuts.cpp
    src/trial_sweep.cpp
    src/floorplanner.cpp
    src/placement_optimizer.cpp
    # ── v3 ───────────────────────────────────────────────────────
    src/verify.cpp
    src/bind_optimizer.cpp
    # bundler.cpp, bundle_refiner.cpp, bdb.cpp, busterm.cpp, sqlite3.c:
    # provided by buda_core via linker
)
pybind11_add_module(buda ${BUDA_PYBIND_EXTRAS} ${SOURCE_FILES})
target_include_directories(buda PRIVATE src)
target_link_libraries(buda PRIVATE buda_core)
set_target_properties(buda PROPERTIES
    INSTALL_RPATH "${PLATFORM_RPATH}"
    BUILD_RPATH   "${CMAKE_CURRENT_BINARY_DIR}"
)

if(MINGW)
    # Native CPython does not search PATH for an extension's DLL dependencies
    # (Python >= 3.8), so a mingw-built buda_core.dll must not depend on the
    # GCC runtime DLLs living in the MSYS2 tree.  Measured (doc-validation run
    # 13): the UCRT64 build succeeds and the import dies with "Could not find
    # module 'libbuda_core.dll' (or one of its dependencies)" -- the
    # dependencies being libstdc++-6/libgcc_s_seh-1/libwinpthread-1 from
    # /ucrt64/bin.  ALL THREE artifacts need the treatment: run 14 measured
    # that buda_core alone is not enough — the import then dies one layer up,
    # inside buda_db.pyd (650KB: visibly no static runtime; the earlier
    # assumption that pybind11 does this for its module targets was wrong).
    foreach(_tgt buda_core buda_db buda)
        target_link_options(${_tgt} PRIVATE
            -static-libgcc -static-libstdc++
            "LINKER:-Bstatic,--whole-archive" "LINKER:-lwinpthread"
            "LINKER:--no-whole-archive" "LINKER:-Bdynamic")
    endforeach()
endif()

if(CYGWIN)
    # Cygwin ld auto-exports EVERY symbol of a DLL by default, and on the
    # pybind11 modules that trips a binutils pathology on lambda-heavy
    # template instantiations:
    #   ld: cannot export _ZZN8pybind11...: symbol wrong type (4 vs 3)
    # (measured: doc-validation run 12, gcc 14 on cygwin64, at the final link
    # of buda.cpython-39-x86_64-cygwin.dll).  The extension modules need no
    # auto-export at all — their one required export, PyInit_<mod>, carries
    # __declspec(dllexport) via PYBIND11_EXPORT (pybind11 defines it so for
    # _WIN32 || __CYGWIN__), and explicit dllexports survive
    # --exclude-all-symbols.  buda_core stays on default auto-export: its
    # symbols are what the modules link against, and it links fine.
    target_link_options(buda    PRIVATE "LINKER:--exclude-all-symbols")
    target_link_options(buda_db PRIVATE "LINKER:--exclude-all-symbols")
endif()

install(TARGETS buda buda_db buda_core DESTINATION build)
