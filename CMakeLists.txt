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
    add_compile_options(-O3 -march=native -ffp-contract=off -Wall -Wextra -fPIC)
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
)
target_include_directories(buda_core PUBLIC src)
set_target_properties(buda_core PROPERTIES
    INSTALL_RPATH "${PLATFORM_RPATH}"
    BUILD_RPATH   "${CMAKE_CURRENT_BINARY_DIR}"
)

# ── buda_db: standalone database-layer module ─────────────────────────────
# Registers BDB, row types, BustermGen, HierBusterm.  Can be imported without
# the routing pipeline.  buda_cli.py imports buda which in turn imports buda_db.
set(BUDA_DB_FILES
    src/bindings_db.cpp
    src/bind_db.cpp
)
pybind11_add_module(buda_db ${BUDA_DB_FILES})
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
    src/layering.cpp
    src/congestion_planner.cpp
    src/nuts.cpp
    src/routing_grid.cpp
    src/detailed_nuts.cpp
    src/floorplanner.cpp
    src/placement_optimizer.cpp
    # ── v3 ───────────────────────────────────────────────────────
    src/verify.cpp
    src/bind_optimizer.cpp
    # bundler.cpp, bundle_refiner.cpp, bdb.cpp, busterm.cpp, sqlite3.c:
    # provided by buda_core via linker
)
pybind11_add_module(buda ${SOURCE_FILES})
target_include_directories(buda PRIVATE src)
target_link_libraries(buda PRIVATE buda_core)
set_target_properties(buda PROPERTIES
    INSTALL_RPATH "${PLATFORM_RPATH}"
    BUILD_RPATH   "${CMAKE_CURRENT_BINARY_DIR}"
)

install(TARGETS buda buda_db buda_core DESTINATION build)
