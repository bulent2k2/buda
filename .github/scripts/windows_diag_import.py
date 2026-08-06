# Import diagnostics for the Windows validation workflow.
#
# A committed file, not a here-string generated in the step: runs 2 and 3
# showed failing pwsh steps on the hosted runner losing the process output
# ENTIRELY (exit 1, zero text, not even this script's first print when it was
# generated inline).  cmd steps demonstrably keep their output, so the
# workflow runs this via `shell: cmd` and everything prints to stdout.
import ctypes
import os
import sys
import traceback

print("cwd:", os.getcwd())
print("python:", sys.version)
print("PYTHONPATH:", os.environ.get("PYTHONPATH"))

for d in ("build", os.path.join("build", "Release")):
    dll = os.path.join(os.getcwd(), d, "buda_core.dll")
    if os.path.exists(dll):
        try:
            ctypes.WinDLL(dll)
            print("ctypes loads OK:", dll)
        except OSError as e:
            # Distinguishes "buda_core.dll itself cannot load (missing
            # dependency / bad image)" from "the .pyd cannot FIND it".
            print("ctypes load FAILED:", dll, "->", e)

try:
    import buda
    import buda_db
    print("import OK:", buda.__file__)
except BaseException:
    traceback.print_exc(file=sys.stdout)
    sys.exit(1)
