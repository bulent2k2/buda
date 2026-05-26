python3 def_cluster.py \
     /tmp/ispd19/ispd19_test5/ispd19_test5.input.def \
     /tmp/ispd19/ispd19_test5/ispd19_test5.input.lef \
     --bipartite --grid 10 --min 5 \
     --high-fanout --min-fanout 5 --min-hpwl 20 \
     --out /tmp/test5.buda 2>&1 | tail -10
