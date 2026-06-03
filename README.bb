export PYTHONPATH=/users/ben/src/buda/buda_system_v2/build:/users/ben/src/buda/buda_system_v2/src
cd ~/src/buda/flow/lefdef
python3 ~/src/buda/src/buda_cli.py ~/src/buda/flow/four_blocks.buda
python3 ~/src/buda/tools/def_viz_o1.py ~/src/

To run:
  cd buda_system_v2
  export PYTHONPATH=build:src
  python3 src/buda_cli.py flow/four_blocks.buda

The original session:
    	   ~/src/buda/.claude
    claude --resume 6d5ea3c2-ebcf-4afc-8cc1-265ee4e7f1d0
    gemini --resume ced8e711-630f-4f66-9d2a-cdd6e79696c5
    codex resume 019e5833-dbe5-72e3-8e36-6aa623f34bde

old:
    gemini --resume 1fd0e79e-4db9-4fbc-a533-52d3c08f1fb0
    	   Moved from: ~/src/buda/ -> ~/src/git/buda/gem

~/Desktop/term-quit-gemini-session0.txt
~/Desktop/term-exit-claude-session0.txt

Session files:
	~/.gemini/
	~/.claude/

New folders:
    ~/src/git/buda/gem
    ~/src/git/buda/claude (claude is still here ./) See: ~/src/buda/.claude/settings.local.json

Moved: /Users/ben/.gemini/tmp/buda/ to:
/Users/ben/.gemini/tmp/gem/
But also updated /Users/ben/.gemini/tmp/gem/.project_root to have /Users/ben/src/git/buda/gem
It worked!

History:
  simple to install:
  993  curl -fsSL https://claude.ai/install.sh | bash
  994    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc\n
  995  pwd
  996  claude --help
  997  claude
  998  pip install google-adk
  999  brew install gemini-cli
 1000  gemini
 2000  brew install codex
 2001  cd ~/src/git/buda
 2002  mv claude codex
 2003  cd codex
 2004  codex
 
docs
test/tests
test/tests/features
buda_system_v2/src
buda_system_v2/flow

2026.05.24:
cc> recap: Added five real-chip BUDA benchmark flows from MacroPlacement and MemPool DEFs (NVDLA, Ariane133, BlackParrot, three MemPool levels), all running clean through DetailedNUTS. All committed and pushed; no pending work.

2026.05.17
  /Users/ben/src/buda/test/tests
      59 test_bundler.py
     435 test_corner_margin.py
     306 test_detailed_nuts.py
     211 test_global_congestion.py
     115 test_no_tiny_stubs.py
     213 test_nuts.py
     286 test_routing_grid.py
     321 test_span_layer_assignment.py
      93 test_topology.py
      66 test_trivial_topo.py
    2105 total

  /Users/ben/src/buda/test/tests/features
      30 bundler_hierarchy.feature
      32 bundler_logic.feature
     101 corner_margin.feature
     119 detailed_track_assignment.feature
      45 global_congestion.feature
      27 large_fanout_mst.feature
      29 layer_assignment.feature
      29 no_tiny_stubs.feature
     108 nuts_track_assignment.feature
     117 routing_grid.feature
      76 span_aware_layer_assignment.feature
      37 topology_generation.feature
     750 total

buda_system_v2/src: 
      38 bundler.h
     110 conn_topology.h
      63 detailed_nuts.h
      82 global_router.h
      43 layering.h
     106 nuts.h
      87 routing_grid.h
     150 topology.h
     679 total

    1052 buda_cli.py
    1913 buda_viz.py
    2965 total

     255 bindings.cpp
      47 bundler.cpp
     376 conn_topology.cpp
     124 detailed_nuts.cpp
     467 global_router.cpp
      52 layering.cpp
     539 nuts.cpp
     127 routing_grid.cpp
     919 topology.cpp
    2906 total
