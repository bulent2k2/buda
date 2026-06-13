cmake_minimum_required(VERSION 3.15)
project(buda VERSION 3.0.0 LANGUAGES CXX C)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
    add_compile_options(/O2)
else()
    add_compile_options(-O3 -march=native -Wall -Wextra -fPIC)
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

set(SOURCE_FILES
    src/bindings.cpp
    src/bind_db.cpp
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
    # ── v3 new / evolved ─────────────────────────────────────────
    src/bdb.cpp
    src/sqlite3.c
    src/busterm.cpp
    src/bundler.cpp
    src/bundle_refiner.cpp
    src/verify.cpp
)

# SQLite amalgamation is C, not C++
set_source_files_properties(src/sqlite3.c PROPERTIES LANGUAGE C)

# ── buda_db: standalone database-layer module ─────────────────────────────
# Contains BDB, row types, BustermGen, HierBusterm.  Can be imported without
# the routing pipeline.  buda_cli.py imports both buda_db and buda.
set(BUDA_DB_FILES
    src/bindings_db.cpp
    src/bind_db.cpp
    src/bdb.cpp
    src/sqlite3.c
    src/busterm.cpp
)
pybind11_add_module(buda_db ${BUDA_DB_FILES})
target_include_directories(buda_db PRIVATE src)

# ── buda: full routing-pipeline module ───────────────────────────────────
# Includes everything; bind_db.cpp is re-compiled here so the buda module
# is self-contained and can also be used without buda_db being imported.
pybind11_add_module(buda ${SOURCE_FILES})
target_include_directories(buda PRIVATE src)

install(TARGETS buda buda_db DESTINATION src)
