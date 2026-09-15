Head:     main Test cases for UI improvement and visual observations
Merge:    origin/main Test cases for UI improvement and visual observations
Push:     origin/main Test cases for UI improvement and visual observations

Untracked files (4)
test/tests/features/corner_margin.feature
test/tests/features/span_aware_layer_assignment.feature
test/tests/test_corner_margin.py
test/tests/test_span_layer_assignment.py

Unstaged changes (10)
modified   buda_system_v2/flow/test5.buda
@@ -1,11 +1,11 @@
 # From channel_stress, pick a few bundles with pin conflict
 
-def_layer 3 M3 V LOW 10.0
+def_layer 3 M3 V 10.0
 # high power % is to force planner to resort to M6
 def_layer 4 M4 H TOP 88.0
 def_layer 5 M5 V TOP 20.0
-def_layer 6 M6 H LOW 20.0
-def_layer 7 M7 V LOW 40.0
+def_layer 6 M6 H 20.0
+def_layer 7 M7 V 40.0
 
 add_block u_b0    20   20   80   80
 add_block u_b1   100   20  160   80
modified   buda_system_v2/src/bindings.cpp
@@ -43,8 +43,28 @@ PYBIND11_MODULE(interconnect, m) {
         .def_readwrite("topology_pinned",         &BundleWrapper::topology_pinned);
     py::class_<Netlist>(m, "Netlist").def(py::init<>()).def("add_net", &Netlist::add_net);
     py::class_<Bundler>(m, "Bundler").def(py::init<>()).def("set_strategy", &Bundler::set_strategy).def("run", &Bundler::run);
-    py::class_<Floorplan>(m, "Floorplan").def(py::init<>()).def("add_block", &Floorplan::add_block).def("get_hanan_grid", [](const Floorplan& fp) { std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y); }).def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });
-    py::class_<LayerStack>(m, "LayerStack").def(py::init<>()).def("add_layer", &LayerStack::add_layer).def("get_layer_ids_by_dir", &LayerStack::get_layer_ids_by_dir).def("get_layer_ids_preferred", &LayerStack::get_layer_ids_preferred).def("get_top_layer", &LayerStack::get_top_layer).def("get_layer_type", &LayerStack::get_layer_type);
+    py::class_<BlockCornerMargin>(m, "BlockCornerMargin")
+        .def(py::init<>())
+        .def_readwrite("dx", &BlockCornerMargin::dx)
+        .def_readwrite("dy", &BlockCornerMargin::dy);
+    py::class_<Floorplan>(m, "Floorplan").def(py::init<>())
+        .def("add_block",              &Floorplan::add_block)
+        .def("set_block_corner_margin",&Floorplan::set_block_corner_margin)
+        .def("get_block_corner_margin",&Floorplan::get_block_corner_margin)
+        .def("get_block_bounds",       &Floorplan::get_block_bounds)
+        .def("get_hanan_grid", [](const Floorplan& fp) {
+            std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y);
+        })
+        .def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });
+    py::class_<LayerStack>(m, "LayerStack").def(py::init<>())
+        .def("add_layer",        &LayerStack::add_layer)
+        .def("set_layer_span",   &LayerStack::set_layer_span)
+        .def("set_layer_kspan",  &LayerStack::set_layer_kspan)
+        .def("is_top",           &LayerStack::is_top)
+        .def("get_layer_ids_by_dir",   &LayerStack::get_layer_ids_by_dir)
+        .def("get_layer_ids_preferred",&LayerStack::get_layer_ids_preferred)
+        .def("get_top_layer",    &LayerStack::get_top_layer)
+        .def("get_layer_type",   &LayerStack::get_layer_type);
     py::class_<SegConn>(m, "SegConn")
         .def_readwrite("kind",       &SegConn::kind)
         .def_readwrite("block_name", &SegConn::block_name)
