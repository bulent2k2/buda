cd ~/src/git/buda/cc; timeout 120 python3 -c "
import sys; sys.path.insert(0,'./tmp/scratchpad')
import healer_staged as H
r = H.run_flow('flow/big_data_test/bigHalf.buda')
for k in ['S0','S1','S2','S3','S4']: print(k, r[k])
" 2>&1 | grep -vE "TopoGen|hanan|Warning" | tail -6

# S0 (4, 286, 2.0)
# S1 (2, 174, 2.3)
# S2 (0, 186, 3.2)
# S3 (1, 10, 3.9)
# S4 (0, 0, 7.7)
