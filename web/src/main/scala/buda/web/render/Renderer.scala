// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.render

import org.scalajs.dom
import scala.scalajs.js

/** SVG renderer for the three BUDA web views (generation / nuts / detailed).
  *
  * Draws the floorplan (blocks + keepouts), the Hanan grid, and the view-specific
  * routing geometry.  BUDA's y grows UP while SVG's grows DOWN, so the scene lives
  * in a `scale(1 -1)` group and labels are locally un-flipped.  This mirrors the
  * vanilla reference client (`src/web/static/index.html`) draw functions verbatim,
  * so the two clients render the same server payloads identically.
  *
  * The display-geometry math (`DisplayGeom`: perp-centering within
  * `[perp_lo, perp_hi]`, endpoint snapping over `conns[].at_pos`) is validated
  * against the golden-JSON snapshot (`test/tests/data/web_golden/b44_generation.json`).
  */
object Renderer {
  private val NS = "http://www.w3.org/2000/svg"

  private def el(tag: String, attrs: (String, Any)*): dom.svg.Element = {
    val e = dom.document.createElementNS(NS, tag).asInstanceOf[dom.svg.Element]
    attrs.foreach { case (k, v) => e.setAttribute(k, v.toString) }
    e
  }

  private def d(o: js.Dynamic, k: String): Double =
    o.selectDynamic(k).asInstanceOf[Double]

  private def arr(o: js.Dynamic, k: String): js.Array[js.Dynamic] =
    o.selectDynamic(k).asInstanceOf[js.Array[js.Dynamic]]

  private def defined(v: js.Dynamic): Boolean = !js.isUndefined(v) && v != null

  /** One restored legacy TEG bridge, ready to draw. */
  private final case class BridgeWire(x1: Double, y1: Double, x2: Double,
                                      y2: Double, label: String,
                                      bundleId: Option[Int])

  /** Restored legacy TEG bridges as drawable wires (teg_multirect_status.md
    * limitation 6).  A candidate restored from a pre-emission checkpoint carries
    * UNREALIZED metal that the `TEG_OPEN` audit reports as "declared bridge is
    * unrealized"; generation emits none, so a live design yields an empty list
    * and nothing is drawn.
    *
    * Mirrors `legacyBridgeWires` in the reference client
    * (`src/web/static/index.html`) — including the label text, which is the same
    * string `viz_common.draw_legacy_bridges` annotates with.  Accepts both
    * payload shapes: the generation view's `{block_name: segment}` map (drawn in
    * sorted block-name order, as matplotlib does) and the NUTS view's flat
    * `legacy_bridges` list, which the server already sorts. */
  private def legacyBridgeWires(bridges: js.Dynamic): List[BridgeWire] = {
    if (!defined(bridges)) return Nil
    // Explicit index loops over the JS values (no Scala collection ops on
    // js.Array, no implicit conversions) so the shape of the payload is the
    // only thing this depends on.
    val entries = scala.collection.mutable.ArrayBuffer.empty[(String, js.Dynamic)]
    if (js.Array.isArray(bridges)) {
      val a = bridges.asInstanceOf[js.Array[js.Dynamic]]
      var i = 0
      while (i < a.length) {
        entries += ((a(i).block_name.asInstanceOf[String], a(i))); i += 1
      }
    } else {
      val keys = js.Object.keys(bridges.asInstanceOf[js.Object])
      val names = scala.collection.mutable.ArrayBuffer.empty[String]
      var i = 0
      while (i < keys.length) { names += keys(i); i += 1 }
      names.sorted.foreach(k => entries += ((k, bridges.selectDynamic(k))))
    }
    val out = scala.collection.mutable.ListBuffer.empty[BridgeWire]
    entries.foreach { case (name, seg) =>
      if (defined(seg) && defined(seg.start) && defined(seg.end))
        // `bundle_id` rides along so the focus view can dim a bridge with its
        // bundle, like the bits and vias beside it.  The generation map is
        // per-candidate and carries none — one bundle in that view, so None
        // reads as "always focused".
        out += BridgeWire(d(seg.start, "x"), d(seg.start, "y"),
                          d(seg.end, "x"), d(seg.end, "y"),
                          s"unrealized bridge (legacy checkpoint): $name",
                          if (defined(seg.selectDynamic("bundle_id")))
                            Some(d(seg, "bundle_id").toInt) else None)
    }
    out.toList
  }

