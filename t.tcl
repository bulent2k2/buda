source /fake/sp/tools/buda.tcl
buda::start -viz 0 -python "/fake/rt/venv/bin/python"
buda::def_layer 3 M3 H 20
buda::def_layer 4 M4 V 20
buda::add_block a 0 0 100 100
buda::add_block b 300 0 400 100
# Backslashes are DOUBLED because this heredoc is unquoted (it has to
# be, to expand $SP and $RUNNER_TEMP): bash eats one level, leaving
# Tcl the `\[4\]` it needs to keep the brackets literal.
buda::add_bus "bus\[4\]" a.out b.in
buda::run_bundler STRICT
puts "   tcl bridge: [buda::query bundles] bundle(s)"
buda::stop
