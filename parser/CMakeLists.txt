cmake_minimum_required(VERSION 3.20)
project(celltrace_parser CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

add_compile_options(-Wall -Wextra -Werror)

option(CELLTRACE_SANITIZE "Build with ASan+UBSan" OFF)
if(CELLTRACE_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -g)
  add_link_options(-fsanitize=address,undefined)
endif()

include(FetchContent)

# --- core static library (no Python dependency) -----------------------------
add_library(celltrace_core STATIC
  src/json.cpp
  src/parser.cpp
  src/log_store.cpp
)
target_include_directories(celltrace_core PUBLIC include)
find_package(Threads REQUIRED)
target_link_libraries(celltrace_core PUBLIC Threads::Threads)

# --- pybind11 extension module -----------------------------------------------
FetchContent_Declare(
  pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG v2.13.6
)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(celltrace_parser src/bindings.cpp)
target_link_libraries(celltrace_parser PRIVATE celltrace_core)

# --- tests --------------------------------------------------------------------
option(CELLTRACE_BUILD_TESTS "Build unit tests" ON)
if(CELLTRACE_BUILD_TESTS)
  FetchContent_Declare(
    catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.6.0
  )
  FetchContent_MakeAvailable(catch2)

  add_executable(celltrace_tests
    tests/test_json.cpp
    tests/test_parser.cpp
    tests/test_ring_buffer.cpp
    tests/test_log_store.cpp
  )
  target_link_libraries(celltrace_tests PRIVATE celltrace_core Catch2::Catch2WithMain)
  target_compile_definitions(celltrace_tests PRIVATE
    CELLTRACE_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")

  include(CTest)
  include(${catch2_SOURCE_DIR}/extras/Catch.cmake)
  catch_discover_tests(celltrace_tests)
endif()

# --- benchmark ------------------------------------------------------------
option(CELLTRACE_BUILD_BENCH "Build throughput benchmark" ON)
if(CELLTRACE_BUILD_BENCH)
  add_executable(bench_throughput benchmarks/bench_throughput.cpp)
  target_link_libraries(bench_throughput PRIVATE celltrace_core)
endif()