  /** Draw `legacyBridgeWires`: a dashed off-palette wire plus its label,
    * locally un-flipped like the block labels so the text reads upright. */
  private def drawLegacyBridges(g: dom.svg.Element, bridges: js.Dynamic,
                               focus: Option[Int] = None): Unit =
    legacyBridgeWires(bridges).foreach { w =>
      val op = w.bundleId.map(b => bundleAlpha(b.toDouble, focus)).getOrElse(1.0)
      g.appendChild(el("line", "class" -> "legacybridge", "opacity" -> op,
        "x1" -> w.x1, "y1" -> w.y1, "x2" -> w.x2, "y2" -> w.y2))
      val lg = el("g", "opacity" -> op, "transform" ->
        s"translate(${(w.x1 + w.x2) / 2} ${(w.y1 + w.y2) / 2}) scale(1 -1)")
      val t = el("text", "class" -> "legacybridgelbl", "x" -> 0, "y" -> -4,
        "text-anchor" -> "middle")
      t.textContent = w.label
      lg.appendChild(t); g.appendChild(lg)
    }

  /** A placed segment centerline: perp coord = track_position, extent = span_lo..hi. */
  private def placedLine(s: js.Dynamic): (Double, Double, Double, Double) = {
    val tp = d(s, "track_position"); val lo = d(s, "span_lo"); val hi = d(s, "span_hi")
    if (s.horiz.asInstanceOf[Boolean]) (lo, tp, hi, tp) else (tp, lo, tp, hi)
  }

  /** A placed segment footprint rect (perp-centered on track_position). */
  private def placedRect(s: js.Dynamic): (Double, Double, Double, Double) = {
    val tp = d(s, "track_position"); val lo = d(s, "span_lo"); val hi = d(s, "span_hi")
    val w = d(s, "width")
    if (s.horiz.asInstanceOf[Boolean]) (lo, tp - w / 2, hi - lo, w)
    else (tp - w / 2, lo, w, hi - lo)
  }

  /** Opacity for a segment of bundle `bid` under the current focus: 1.0 when no
    * bundle is isolated (`focus` is None) or this IS the isolated one, else 0.1 —
    * so `n`/`p` stepping in the NUTS/detailed views dims every other bundle. */
  private def bundleAlpha(bid: Double, focus: Option[Int]): Double =
    if (focus.isEmpty || focus.contains(bid.toInt)) 1.0 else 0.1

  /** A marker ring carrying a <title>, so hovering says which kind it is
    * instead of leaving the colours to memory. */
  private def ring(cx: Double, cy: Double, cls: String, tip: String,
                   r: Double): dom.Element = {
    val c = el("circle", "class" -> cls, "cx" -> cx, "cy" -> cy, "r" -> r)
    val t = el("title")
    t.textContent = tip
    c.appendChild(t)
    c
  }

  /** The view box for "zoom to this bundle": the geometry of the one bundle on
    * screen.  None when nothing is isolated (NUTS/detailed showing ALL
    * bundles), so the toggle then keeps the full floorplan rather than
    * pretending to zoom.  Twin of the reference client's `zoomBox()`. */
  private def zoomBox(payload: js.Dynamic, view: String, cand: Int,
                      edit: js.Dynamic,
                      focus: Option[Int]): Option[(Double, Double, Double, Double)] = {
    val acc = scala.collection.mutable.ArrayBuffer[(Double, Double, Double, Double)]()
    def add(ax1: Double, ay1: Double, ax2: Double, ay2: Double): Unit =
      acc += ((math.min(ax1, ax2), math.min(ay1, ay2),
               math.max(ax1, ax2), math.max(ay1, ay2)))
    if (view == "generation") {
      val editing = defined(edit) && edit.open.asInstanceOf[Boolean]
      val bundles = arr(payload, "bundles")
      val c: js.Dynamic =
        if (editing) edit.topology
        else if (bundles.isEmpty) null
        else {
          val cs = arr(bundles(0), "candidates")
          if (cand >= 0 && cand < cs.length) cs(cand) else null
        }
      if (c == null) return None
      arr(c, "segments").foreach { sg =>
        add(d(sg.start, "x"), d(sg.start, "y"), d(sg.end, "x"), d(sg.end, "y"))
      }
      arr(c, "seg_busterms").foreach { bt =>
        for (side <- Seq("start", "end")) {
          val b = bt.selectDynamic(side)
          if (defined(b))
            add(d(b.bbox, "x1"), d(b.bbox, "y1"), d(b.bbox, "x2"), d(b.bbox, "y2"))
        }
      }
    } else {
      if (focus.isEmpty) return None
      val segs =
        if (view == "nuts") {
          val n = payload.selectDynamic("nuts")
          if (defined(n)) arr(n, "segments") else js.Array[js.Dynamic]()
        } else {
          val dd = payload.selectDynamic("detailed")
          if (defined(dd)) arr(dd, "net_segments") else js.Array[js.Dynamic]()
        }
      segs.foreach { sg =>
        if (focus.contains(d(sg, "bundle_id").toInt)) {
          val (a, b, c, dd2) = placedLine(sg)
          add(a, b, c, dd2)
        }
      }
    }
    if (acc.isEmpty) None
    else Some((acc.map(_._1).min, acc.map(_._2).min,
               acc.map(_._3).max, acc.map(_._4).max))
  }