@@ -95,6 +115,7 @@ PYBIND11_MODULE(interconnect, m) {
     py::class_<GlobalRouter>(m, "GlobalRouter")
         .def(py::init<const Floorplan&, const LayerStack&>())
         .def("set_layer_overhead",  &GlobalRouter::set_layer_overhead)
+        .def("set_planner_param",   &GlobalRouter::set_planner_param)
         .def("build_congestion_map",&GlobalRouter::build_congestion_map)
         .def("optimize_topologies", &GlobalRouter::optimize_topologies)
         .def("get_cuts",            &GlobalRouter::get_cuts)
modified   buda_system_v2/src/buda_cli.py
@@ -45,6 +45,7 @@ class BudaSession:
         self.bundles = []
         self.nuts_result = None
         self._layer_overheads = {}   # layer_id -> overhead_percent
+        self._planner_params  = {}   # param_name -> value (buffered before planner exists)
         self._net_endpoints   = {}   # net_name -> (driver_instance, [receiver_instances])
         self._layer_name_map = {}    # layer_name -> layer_id
         self._nuts_pitch = 1.0       # last track pitch used by run_nuts
@@ -534,7 +535,35 @@ class BudaSession:
         args = parts[1:]
 
         if cmd == "add_block":
-            self.fp.add_block(args[0], int(args[1]), int(args[2]), int(args[3]), int(args[4]))
+            # add_block <name> <x1> <y1> <x2> <y2>
+            #   [corner_margin dx <n> [dy <n>]]
+            #   [corner_margin pct_h <p> [pct_v <p>]]
+            name = args[0]
+            x1, y1, x2, y2 = int(args[1]), int(args[2]), int(args[3]), int(args[4])
+            self.fp.add_block(name, x1, y1, x2, y2)
+            rest = list(args[5:])
+            if rest and rest[0].lower() == "corner_margin":
+                rest = rest[1:]
+                kws = {}
+                i = 0
+                while i < len(rest):
+                    kw = rest[i].lower()
+                    if kw in ("dx", "dy", "pct_h", "pct_v") and i + 1 < len(rest):
+                        kws[kw] = float(rest[i + 1]); i += 2
+                    else: i += 1
+                # Resolve to absolute dx, dy
+                cm_dx = cm_dy = 0
+                if "dx" in kws:    cm_dx = int(round(kws["dx"]))
+                if "dy" in kws:    cm_dy = int(round(kws["dy"]))
+                if "pct_h" in kws: cm_dx = int(round((x2 - x1) * kws["pct_h"] / 100.0))
+                if "pct_v" in kws: cm_dy = int(round((y2 - y1) * kws["pct_v"] / 100.0))
+                # If only one axis specified, mirror to the other
+                if "dx" in kws and "dy" not in kws and "pct_v" not in kws: cm_dy = cm_dx
+                if "dy" in kws and "dx" not in kws and "pct_h" not in kws: cm_dx = cm_dy
+                if "pct_h" in kws and "pct_v" not in kws and "dy" not in kws: cm_dy = cm_dx
+                if "pct_v" in kws and "pct_h" not in kws and "dx" not in kws: cm_dx = cm_dy
+                if cm_dx > 0 or cm_dy > 0:
+                    self.fp.set_block_corner_margin(name, cm_dx, cm_dy)
         elif cmd == "add_net":
             name, drv_pin, rcv_str = args[0], args[1], args[2]
             rcv_pins = rcv_str.split(',')
@@ -566,14 +595,45 @@ class BudaSession:
                 self.netlist.add_net(net_name, drv_pin, rcv_pins)
                 self._net_endpoints[net_name] = (drv_inst, rcv_insts)
         elif cmd == "def_layer":
-            lid, name, dirstr, typestr, ovh = args
-            ldir = interconnect.LayerDir.HORIZONTAL if dirstr.upper()=="H" else interconnect.LayerDir.VERTICAL
-            ltype = interconnect.LayerType.TOP if typestr.upper()=="TOP" else interconnect.LayerType.LOW
+            # def_layer <id> <name> <H|V> [TOP|LOW] <overhead%>
+            #           [span_min N] [span_max N] [kSpan K]
+            # TOP/LOW is optional; omitting it means non-TOP. LOW is accepted for
+            # backward compatibility and treated as non-TOP.
+            lid, name, dirstr = args[0], args[1], args[2]
+            rest = list(args[3:])
+            if rest and rest[0].upper() in ("TOP", "LOW"):
+                typestr = rest.pop(0).upper()
+            else:
+                typestr = "NONE"
+            ovh = rest.pop(0)
+            # Parse optional keyword args
+            span_min = span_max = kspan_override = None
+            i = 0
+            while i < len(rest):
+                kw = rest[i].lower()
+                if kw == "span_min":    span_min = int(rest[i+1]);    i += 2
+                elif kw == "span_max":  span_max = int(rest[i+1]);    i += 2
+                elif kw == "kspan":     kspan_override = float(rest[i+1]); i += 2
+                else: i += 1
+            ldir  = interconnect.LayerDir.HORIZONTAL if dirstr.upper()=="H" else interconnect.LayerDir.VERTICAL
+            ltype = interconnect.LayerType.TOP if typestr == "TOP" else interconnect.LayerType.LOW
             self.layers.add_layer(int(lid), name, ldir, ltype)
+            if span_min is not None or span_max is not None:
+                smin = span_min if span_min is not None else 0
+                smax = span_max if span_max is not None else 1_000_000_000
+                self.layers.set_layer_span(int(lid), smin, smax)
+            if kspan_override is not None:
+                self.layers.set_layer_kspan(int(lid), kspan_override)
             ovh_val = float(ovh)
             if ovh_val > 0.0:
                 self._layer_overheads[int(lid)] = ovh_val
             self._layer_name_map[name] = int(lid)
+        elif cmd == "set_planner_param":
+            name_p, value_p = args[0], float(args[1])
+            if self.planner is None:
+                self._planner_params[name_p] = value_p
+            else:
+                self.planner.set_planner_param(name_p, value_p)
         elif cmd == "run_bundler":
             self.bundler.set_strategy(interconnect.Strategy.STRICT)
             raw_bundles = self.bundler.run(self.netlist)
@@ -687,6 +747,8 @@ class BudaSession:
                 self.planner = interconnect.GlobalRouter(self.fp, self.layers)
                 for lid, ovh in self._layer_overheads.items():
                     self.planner.set_layer_overhead(lid, ovh)
+                for pname, pval in self._planner_params.items():
+                    self.planner.set_planner_param(pname, pval)
                 self.planner.build_congestion_map()
                 # Apply architect-pinned selections BEFORE optimizing so the
                 # planner scores the correct topology and assigns layers for it.
modified   buda_system_v2/src/conn_topology.cpp
@@ -240,23 +240,35 @@ void ConnTopology::compute_slide_ranges(const Floorplan& fp) {
     }
 
     // ── Pass 1 ──
-    // Constrain to the full face extent [rect.lo, rect.hi].  No inward margin is
-    // applied here: topology generation already places segment endpoints at least
-    // 10% from block corners (via clamp_10pct / stub_y / stub_x), so the NUTS
-    // preferred position is already away from extremes.  Shrinking the slide range
-    // by another 10% would exclude the face-adjacent positions that the topology
-    // generator intentionally produces (e.g. hy = src.y2 for a below-to-above L),
-    // causing inverted intervals and out-of-range placements.
+    // Constrain to the block face extent, optionally shrunk by the block's corner
+    // margin (set via Floorplan::set_block_corner_margin).
+    //
+    //   H segment on left/right face (face runs in Y) → margin = dy
+    //     slide becomes [rect.y1+dy, rect.y2-dy]
+    //   V segment on top/bottom face (face runs in X) → margin = dx
+    //     slide becomes [rect.x1+dx, rect.x2-dx]
+    //
+    // Guard: if the margin would invert the interval (block smaller than 2×margin),
+    // fall back to the full face extent for that axis.
     for (auto& cs : segs_) {
         for (const auto& conn : cs.conns) {
             if (conn.kind != SegConn::BUSTERM) continue;
             const Rect& rect = bmap.at(conn.block_name);
+            BlockCornerMargin cm = fp.get_block_corner_margin(conn.block_name);
             if (cs.horiz) {
-                cs.perp_lo = std::max(cs.perp_lo, rect.y1);
-                cs.perp_hi = std::min(cs.perp_hi, rect.y2);
+                int m  = cm.dy;
+                int lo = rect.y1 + m;
+                int hi = rect.y2 - m;
+                if (lo > hi) { lo = rect.y1; hi = rect.y2; }  // block too short
+                cs.perp_lo = std::max(cs.perp_lo, lo);
+                cs.perp_hi = std::min(cs.perp_hi, hi);
             } else {
-                cs.perp_lo = std::max(cs.perp_lo, rect.x1);
-                cs.perp_hi = std::min(cs.perp_hi, rect.x2);
+                int m  = cm.dx;
+                int lo = rect.x1 + m;
+                int hi = rect.x2 - m;
+                if (lo > hi) { lo = rect.x1; hi = rect.x2; }  // block too narrow
+                cs.perp_lo = std::max(cs.perp_lo, lo);
+                cs.perp_hi = std::min(cs.perp_hi, hi);
             }
         }
     }
modified   buda_system_v2/src/global_router.cpp
@@ -15,6 +15,13 @@ void GlobalRouter::set_layer_overhead(int layer_id, double overhead_percent) {
     layer_dilution_factors_[layer_id] = 100.0 / (100.0 - overhead_percent);
 }
 
+void GlobalRouter::set_planner_param(const std::string& name, double value) {
+    if      (name == "kCong")             kCong_             = value;
+    else if (name == "kSpan")             kSpan_             = value;
+    else if (name == "base_cost_non_top") base_cost_non_top_ = value;
+    else std::cout << "[Planner] Warning: unknown param '" << name << "'\n";
+}
+
 double GlobalRouter::get_dilution(int layer_id) const {
     auto it = layer_dilution_factors_.find(layer_id);
     return (it != layer_dilution_factors_.end()) ? it->second : 1.0;
@@ -214,27 +221,44 @@ void GlobalRouter::apply_segment(const Segment& seg, int layer_id, double eff_wi
 }
 
 // ---------------------------------------------------------------------------
-// Affinity helpers — bias toward physically appropriate layers
+// Span-aware cost helpers
 // ---------------------------------------------------------------------------
 
-static constexpr double kBase     = 0.5;
-static constexpr double kMismatch = 0.001;
-
-static int preferred_alt_idx(double span_norm, int n_alts) {
-    if (n_alts <= 1) return 0;
-    return std::clamp((int)std::round(span_norm * (n_alts - 1)), 0, n_alts - 1);
+// Hyperbolic congestion cost: kCong * u/(1-u) where u = (existing+eff)/cap.
+// Returns the peak cost across all cuts the segment crosses on the given layer.
+double GlobalRouter::cong_cost_segment(const Segment& seg, int layer_id,
+                                       double eff_width) const {
+    bool   is_h      = (seg.start.y == seg.end.y);
+    double peak_cost = 0.0;
+    for (const auto& c : cuts_) {
+        if (c.layer_id != layer_id) continue;
+        int b = -1;
+        if (is_h && c.dir == LayerDir::VERTICAL) {
+            if (!h_seg_crosses_vcut(seg.start.x, seg.end.x, c.cut_coord)) continue;
+            b = find_band(/*is_vcut=*/true, seg.start.y);
+        } else if (!is_h && c.dir == LayerDir::HORIZONTAL) {
+            if (!v_seg_crosses_hcut(seg.start.y, seg.end.y, c.cut_coord)) continue;
+            b = find_band(/*is_vcut=*/false, seg.start.x);
+        }
+        if (b < 0 || b >= (int)c.band_cap.size()) continue;
+        double cap = c.band_cap[b];
+        if (cap <= 0.0) { return kCong_ * 9999.0; }
+        double u    = std::min((c.band_usage[b] + eff_width) / cap, 0.9999);
+        double cost = kCong_ * u / (1.0 - u);
+        peak_cost   = std::max(peak_cost, cost);
+    }
+    return peak_cost;
 }
 
-// Returns 0 for the top layer, kBase + small mismatch for alternates.
-double GlobalRouter::segment_affinity(double span_norm, int layer_id,
-                                      int top_layer,
-                                      const std::vector<int>& alt_layers) const {
-    if (layer_id == top_layer || alt_layers.empty()) return 0.0;
-    auto it = std::find(alt_layers.begin(), alt_layers.end(), layer_id);
-    if (it == alt_layers.end()) return 0.0;
-    int actual_idx = (int)(it - alt_layers.begin());
-    int pref_idx   = preferred_alt_idx(span_norm, (int)alt_layers.size());
-    return kBase + kMismatch * std::abs(actual_idx - pref_idx);
+// Span-mismatch cost: kSpan(layer) * excess outside [span_min, span_max].
+double GlobalRouter::span_cost_for(double seg_span, int layer_id) const {
+    const Layer* layer = layers_.get_layer(layer_id);
+    if (!layer) return 0.0;
+    double k      = (layer->kspan_override >= 0.0) ? layer->kspan_override : kSpan_;
+    double excess = std::max({0.0,
+                              (double)layer->span_min - seg_span,
+                              seg_span - (double)layer->span_max});
+    return k * excess;
 }
 
 // ---------------------------------------------------------------------------
@@ -286,23 +310,9 @@ std::vector<BundleAssignment> GlobalRouter::optimize_topologies(
     if (h_layers.empty()) { h_layers.push_back(4); top_h = 4; }
     if (v_layers.empty()) { v_layers.push_back(5); top_v = 5; }
 
-    std::vector<int> alt_h, alt_v;
-    for (int id : h_layers) if (id != top_h) alt_h.push_back(id);
-    for (int id : v_layers) if (id != top_v) alt_v.push_back(id);
-
-    // Pre-compute max spans across all candidates for span-norm affinity.
-    double max_h_span = 1.0, max_v_span = 1.0;
-    for (const auto& bw : bundles) {
-        for (const auto& cand : bw.candidates) {
-            for (const auto& seg : cand.segments) {
-                bool is_h = (seg.start.y == seg.end.y);
-                if (is_h)
-                    max_h_span = std::max(max_h_span, (double)std::abs(seg.end.x - seg.start.x));
-                else
-                    max_v_span = std::max(max_v_span, (double)std::abs(seg.end.y - seg.start.y));
-            }
-        }
-    }
+    // Reversed copies: highest layer ID first so ties break toward higher metal.
+    auto h_layers_rev = h_layers; std::reverse(h_layers_rev.begin(), h_layers_rev.end());
+    auto v_layers_rev = v_layers; std::reverse(v_layers_rev.begin(), v_layers_rev.end());
 
     // Process widest buses first so they claim the best paths early.
     std::vector<int> order(bundles.size());
@@ -345,23 +355,23 @@ std::vector<BundleAssignment> GlobalRouter::optimize_topologies(
             for (int si = 0; si < (int)topo.segments.size(); ++si) {
                 const Segment& seg = topo.segments[si];
                 bool  is_h         = (seg.start.y == seg.end.y);
-                int   top_layer    = is_h ? top_h : top_v;
-                const auto& layers_for_dir = is_h ? h_layers : v_layers;
-                const auto& alt            = is_h ? alt_h    : alt_v;
+                const auto& layers_rev = is_h ? h_layers_rev : v_layers_rev;
                 double seg_span = is_h
                     ? (double)std::abs(seg.end.x - seg.start.x)
                     : (double)std::abs(seg.end.y - seg.start.y);
-                double span_norm = seg_span / (is_h ? max_h_span : max_v_span);
 
-                int    best_lid = layers_for_dir[0];
+                int    best_lid = layers_rev[0];
                 double best_s   = std::numeric_limits<double>::max();
                 double best_ov  = 0.0;
 
-                for (int lid : layers_for_dir) {
-                    double eff = bw.width * get_dilution(lid);
-                    double ov  = score_segment(seg, lid, eff);
-                    double aff = segment_affinity(span_norm, lid, top_layer, alt);
-                    double s   = ov + aff;
+                // Iterate highest-ID first so equal-cost layers prefer higher metal.
+                for (int lid : layers_rev) {
+                    double eff  = bw.width * get_dilution(lid);
+                    double cong = cong_cost_segment(seg, lid, eff);
+                    double span = span_cost_for(seg_span, lid);
+                    double base = layers_.is_top(lid) ? 0.0 : base_cost_non_top_;
+                    double s    = cong + span + base;
+                    double ov   = score_segment(seg, lid, eff);  // raw overflow for logging
                     if (s < best_s) { best_s = s; best_lid = lid; best_ov = ov; }
                 }
 
modified   buda_system_v2/src/global_router.h
@@ -42,6 +42,11 @@ class GlobalRouter {
 public:
     GlobalRouter(const Floorplan& fp, const LayerStack& layers);
     void set_layer_overhead(int layer_id, double overhead_percent);
+    // Tune global planner knobs.  Recognised names:
+    //   "kCong"            — congestion cost coefficient (default 1.0)
+    //   "kSpan"            — span-mismatch cost per layout-unit (default 0.001)
+    //   "base_cost_non_top"— flat penalty for non-TOP layers (default 0.5)
+    void set_planner_param(const std::string& name, double value);
     void build_congestion_map();
     std::vector<BundleAssignment> optimize_topologies(
             std::vector<BundleWrapper>& bundles, int max_iterations);
@@ -50,24 +55,28 @@ public:
     const std::vector<int>& get_y_grid() const { return y_grid_; }
 
 private:
-    // Rebuild cuts_ from the current x_grid_ / y_grid_ (without resetting the grids).
     void _rebuild_cuts();
-    // 2D per-segment scoring/application.
+    // Hyperbolic congestion cost: kCong * u/(1-u) where u = (usage+eff)/cap.
+    double cong_cost_segment(const Segment& seg, int layer_id, double eff_width) const;
+    // Raw overflow for logging (usage+eff - cap, clamped to 0).
     double score_segment(const Segment& seg, int layer_id, double eff_width) const;
     void   apply_segment(const Segment& seg, int layer_id, double eff_width);
+    // Span-mismatch cost: kSpan(layer) * max(0, span_min-span, span-span_max).
+    double span_cost_for(double seg_span, int layer_id) const;
 
-    // Band lookup: for a V-cut (is_vcut=true) look up in y_grid_, else x_grid_.
-    int  find_band(bool is_vcut, int perp_pos) const;
+    int    find_band(bool is_vcut, int perp_pos) const;
     double get_dilution(int layer_id) const;
-    double segment_affinity(double span_norm, int layer_id,
-                            int top_layer,
-                            const std::vector<int>& alt_layers) const;
 
     const Floorplan&  floorplan_;
     const LayerStack& layers_;
     std::map<int, double> layer_dilution_factors_;
     std::vector<GlobalCut> cuts_;
-    std::vector<int> x_grid_, y_grid_;  // Hanan grids, populated by build_congestion_map
+    std::vector<int> x_grid_, y_grid_;
+
+    // Tunable cost coefficients.
+    double kCong_             = 1.0;
+    double kSpan_             = 0.001;
+    double base_cost_non_top_ = 0.5;
 };
 
 } // namespace interconnect
modified   buda_system_v2/src/layering.cpp
@@ -7,6 +7,20 @@ void LayerStack::add_layer(int id, const std::string& name, LayerDir dir, LayerT
         else top_vert_id_ = id;
     }
 }
+void LayerStack::set_layer_span(int id, int span_min, int span_max) {
+    for (auto& l : layers_) if (l.id == id) { l.span_min = span_min; l.span_max = span_max; return; }
+}
+void LayerStack::set_layer_kspan(int id, double kspan) {
+    for (auto& l : layers_) if (l.id == id) { l.kspan_override = kspan; return; }
+}
+const Layer* LayerStack::get_layer(int id) const {
+    for (const auto& l : layers_) if (l.id == id) return &l;
+    return nullptr;
+}
+bool LayerStack::is_top(int id) const {
+    const Layer* l = get_layer(id);
+    return l && l->type == LayerType::TOP;
+}
 LayerDir LayerStack::get_layer_dir(int id) const {
     for(auto& l : layers_) if(l.id == id) return l.dir;
     return LayerDir::HORIZONTAL;
@@ -16,14 +30,13 @@ LayerType LayerStack::get_layer_type(int id) const {
     return LayerType::LOW;
 }
 std::vector<int> LayerStack::get_layer_ids_preferred(LayerDir dir) const {
-    int top_id = get_top_layer(dir);
     std::vector<int> ids = get_layer_ids_by_dir(dir);
-    // Stable-sort: TOP layer first, LOW layers keep ascending order after it.
+    // Stable-sort: TOP layers first (ascending), then non-TOP (ascending).
     std::stable_sort(ids.begin(), ids.end(), [&](int a, int b) {
-        bool a_top = (a == top_id);
-        bool b_top = (b == top_id);
+        bool a_top = is_top(a);
+        bool b_top = is_top(b);
         if (a_top != b_top) return a_top;
-        return false; // preserve original ascending order for LOW layers
+        return false; // preserve original ascending order within each group
     });
     return ids;
 }
modified   buda_system_v2/src/layering.h
@@ -5,22 +5,36 @@
 #include "topology.h"
 namespace interconnect {
 enum class LayerDir { HORIZONTAL, VERTICAL };
-enum class LayerType { TOP, LOW };
+enum class LayerType { TOP, LOW };  // LOW kept for backward compatibility; treated as non-TOP
 struct Layer {
     int id;
     std::string name;
     LayerDir dir;
     LayerType type;
+    // Span preference: span_cost = kSpan * max(0, span_min-span, span-span_max).
+    // Defaults give zero span cost for any segment length.
+    int    span_min      = 0;
+    int    span_max      = 1'000'000'000;
+    // Per-layer kSpan override; negative means "use global kSpan".
+    double kspan_override = -1.0;
 };
 class LayerStack {
 public:
     void add_layer(int id, const std::string& name, LayerDir dir, LayerType type);
-    LayerDir  get_layer_dir(int id) const;
-    LayerType get_layer_type(int id) const;
+    // Set span preference window for an already-added layer.
+    void set_layer_span(int id, int span_min, int span_max);
+    // Override the global kSpan coefficient for one layer.
+    void set_layer_kspan(int id, double kspan);
+
+    const Layer* get_layer(int id) const;
+    bool         is_top(int id) const;
+    LayerDir     get_layer_dir(int id) const;
+    LayerType    get_layer_type(int id) const;
+    // Returns the last-added TOP layer for the given direction (-1 if none).
     int get_top_layer(LayerDir dir) const;
     // Returns IDs of all layers with the given direction, sorted ascending.
     std::vector<int> get_layer_ids_by_dir(LayerDir dir) const;
-    // Returns IDs sorted: TOP layer first, then LOW layers ascending by ID.
+    // Returns IDs sorted: TOP layers first (ascending), then non-TOP (ascending).
     std::vector<int> get_layer_ids_preferred(LayerDir dir) const;
 private:
     std::vector<Layer> layers_;
modified   buda_system_v2/src/topology.cpp
@@ -7,6 +7,13 @@ namespace interconnect {
 void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
     blocks_[name] = Rect{x1, y1, x2, y2};
 }
+void Floorplan::set_block_corner_margin(const std::string& name, int dx, int dy) {
+    corner_margins_[name] = BlockCornerMargin{dx, dy};
+}
+BlockCornerMargin Floorplan::get_block_corner_margin(const std::string& name) const {
+    auto it = corner_margins_.find(name);
+    return (it != corner_margins_.end()) ? it->second : BlockCornerMargin{};
+}
 Rect Floorplan::get_block_bounds(const std::string& name) const {
     if (blocks_.count(name)) return blocks_.at(name);
     return Rect{0,0,0,0};
modified   buda_system_v2/src/topology.h
@@ -40,10 +40,23 @@ struct Topology {
     // scanning the entire floorplan.  Key = segment index.
     std::map<int, SegEndpoints> seg_busterms;
 };
+// Per-block corner margin: keeps trunk/stub connections away from block corners.
+// dx: margin along the horizontal direction (applied to top/bottom faces, extent in X).
+// dy: margin along the vertical direction   (applied to left/right  faces, extent in Y).
+// A margin of 0 means no constraint beyond the face extent (default).
+struct BlockCornerMargin {
+    int dx = 0;
+    int dy = 0;
+};
+
 class Floorplan {
 public:
     void add_block(const std::string& name, int x1, int y1, int x2, int y2);
+    // Set corner margin for a previously-added block (absolute units).
+    void set_block_corner_margin(const std::string& name, int dx, int dy);
     Rect get_block_bounds(const std::string& name) const;
+    // Returns {0,0} for blocks without an explicit margin.
+    BlockCornerMargin get_block_corner_margin(const std::string& name) const;
     void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
     std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
         std::vector<std::pair<std::string, Rect>> res;
@@ -52,6 +65,7 @@ public:
     }
 private:
     std::map<std::string, Rect> blocks_;
+    std::map<std::string, BlockCornerMargin> corner_margins_;
 };
 class TopologyGenerator {
 public:

Recent commits
04dac1a origin/main Test cases for UI improvement and visual observations
92949d6 Fix busterm ambiguity, NUTS slide ranges, and add viz keyboard nav
48b6717 Add Vias/Conns toggle button to left panel
b02501a Fix via duplication, NUTS misalignment, and marker sizing
60a17fd Use ConnTopology to correctly classify via vs busterm-conn markers
2ce023a Replace via dots with X-in-square; add filled-square busterm conn markers
a6bd8f0 Clip labels to axes; fix Cmd+A after bundle zoom
996340c Add Cmd/Ctrl+A shortcut to reset view (home button)
fc94f94 Don't clear bundle selection while zoom/pan tool is active
7098cf7 Add 'All' master toggle to left panel

