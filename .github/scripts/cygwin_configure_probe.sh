#!/bin/bash
# Probe for the Cygwin lane's configure hang (windows-validate.yml; dispatch
# the workflow with `cygwin_probe` on to run it).
#
# What it found (run 43): under the Actions runner the configure stops at
# its first execute_process after the compiler checks -- CMakeLists.txt's
# `python3 -c "import pybind11; ..."` -- when it inherits the step's stdin,
# a pipe held by the runner, which is not a Cygwin process.  It hung in both
# tries, with CMake still running and no child of it left.  With stdin from
# /dev/null the same configure finished in 8 s, and a minimal project making
# the same calls finished with the step's stdin.  CMake 4.4's
# execute_process hands every child CMake's own stdin unless INPUT_FILE is
# given.  Runs 40 and 41 hung the same way in bin/bb, and run 42's bin/bb
# did not, so it is likely rather than certain.
#
# How it looks: nothing here relies on a signal being honoured (run 41's
# `timeout 600` never ended a hung configure).  Each configure runs in the
# background with its output in a file; once that output stops growing, the
# probe lists every process, kills the tree by force (TerminateProcess) and
# prints the output, whose last trace line is the command in flight.
# Always exits 0: its job is to report.
set -u
cd "$(dirname "$0")/../.." || exit 0
# A background job in a non-interactive bash reads /dev/null unless it is
# given stdin explicitly, so fd 3 keeps the step's stdin for the cases that
# want it.
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

procs() {       # every process: pid, parent, Windows pid, state, command
    local d pid cmd
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
        # An exited child nobody has reaped yet has no command line.
        [ -n "$cmd" ] || cmd="[$(cat "$d/exename" 2>/dev/null)]"
        printf '  %6s ppid=%-6s winpid=%-6s %-14s %s\n' "$pid" \
            "$(ppid_of "$pid")" "$(cat "$d/winpid" 2>/dev/null)" \
            "$(sed -n 's/^State:[[:space:]]*//p' "$d/status" 2>/dev/null)" \
            "$cmd"
    done | sort -n
}

tree_of() {     # $1 and every process below it, deepest first
    local d
    for d in /proc/[0-9]*; do
        [ "$(ppid_of "${d#/proc/}")" = "$1" ] && tree_of "${d#/proc/}"
    done
    echo "$1"
}

# bounded <max seconds> <name> <stdin> <command...>: run it with the given
# stdin and its output in $out/<name>.out.  It counts as hung once that
# output has not grown for $idle seconds, or at <max>.  The configures run
# with --trace-expand and no --trace-redirect: the trace then goes to
# stderr, which is unbuffered, so its last line is the command in flight; a
# trace file may be buffered, and a kill would lose that line.
idle=120
bounded() {
    local limit=$1 name=$2 input=$3 t=0 still=0 size=-1 now pid win p
    shift 3
    echo "=== $name (stdin: $input): $*"
    if [ "$input" = step ]; then
        "$@" <&3 > "$out/$name.out" 2>&1 &
    else
        "$@" < /dev/null > "$out/$name.out" 2>&1 &
    fi
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
        ps -W 2>/dev/null | grep -iE 'cmake|python|make|gcc|defunct' \
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

echo "cmake: $(cmake --version | head -n 1)"
echo "python3: $(command -v python3) -> $(readlink -f "$(command -v python3)")"
echo "cygwin: $(uname -r)"

# The control: bin/bb's configure as the step would run it.
bounded 600 real step \
    cmake -S . -B build-probe -DBUDA_ARCH=x86-64-v2 --trace-expand

# The workaround the build step uses: the same, with stdin from /dev/null.
rm -rf build-probe
bounded 600 nostdin null \
    cmake -S . -B build-probe -DBUDA_ARCH=x86-64-v2 --trace-expand

# The contrast: the calls that hang, in a project with nothing else in it,
# with the step's stdin.
mkdir -p "$out/pybind"
cat > "$out/pybind/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.15)
project(probe LANGUAGES CXX)
execute_process(COMMAND python3 -c "import pybind11; print(pybind11.get_cmake_dir())"
    OUTPUT_VARIABLE d OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
list(APPEND CMAKE_PREFIX_PATH "${d}")
set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 REQUIRED)
message(STATUS "pybind: ${Python_EXECUTABLE} ${PYTHON_MODULE_EXTENSION}")
EOF
bounded 300 pybind step \
    cmake -S "$out/pybind" -B "$out/pybind/b" --trace-expand

echo "=== hung:${hung:- none}"
rm -rf build-probe
touch "$out/DONE"
exit 0
