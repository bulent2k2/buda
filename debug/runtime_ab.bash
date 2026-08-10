cd ..; python3 tools/runtime_ab.py --base origin/main --threads 4 -n 3 flow/chip/chip_stack_bottomup.buda flow/chip/chip_stack_topdown.buda 2>&1 | tail -45
