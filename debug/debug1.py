# By claude
#   python3 debug1.py 2>&1 | grep -v "^\[" | grep -v "Generated\|Bundler\|min_band"
#
#   Debug which bus segment fails the signal track count check

   import os, sys
   os.environ['MPLBACKEND'] = 'Agg'
   sys.path.insert(0, '.')
   import interconnect

   # Replay the script up to run_nuts, then inspect bus segments
   import importlib.util
   spec = importlib.util.spec_from_file_location('buda_cli', 'buda_cli.py')
   mod  = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(mod)

   sess = mod.BudaSession()
   sess.no_viz = True
   script = open('../flow/two_rotated_buses.buda').read()
   stop_after = 'run_detailed_nuts'
   for line in script.splitlines():
       s = line.strip()
       if not s or s.startswith('#'): continue
       sess.do_command(line)
       if s.startswith(stop_after): break

   # Now inspect the bus segments that were passed to DetailedNUTS
   if sess.nuts_result and sess.routing_grid:
       print("\n--- Bus segment intervals and signal track counts ---")
       for ts in sess.nuts_result.segments:
           bs = interconnect.BusSegment()
           bs.layer      = ts.layer
           bs.span_lo    = ts.span_lo
           bs.span_hi    = ts.span_hi
           bs.interval_lo = ts.interval_lo
           bs.interval_hi = ts.interval_hi
           bs.bit_width   = int(round(ts.width))
           layer_name = {4:'M4',5:'M5'}.get(bs.layer, str(bs.layer))

           if sess.routing_grid.has_layer(bs.layer):
               grid = sess.routing_grid.get_layer_grid(bs.layer)
               x = (bs.span_lo + bs.span_hi) / 2.0
               sigs = grid.signal_tracks_in(x, bs.interval_lo, bs.interval_hi)
               n = len(sigs)
               ok = "OK" if n >= bs.bit_width else f"FAIL (need {bs.bit_width}, have {n})"
               ivl = f"[{bs.interval_lo:.0f},{bs.interval_hi:.0f}]={bs.interval_hi-bs.interval_lo:.0f}u"
               print(f"  B{ts.bundle_id}.{layer_name} iv={ivl} sig={n} bw={bs.bit_width} {ok}")
