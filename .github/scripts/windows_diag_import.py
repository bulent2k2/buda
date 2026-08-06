# Import diagnostics for the Windows validation workflow.
#
# Run with `python -u`: run 4 established the failure is a NATIVE fast-fail
# (exit 0xC0000409, STATUS_STACK_BUFFER_OVERRUN == __fastfail/abort in the
# modern CRT).  __fastfail is not interceptable in-process — no faulthandler
# dump — and it kills the process with stdio buffers UNFLUSHED, which is why
# runs 2-4 showed zero output under both pwsh and cmd.  Unbuffered, staged
# prints localize the crashing stage; they cannot survive past it.
import ctypes
import os
import sys
import traceback

def stage(msg):
    print(msg, flush=True)

stage("stage 0: python %s" % sys.version.split()[0])
stage("stage 0: cwd=%s" % os.getcwd())
stage("stage 0: PYTHONPATH=%s" % os.environ.get("PYTHONPATH"))

for d in ("build", os.path.join("build", "Release"), "build-noassert"):
    dll = os.path.join(os.getcwd(), d, "buda_core.dll")
    if os.path.exists(dll):
        stage("stage 1: ctypes-loading %s ..." % dll)
        try:
            ctypes.WinDLL(dll)
            stage("stage 1: ctypes loads OK: %s" % dll)
        except OSError as e:
            stage("stage 1: ctypes load FAILED: %s -> %s" % (dll, e))

try:
    stage("stage 2: import buda_db ...")
    import buda_db
    stage("stage 2: buda_db OK: %s" % buda_db.__file__)
    stage("stage 3: import buda ...")
    import buda
    stage("stage 3: buda OK: %s" % buda.__file__)
    stage("ALL STAGES PASSED")
except BaseException:
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
    sys.exit(1)
