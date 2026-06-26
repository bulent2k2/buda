cd /home/user/buda
cat > /tmp/cl_dump.buda <<'EOF'
source ../tracks4top.buda
source tc3a_flat_x10.buda
run_bundler
generate_topologies
dump_topologies
exit
EOF
cp /tmp/cl_dump.buda flow/big_data_test/_cl_dump.buda
./buda --no-viz flow/big_data_test/_cl_dump.buda 2>&1 | grep -E "── bundle (57|46|50) " 
rm -f flow/big_data_test/_cl_dump.buda
