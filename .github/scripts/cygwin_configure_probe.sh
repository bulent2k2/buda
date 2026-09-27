#!/bin/bash
# Probe for the Cygwin lane's configure hang (windows-validate.yml; dispatch
# the workflow with `cygwin_probe` on to run it).
#
# What is known (runs 40-44; Cygwin 3.6.10, CMake 4.4.3): a configure can stop
# for good at CMakeLists.txt's `execute_process(COMMAND python3 -c "import
# pybind11; ...")`, 8 times in 14 tries, with CMake asleep and no child of it
# left, not even a zombie.  Every hang was at that call: none at the compiler
# runs before it, none at FindPython's interpreter runs after it (which name
# the interpreter by absolute path).  The step's stdin made no difference:
# run 43 looked as if it did, and in run 44 the configure with stdin from
# /dev/null hung and the one with the step's stdin did not.
#
# The matrix below takes that call apart.  Each trial is a fresh minimal
# project (compiler detection, which came before every hang) that makes ONE
# execute_process with a TIMEOUT, so a trial that hangs ends by itself and
# says whether the child's output had arrived.  Variants: `python3` by name
# (the call itself), the same `python3` by absolute path (on Cygwin a symlink
# into /etc/alternatives), the interpreter's real file (what FindPython
# runs), and `true` by name (not Python).  Then the real configure, a few
# times, with CMakeLists.txt's guard.
#
# Nothing here relies on a signal being honoured (run 41's `timeout 600`
# never ended a hung configure): each configure runs in the background with
# its output in a file, and once that output stops growing the probe lists
# every process, kills the tree by force (TerminateProcess) and says so.
# Always exits 0: its job is to report.
set -u
cd "$(dirname "$0")/../.." || exit 0
# A background job in a non-interactive bash reads /dev/null unless it is
# given stdin explicitly, so fd 3 keeps the step's stdin for the configures.
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

# bounded <max seconds> <idle seconds> <name> <command...>: run it with the
# step's stdin and its output in $out/<name>.out.  It counts as hung once
# that output has not grown for <idle> seconds, or at <max>.  Sets $took.
bounded() {
    local limit=$1 idle=$2 name=$3 t=0 still=0 size=-1 now pid win p
    shift 3
    "$@" <&3 > "$out/$name.out" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        t=$((t + 1))
        now=$(wc -c < "$out/$name.out")
        if [ "$now" = "$size" ]; then
            still=$((still + 1))
        else
            still=0
            size=$now
        fi
        if [ "$still" -ge "$idle" ] || [ "$t" -ge "$limit" ]; then
            break
        fi
    done
    took=$t
    if kill -0 "$pid" 2>/dev/null; then
        hung="$hung $name"
        echo "$name" >> "$out/HUNG"     # at once, in case the step is cut off
        echo "=== $name: STILL RUNNING after ${t}s, its output unchanged" \
             "for ${still}s.  The processes:"
        procs
        echo "=== Windows processes (ps -W), the likely ones:"
        ps -W 2>/dev/null | grep -iE 'cmake|python|make|gcc|true|defunct' \
            | grep -v grep
        win=$(cat "/proc/$pid/winpid" 2>/dev/null)
        for p in $(tree_of "$pid"); do kill -9 "$p" 2>/dev/null; done
        [ -n "$win" ] && taskkill /F /T /PID "$win" > /dev/null 2>&1
        echo "--- $name output, last 30 lines:"
        tail -n 30 "$out/$name.out"
        return 124                    # no wait: nothing here may block
    fi
    wait "$pid"
}

# trial <variant> <n> <program> [args...]: a minimal project that makes one
# execute_process of <program>, bounded by a TIMEOUT, and reports it.
trial() {
    local v=$1 n=$2 dir line rc
    shift 2
    dir=$out/t-$v-$n
    mkdir -p "$dir"
    {
        echo 'cmake_minimum_required(VERSION 3.15)'
        echo 'project(p LANGUAGES CXX)'
        printf 'execute_process(COMMAND'
        printf ' "%s"' "$@"
        echo ' OUTPUT_VARIABLE out OUTPUT_STRIP_TRAILING_WHITESPACE'
        echo '    ERROR_QUIET RESULT_VARIABLE rc TIMEOUT 20)'
        echo 'message(STATUS "TRIAL rc=[${rc}] out=[${out}]")'
    } > "$dir/CMakeLists.txt"
    bounded 180 60 "t-$v-$n" cmake -S "$dir" -B "$dir/b" --trace-expand
    rc=$?
    line=$(grep -a '^-- TRIAL ' "$out/t-$v-$n.out" | tail -n 1)
    echo "=== trial $v #$n: exit $rc after ${took}s: ${line:-no TRIAL line}"
}

echo "cmake: $(cmake --version | head -n 1)"
echo "python3: $(command -v python3) -> $(readlink -f "$(command -v python3)")"
echo "cygwin: $(uname -r)"
link_py=$(command -v python3)
real_py=$(readlink -f "$link_py")
[ -e "$real_py.exe" ] && real_py="$real_py.exe"
echo "real interpreter file: $real_py"

pyarg='import pybind11; print(pybind11.get_cmake_dir())'
for n in 1 2 3 4 5 6; do
    trial name "$n" python3 -c "$pyarg"
    trial link "$n" "$link_py" -c "$pyarg"
    trial exe  "$n" "$real_py" -c "$pyarg"
    trial true "$n" true
done
echo "=== matrix, per variant: TIMEOUT means CMake was still waiting at 20 s"
for v in name link exe true; do
    printf '===   %-4s ' "$v"
    for n in 1 2 3 4 5 6; do
        line=$(grep -a '^-- TRIAL ' "$out/t-$v-$n.out" 2>/dev/null | tail -n 1)
        case "$line" in
        *'rc=[0]'*) printf 'ok ' ;;
        *timeout*out=\[\]*) printf 'TIMEOUT(no-output) ' ;;
        *timeout*) printf 'TIMEOUT(output) ' ;;
        '') printf 'KILLED ' ;;
        *) printf 'other ' ;;
        esac
    done
    echo
done

# The real configure with CMakeLists.txt's guard (the TIMEOUT on the
# pybind11 lookup): does it finish, and did the guard have to act?
for n in 1 2 3; do
    rm -rf build-probe
    bounded 600 120 "real-$n" \
        cmake -S . -B build-probe -DBUDA_ARCH=x86-64-v2 --trace-expand
    rc=$?
    guard=$(grep -a '^-- BUDA: python3' "$out/real-$n.out" | tail -n 1)
    echo "=== real #$n: exit $rc after ${took}s; guard: ${guard:-did not act}"
done

echo "=== hung:${hung:- none}"
rm -rf build-probe
touch "$out/DONE"
exit 0
