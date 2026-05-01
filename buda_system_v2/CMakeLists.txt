cmake_minimum_required(VERSION 3.15)
project(buda_interconnect VERSION 0.2.0 LANGUAGES CXX)
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
    src/bundler.cpp
    src/topology.cpp
    src/conn_topology.cpp
    src/layering.cpp
    src/global_router.cpp
    src/nuts.cpp
)
pybind11_add_module(interconnect ${SOURCE_FILES})