  /** Render `payload` into `svg` for the given `view` ("generation"/"nuts"/
    * "detailed").  For the generation view, shows candidate `cand` of the first
    * bundle, or the `edit` working copy when an edit session is open.  In the
    * NUTS/detailed views, `focus` (a bundle id, or None = show all) dims every
    * other bundle so one can be isolated.  Returns the candidate-bar label (empty
    * unless the generation view has something to show). */
  def draw(svg: dom.svg.SVG, payload: js.Dynamic, view: String, cand: Int,
           edit: js.Dynamic, focus: Option[Int] = None,
           zoom: Boolean = false): String = {
    svg.innerHTML = ""
    if (js.isUndefined(payload) || payload == null) return ""
    val fp = payload.floorplan
    val blocks = arr(fp, "blocks")

    var (x1, y1, x2, y2) = (1e18, 1e18, -1e18, -1e18)
    blocks.foreach { b =>
      x1 = math.min(x1, d(b.bbox, "x1")); y1 = math.min(y1, d(b.bbox, "y1"))
      x2 = math.max(x2, d(b.bbox, "x2")); y2 = math.max(y2, d(b.bbox, "y2"))
    }
    if (x1 > x2) { x1 = 0; y1 = 0; x2 = 1000; y2 = 1000 }
    // Zoom to the ONE bundle on screen (the shown candidate in the generation
    // view, the focused bundle's placed wires otherwise).  None when there is
    // nothing isolated to zoom to, which keeps the full floorplan.
    if (zoom) zoomBox(payload, view, cand, edit, focus).foreach { case (a, b, c, dd) =>
      x1 = a; y1 = b; x2 = c; y2 = dd
    }
    val pad = math.max(x2 - x1, y2 - y1) * 0.06 + 50
    x1 -= pad; y1 -= pad; x2 += pad; y2 += pad
    val (w, h) = (x2 - x1, y2 - y1)
    svg.setAttribute("viewBox", s"0 0 $w $h")
    val g = el("g", "transform" -> s"translate(${-x1} $y2) scale(1 -1)")
    svg.appendChild(g)

    val hanan = payload.hanan
    hanan.xs.asInstanceOf[js.Array[Double]].foreach { xv =>
      g.appendChild(el("line", "class" -> "hanan", "x1" -> xv, "y1" -> y1, "x2" -> xv, "y2" -> y2))
    }
    hanan.ys.asInstanceOf[js.Array[Double]].foreach { yv =>
      g.appendChild(el("line", "class" -> "hanan", "x1" -> x1, "y1" -> yv, "x2" -> x2, "y2" -> yv))
    }
    arr(fp, "keepouts").foreach { k =>
      g.appendChild(el("rect", "class" -> "keepout",
        "x" -> d(k.bbox, "x1"), "y" -> d(k.bbox, "y1"),
        "width" -> (d(k.bbox, "x2") - d(k.bbox, "x1")),
        "height" -> (d(k.bbox, "y2") - d(k.bbox, "y1"))))
    }
    blocks.foreach { b =>
      val container = b.is_container.asInstanceOf[Boolean]
      val rects = b.rects.asInstanceOf[js.Array[js.Array[Double]]]
      if (rects.length > 0) {
        // Multi-rect / TEG block: draw each real rect solid + outline the bbox,
        // so gaps (valid pass-through channels) don't read as block area.
        rects.foreach { r =>
          g.appendChild(el("rect", "class" -> "blk",
            "x" -> r(0), "y" -> r(1), "width" -> (r(2) - r(0)), "height" -> (r(3) - r(1))))
        }
        g.appendChild(el("rect", "class" -> "blk container",
          "x" -> d(b.bbox, "x1"), "y" -> d(b.bbox, "y1"),
          "width" -> (d(b.bbox, "x2") - d(b.bbox, "x1")),
          "height" -> (d(b.bbox, "y2") - d(b.bbox, "y1"))))
      } else {
        g.appendChild(el("rect", "class" -> ("blk" + (if (container) " container" else "")),
          "x" -> d(b.bbox, "x1"), "y" -> d(b.bbox, "y1"),
          "width" -> (d(b.bbox, "x2") - d(b.bbox, "x1")),
          "height" -> (d(b.bbox, "y2") - d(b.bbox, "y1"))))
      }
      val lg = el("g", "transform" ->
        s"translate(${d(b.bbox, "x1") + 4} ${d(b.bbox, "y2") - 12}) scale(1 -1)")
      val t = el("text", "class" -> "blklbl", "x" -> 0, "y" -> 0)
      t.textContent = b.name.asInstanceOf[String]
      lg.appendChild(t); g.appendChild(lg)
    }

    view match {
      case "nuts"     => drawNuts(g, payload, focus); ""
      case "detailed" => drawDetailed(g, payload, math.max(w, h), focus); ""
      case _          => drawGeneration(g, payload, cand, edit, w, h)
    }
  }

