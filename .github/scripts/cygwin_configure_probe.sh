#!/bin/bash
# Probe for the Cygwin lane's configure hang (windows-validate.yml).
#
# Runs 40 and 41 stopped in CMake's configure right after the JCC-erratum
# status line and printed nothing more until the step's cap, 82 and 30
# minutes later.  A `timeout 600` around a traced configure did not end it
# either (run 41: no exit status and no trace tail within 15 minutes), so
# nothing here relies on a signal being honoured: every configure runs in
# the background with its output in a file, and once that output stops
# growing the probe lists every process -- the one CMake is waiting on is
# the answer -- kills the tree by force (TerminateProcess), and only then
# prints the output, whose last trace line is the command in flight.
#
# In CMakeLists.txt the JCC line is followed by an execute_process of
# `python3 -c "import pybind11; ..."` and then find_package(pybind11), whose
# FindPython (PYBIND11_FINDPYTHON ON) runs the interpreter several times.
# After the real configure, three minimal projects take those apart; if the
# real one hung, it runs twice more, once with the interpreter handed to
# FindPython rather than searched for and once with stdin closed.  Always
# exits 0: its job is to report.
set -u
cd "$(dirname "$0")/../.." || exit 0
# A background job in a non-interactive bash reads /dev/null unless it is
# given stdin explicitly, so fd 3 keeps the stdin bin/bb's configure has.
exec 3<&0
out=cyg-probe
rm -rf "$out" build-probe
mkdir -p "$out"
hung=""

ppid_of() {     # from /proc/<pid>/stat, which Cygwin and Linux both have
    local s
    s=$(cat "/proc/$1/stat" 2>/dev/null) || return 0
    s=${s##*) }     # past "pid (command)"; then "state ppid ..."
    set -- $s
    echo "${2:-}"
}

procs() {       # every process with a command line: pid, parent, command
    local d cmd
    for d in /proc/[0-9]*; do
        cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
        [ -n "$cmd" ] || continue
        printf '  %6s ppid=%-6s winpid=%-6s %s\n' "${d#/proc/}" \
            "$(ppid_of "${d#/proc/}")" "$(cat "$d/winpid" 2>/dev/null)" "$cmd"
    done | sort -n
}

tree_of() {     # $1 and every process below it, deepest first
    local d
    for d in /proc/[0-9]*; do
        [ "$(ppid_of "${d#/proc/}")" = "$1" ] && tree_of "${d#/proc/}"
    done
    echo "$1"
}

# bounded <max seconds> <name> <command...>: run it with its output in
# $out/<name>.out and the stdin bin/bb's configure has (fd 3).  It counts
# as hung once that output has not grown for $idle seconds, or at <max>.
# The configures run with --trace-expand and no --trace-redirect: the trace
# then goes to stderr, which is unbuffered, so its last line is the command
# in flight; a trace file may be buffered, and a kill would lose that line.
idle=120
bounded() {
    local limit=$1 name=$2 t=0 still=0 size=-1 now pid win p
    shift 2
    echo "=== $name: $*"
    "$@" <&3 > "$out/$name.out" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        sleep 2
        t=$((t + 2))
        now=$(wc -c < "$out/$name.out")
        if [ "$now" = "$size" ]; then
            still=$((still + 2))
        else
            still=0
            size=$now
        fi
        if [ "$still" -ge "$idle" ] || [ "$t" -ge "$limit" ]; then
            break
        fi
    done
    if kill -0 "$pid" 2>/dev/null; then
        hung="$hung $name"
        echo "$name" >> "$out/HUNG"     # at once, in case the step is cut off
        echo "=== $name: STILL RUNNING after ${t}s, its output unchanged" \
             "for ${still}s.  The processes:"
        procs
        echo "=== Windows processes (ps -W), the likely ones:"
        ps -W 2>/dev/null | grep -iE 'cmake|python|make|gcc|g\+\+|cc1|ld|/as|sh' \
            | grep -v grep
        win=$(cat "/proc/$pid/winpid" 2>/dev/null)
        for p in $(tree_of "$pid"); do kill -9 "$p" 2>/dev/null; done
        [ -n "$win" ] && taskkill /F /T /PID "$win" > /dev/null 2>&1
        echo "=== $name: killed"     # no wait: nothing here may block
    else
        wait "$pid"
        echo "=== $name: exit $? after ${t}s"
    fi
    echo "--- $name output (with the trace), last 50 lines:"
    tail -n 50 "$out/$name.out"
}

# A minimal project: $1 = directory name, $2 = languages, rest = body lines.
project() {
    local dir=$out/$1 langs=$2
    shift 2
    mkdir -p "$dir"
    {
        echo 'cmake_minimum_required(VERSION 3.15)'
        echo "project(probe LANGUAGES $langs)"
        printf '%s\n' "$@"
    } > "$dir/CMakeLists.txt"
}

echo "cmake: $(cmake --version | head -n 1)"
echo "python3: $(command -v python3) -> $(readlink -f "$(command -v python3)")"
echo "pythons on PATH:"
ls -la /usr/bin/python* /usr/bin/pypy* 2>/dev/null

bounded 600 real cmake -S . -B build-probe -DBUDA_ARCH=x86-64-v2 --trace-expand

project interp NONE \
    'find_package(Python 3.9 REQUIRED COMPONENTS Interpreter)' \
    'message(STATUS "interp: ${Python_EXECUTABLE} ${Python_VERSION}")'
bounded 300 interp cmake -S "$out/interp" -B "$out/interp/b" --trace-expand

project module CXX \
    'find_package(Python 3.9 REQUIRED COMPONENTS Interpreter Development.Module)' \
    'message(STATUS "module: ${Python_EXECUTABLE} ${Python_INCLUDE_DIRS} ${Python_LIBRARIES}")'
bounded 300 module cmake -S "$out/module" -B "$out/module/b" --trace-expand

project pybind CXX \
    'execute_process(COMMAND python3 -c "import pybind11; print(pybind11.get_cmake_dir())"' \
    '    OUTPUT_VARIABLE d OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)' \
    'message(STATUS "pybind11 cmake dir: ${d}")' \
    'list(APPEND CMAKE_PREFIX_PATH "${d}")' \
    'set(PYBIND11_FINDPYTHON ON)' \
    'find_package(pybind11 REQUIRED)' \
    'message(STATUS "pybind: ${Python_EXECUTABLE} ${PYTHON_MODULE_EXTENSION}")'
bounded 300 pybind cmake -S "$out/pybind" -B "$out/pybind/b" --trace-expand

# Only if the real configure hung: hand FindPython the interpreter, and
# close stdin, each on its own.
case " $hung " in
*" real "*)
    rm -rf build-probe
    bounded 600 given cmake -S . -B build-probe -DBUDA_ARCH=x86-64-v2 \
        -DPython_EXECUTABLE="$(readlink -f "$(command -v python3)")" \
        --trace-expand
    rm -rf build-probe
    bounded 600 nostdin sh -c 'exec cmake -S . -B build-probe \
        -DBUDA_ARCH=x86-64-v2 --trace-expand < /dev/null'
    ;;
esac
echo "=== hung:${hung:- none}"
rm -rf build-probe
# The workflow stops the job on HUNG (or on a probe cut off before DONE)
# rather than spend the build step's cap on a configure seen to hang.
touch "$out/DONE"
exit 0
