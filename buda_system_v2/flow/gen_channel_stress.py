#!/usr/bin/env python3
"""Generate channel_stress.buda

Layout
------
  8 "top" blocks  (u_t0..u_t7)  above a wide horizontal channel
  8 "bottom" blocks (u_b0..u_b7) below the channel
  200 nets randomly pairing one top block to one bottom block

Nets are named  t{T}_b{B}_{idx:02d}  so that the bundle whose driver is
u_tT and receiver u_bB can always be targeted by hint  "t{T}_b{B}".

Block geometry
--------------
  width = 60, height = 60, x-pitch = 80
  bottom row : y = 20 .. 80
  top row    : y = 220 .. 280
  channel    : y = 80 .. 220  (140 units tall)
"""

import random, sys, os

SEED      = 42
NUM_NETS  = 200
N         = 8         # blocks per row
BLOCK_W   = 60
BLOCK_H   = 60
X_PITCH   = 80
X0        = 20
Y_BOT_LO  = 20;  Y_BOT_HI  = 80
Y_TOP_LO  = 220; Y_TOP_HI  = 280

random.seed(SEED)

lines = []
def L(s=""): lines.append(s)

L("# channel_stress.buda")
L("# 8 top blocks + 8 bottom blocks with a 140-unit horizontal channel.")
L("# 200 nets randomly paired (fixed seed=42) — stress-tests NUTS channel packing.")
L()

L("def_layer 4 M4 H TOP 0.0")
L("def_layer 5 M5 V TOP 0.0")
L("def_layer 6 M6 H TOP 0.0")
L()

for i in range(N):
    x1 = X0 + i * X_PITCH
    x2 = x1 + BLOCK_W
    L(f"add_block u_b{i}  {x1:4d} {Y_BOT_LO:4d} {x2:4d} {Y_BOT_HI:4d}")
L()
for i in range(N):
    x1 = X0 + i * X_PITCH
    x2 = x1 + BLOCK_W
    L(f"add_block u_t{i}  {x1:4d} {Y_TOP_LO:4d} {x2:4d} {Y_TOP_HI:4d}")
L()

# Assign 200 nets to random (top, bottom) pairs
pair_count = {}   # (t,b) -> count so far (for per-pair indexing)
pairs_seen = set()

for _ in range(NUM_NETS):
    t = random.randint(0, N - 1)
    b = random.randint(0, N - 1)
    key = (t, b)
    idx = pair_count.get(key, 0)
    pair_count[key] = idx + 1
    pairs_seen.add(key)
    L(f"add_net t{t}_b{b}_{idx:02d}  u_t{t}.tx  u_b{b}.rx")

L()
L("run_bundler strict")
L()

# One generate_topologies_for_bundle per unique (top, bottom) pair
for (t, b) in sorted(pairs_seen):
    L(f"generate_topologies_for_bundle t{t}_b{b}  u_t{t}  u_b{b}")

L()
L("run_planner 5")
L("run_nuts 2.0")
L("visualize")

out_path = os.path.join(os.path.dirname(__file__), "channel_stress.buda")
with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")

# Stats
print(f"Wrote {out_path}")
print(f"  {NUM_NETS} nets  →  {len(pairs_seen)} unique (top,bottom) pairs  →  ≤{len(pairs_seen)} bundles")
widths = sorted(pair_count.values())
print(f"  nets-per-bundle: min={widths[0]}  max={widths[-1]}  "
      f"mean={sum(widths)/len(widths):.1f}")
print(f"  layout canvas: x=0..{X0 + (N-1)*X_PITCH + BLOCK_W + X0}  "
      f"y=0..{Y_TOP_HI + X0}")