  private def drawGeneration(g: dom.svg.Element, payload: js.Dynamic, cand: Int,
                             edit: js.Dynamic, w: Double, h: Double): String = {
    var lbl = ""
    val editing = defined(edit) && edit.open.asInstanceOf[Boolean]
    val c: js.Dynamic =
      if (editing) {
        val topo = edit.topology
        lbl = s"EDITING bundle ${edit.bundle_id} · ${arr(topo, "segments").length} seg(s)"
        topo
      } else {
        val bundles = arr(payload, "bundles")
        if (bundles.isEmpty) return lbl
        val bundle = bundles(0)
        val cands = arr(bundle, "candidates")
        // A degenerate placement can leave a bundle with NO candidates at all;
        // the bundle stepper can land on one, so say so rather than draw nothing.
        if (cands.isEmpty) return "no candidates"
        val idx = math.max(0, math.min(cand, cands.length - 1))
        val cc = cands(idx)
        val pin = pinSuffix(payload, bundle.id.asInstanceOf[Double].toInt, idx)
        // The bundle id lives in #bundlelbl now, so it is not repeated here.
        lbl = s"cand ${idx + 1}/${cands.length} · ${cc.`type`} · WL ${cc.estimated_wirelength}$pin"
        cc
      }

    val analysis = arr(c, "analysis")
    val segs = arr(c, "segments")
    if (analysis.isEmpty && segs.isEmpty) return lbl
    val placed = DisplayGeom.compute(analysis)
    for (i <- 0 until analysis.length) {
      val a = analysis(i)
      val horiz = a.horiz.asInstanceOf[Boolean]
      val jog = segs(i).is_jog.asInstanceOf[Boolean]
      val cls = "seg " + (if (jog) "jog" else if (horiz) "H" else "V")
      val p = placed.perp(i)
      if (horiz)
        g.appendChild(el("line", "class" -> cls,
          "x1" -> placed.alo(i), "y1" -> p, "x2" -> placed.ahi(i), "y2" -> p))
      else
        g.appendChild(el("line", "class" -> cls,
          "x1" -> p, "y1" -> placed.alo(i), "x2" -> p, "y2" -> placed.ahi(i)))
    }
    // Busterm taps: a small circle at each tapped block's center.
    val r = math.max(w, h) * 0.008
    arr(c, "seg_busterms").foreach { bt =>
      for (side <- Seq("start", "end")) {
        val b = bt.selectDynamic(side)
        if (defined(b)) {
          g.appendChild(ring((d(b.bbox, "x1") + d(b.bbox, "x2")) / 2,
            (d(b.bbox, "y1") + d(b.bbox, "y2")) / 2, "rcv",
            "busterm tap: " + b.block_name.asInstanceOf[String], r))
        }
      }
    }
    // The bundle's own blocks this candidate passes THROUGH rather than taps
    // (the serializer's `passthru_blocks`, derived by the same predicate
    // `dump_topologies --conn` prints).  Dashed and slightly larger, so a block
    // both tapped and crossed shows both rings.
    val byName = scala.collection.mutable.Map[String, js.Dynamic]()
    arr(payload.floorplan, "blocks").foreach { b =>
      byName(b.name.asInstanceOf[String]) = b
    }
    val pt = c.selectDynamic("passthru_blocks")
    if (defined(pt)) pt.asInstanceOf[js.Array[String]].foreach { name =>
      byName.get(name).foreach { b =>
        g.appendChild(ring((d(b.bbox, "x1") + d(b.bbox, "x2")) / 2,
          (d(b.bbox, "y1") + d(b.bbox, "y2")) / 2, "passthru",
          "pass-through (crossed, not tapped): " + name, r * 1.45))
      }
    }
    drawLegacyBridges(g, c.selectDynamic("bridge_segments"))
    lbl
  }

