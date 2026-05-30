cmake_minimum_required(VERSION 3.15)
project(buda VERSION 3.0.0 LANGUAGES CXX C)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
    add_compile_options(/O2)
else()
    add_compile_options(-O3 -march=native -Wall -Wextra -fPIC)
endif()

find_package(pybind11 REQUIRED)

set(SOURCE_FILES
    src/bindings.cpp
    # ── as-is from v2 ─────────────────────────────────────────────
    src/topology.cpp
    src/conn_topology.cpp
    src/layering.cpp
    src/congestion_planner.cpp
    src/nuts.cpp
    src/routing_grid.cpp
    src/detailed_nuts.cpp
    # ── v3 new / evolved ─────────────────────────────────────────
    src/bdb.cpp
    src/sqlite3.c
    src/busterm.cpp
    src/bundler.cpp
    src/bundle_refiner.cpp
)

# SQLite amalgamation is C, not C++
set_source_files_properties(src/sqlite3.c PROPERTIES LANGUAGE C)

pybind11_add_module(buda ${SOURCE_FILES})
target_include_directories(buda PRIVATE src)

install(TARGETS buda DESTINATION src)