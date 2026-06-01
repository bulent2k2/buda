# Clustering and DEF Visualization tools

[Also see research doc](../docs/research.md)

## Some quick commands:
> cd ~/src/buda
> python3 tools/def_viz_o3.py chip_designs/ariane136/ariane136.bdb
> python3 tools/def_viz_o3.py chip_designs/ariane136/ariane136_fp_placed_macros.bdb &
> python3 tools/def_viz.py flow/lefdef/gcd/gcd.def flow/lefdef/gcd/Nangate45.lef &

First Buda viz:
> rm -f /tmp/buda_ipc_four_blocks.sock
> cd /Users/ben/src/buda/src
> python3 buda_cli.py ../flow/four_blocks.buda

After it comes up, def viz:

> cd /Users/ben/src/buda/tools
> python3 def_viz.py ../buda_system_v2/flow/lefdef/four_blocks/four_blocks.def ../buda_system_v2/flow/lefdef/four_blocks/four_blocks.lef &

## Alternative

alias py=python3
BUDA=~/src/buda/src/buda_cli.py
VIZ=~/src/buda/tools/def_viz.py
VIZo1=~/src/buda/tools/def_viz_o1.py
VIZo2=~/src/buda/tools/def_viz_o2.py
VIZo3=~/src/buda/tools/def_viz_o3.py
cd ~/src/buda/flow
rm -f /tmp/buda_ipc_four_blocks.sock
py $BUDA four_blocks.buda &
py $VIZ lefdef/four_blocks/four_blocks.def lefdef/four_blocks/four_blocks.lef &

cd ~/src/buda/flow/lefdef/ispd19_test1
py $VIZ ispd19_test1.input.def ispd19_test1.input.lef &
py $BUDA ~/src/buda/flow/ispd19_test1.buda

# Interactive IPC Test Sequence

Start with def_viz.py. It has no group management UI — just nets, instances, and canvas — so any IPC issue shows up clearly without extra UI state to reason about. Once the base is confirmed working, the o1/o2/o3 variants all use identical IPC code so they should just work.

## small test
Terminal 1 — buda_viz:
  cd ~/src/buda/buda_system_v2/src
  python3 buda_cli.py ../flow/four_blocks.buda
  Session name → four_blocks, socket → /tmp/buda_ipc_four_blocks.sock

  Terminal 2 — def_viz:
  cd ~/src/buda/tools
  python3 def_viz.py ../buda_system_v2/flow/lefdef/four_blocks/four_blocks.def \
                     ../buda_system_v2/flow/lefdef/four_blocks/four_blocks.lef

  This is the ideal IPC test: only 4 instances (u1–u4) on screen in def_viz, so the cross-highlighting is immediately obvious. Click bundle b1 in buda_viz → only u1 (orange) + u2 (blue) light up. Click
  u3 in def_viz → bundles b2, b3, b4 (all touching u3) highlight in buda_viz.

## ispd19 test1

  Setup — two terminal windows

  Terminal 1 (buda_viz):
  cd ~/src/buda/buda_system_v2/src
  python3 buda_cli.py ../flow/ispd19_test1.buda
  This runs bundler → planner → NUTS → detailed NUTS, then opens buda_viz. The session name will be ispd19_test1 so the socket will be /tmp/buda_ipc_ispd19_test1.sock.

  Terminal 2 (def_viz):
  cd ~/src/buda/tools
  python3 def_viz.py ../buda_system_v2/flow/lefdef/ispd19_test1/ispd19_test1.input.def \
                     ../buda_system_v2/flow/lefdef/ispd19_test1/ispd19_test1.input.lef
  The session name defaults to ispd19_test1 from the file stem — they auto-connect.

  ---
  Tests to run

  Test 1 — buda→def direction:
  - Click any bundle segment in buda_viz
  - Expected: def_viz highlights the driver and receiver instances for that bundle's nets; status bar shows [IPC] bundle N: X net(s) → Y instance(s)

  Test 2 — def→buda direction:
  - Click an instance box in def_viz (or select one from the instance listbox)
  - Expected: buda_viz highlights the bundle(s) that connect that instance

  Test 3 — clear propagates:
  - Click background in buda_viz → check def_viz clears
  - Click Clear button in def_viz → check buda_viz deselects

  Test 4 — late-connect (reconnect logic):
  - Start buda_viz first, wait for its window, then start def_viz
  - Within ~1 second the poll timer will connect them; run tests 1–3 again

  Test 5 — reverse startup order:
  - Start def_viz first, then buda_viz
  - Same expectation: they find each other within ~1 second

  Test 6 — session isolation:
  - Open a second buda_viz with a different design (e.g. comprehensive_demo.buda) in a third terminal — it gets session name comprehensive_demo and a different socket, so it should not interfere with the ispd19 pair

  ---
  What to watch for as failure signals

  - def_viz status bar never shows [IPC] prefix → connection didn't form (check /tmp/buda_ipc_ispd19_test1.sock exists with ls -la /tmp/buda_ipc_*.sock)
  - buda_viz console prints [viz_ipc] … errors → socket errors worth investigating
  - Highlighting works one direction only → likely _bundle_insts is empty (net_endpoints not populated)
  - Crash on click → exception in _on_ipc_message; Terminal 1/2 will show the traceback

✻ Cooked for 37s