  /** " 📌PINNED" when the shown candidate is the pinned selection, else "". */
  /** serialize_state() lists EVERY bundle, not just the rendered one, so the shown
    * bundle's digest must be looked up by id — bundles(0) was only ever right
    * while the view was stuck on the first bundle. */
  private def pinSuffix(payload: js.Dynamic, bundleId: Int, idx: Int): String = {
    val state = payload.selectDynamic("state")
    if (!defined(state)) return ""
    val sbs = arr(state, "bundles").filter(_.id.asInstanceOf[Double].toInt == bundleId)
    if (sbs.isEmpty) return ""
    val sb = sbs(0)
    val pinned = defined(sb.selectDynamic("pinned")) && sb.pinned.asInstanceOf[Boolean]
    if (pinned && sb.selected_index.asInstanceOf[Int] == idx) " 📌PINNED" else ""
  }

  private def drawNuts(g: dom.svg.Element, payload: js.Dynamic, focus: Option[Int]): Unit = {
    val n = payload.selectDynamic("nuts")
    if (!defined(n)) return
    arr(n, "segments").foreach { s =>
      val op = bundleAlpha(d(s, "bundle_id"), focus)
      val (rx, ry, rw, rh) = placedRect(s)
      val hv = if (s.horiz.asInstanceOf[Boolean]) "H" else "V"
      g.appendChild(el("rect", "class" -> ("trackftp " + hv), "opacity" -> op, "x" -> rx, "y" -> ry, "width" -> rw, "height" -> rh))
      val (a, b, c, d2) = placedLine(s)
      val cls = "track " + hv
      g.appendChild(el("line", "class" -> cls, "opacity" -> op, "x1" -> a, "y1" -> b, "x2" -> c, "y2" -> d2))
    }
    // Overlaps stay full-opacity (congestion markers, not per-bundle geometry).
    arr(n, "overlap_details").foreach { o =>
      g.appendChild(el("rect", "class" -> "overlap",
        "x" -> d(o, "span_lo"), "y" -> d(o, "perp_lo"),
        "width" -> (d(o, "span_hi") - d(o, "span_lo")),
        "height" -> (d(o, "perp_hi") - d(o, "perp_lo"))))
    }
    // A restored legacy bridge is UNPLACED by definition, so it is drawn here
    // at its recorded nominal coordinates — unrealized metal beside the placed
    // tracks, exactly as the matplotlib NUTS view draws it.
    drawLegacyBridges(g, payload.selectDynamic("legacy_bridges"), focus)
  }

  private def drawDetailed(g: dom.svg.Element, payload: js.Dynamic, span: Double,
                           focus: Option[Int]): Unit = {
    val det = payload.selectDynamic("detailed")
    if (!defined(det)) return
    arr(det, "net_segments").foreach { s =>
      val (a, b, c, e) = placedLine(s)
      val cls = "bit " + (if (s.horiz.asInstanceOf[Boolean]) "H" else "V")
      g.appendChild(el("line", "class" -> cls, "opacity" -> bundleAlpha(d(s, "bundle_id"), focus),
        "x1" -> a, "y1" -> b, "x2" -> c, "y2" -> e))
    }
    val r = span * 0.004
    arr(det, "net_vias").foreach { v =>
      g.appendChild(el("rect", "class" -> "via", "opacity" -> bundleAlpha(d(v, "bundle_id"), focus),
        "x" -> (d(v, "x") - r), "y" -> (d(v, "y") - r), "width" -> (2 * r), "height" -> (2 * r)))
    }
  }
}
