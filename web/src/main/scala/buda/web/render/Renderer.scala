// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.render

import org.scalajs.dom
import scala.scalajs.js

/** SVG renderer for the generation-stage payload.
  *
  * Draws the floorplan (blocks + keepouts), the Hanan grid, and one candidate's
  * segments.  BUDA's y grows UP while SVG's grows DOWN, so the scene lives in a
  * `scale(1 -1)` group and labels are locally un-flipped.
  *
  * Phase 1 draws segments at NOMINAL coordinates.  The display-geometry math
  * (`DisplayGeom`: perp-centering within `[perp_lo, perp_hi]`, endpoint snapping
  * over `conns[].at_pos`, slide-band extents, pull arrows, via dedup) is the
  * Phase-2 port from `src/viz_explorer/draw.py` + `src/viz_main/draw_abstract.py`,
  * validated against the golden-JSON snapshot.
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

  /** Render `payload` (from ApiClient.renderGeneration) into `svg`, showing
    * candidate index `cand` of the first bundle. */
  def draw(svg: dom.svg.SVG, payload: js.Dynamic, cand: Int): Unit = {
    svg.innerHTML = ""
    if (js.isUndefined(payload) || payload == null) return
    val fp = payload.floorplan
    val blocks = fp.blocks.asInstanceOf[js.Array[js.Dynamic]]

    var (x1, y1, x2, y2) = (1e18, 1e18, -1e18, -1e18)
    blocks.foreach { b =>
      x1 = math.min(x1, d(b.bbox, "x1")); y1 = math.min(y1, d(b.bbox, "y1"))
      x2 = math.max(x2, d(b.bbox, "x2")); y2 = math.max(y2, d(b.bbox, "y2"))
    }
    if (x1 > x2) { x1 = 0; y1 = 0; x2 = 1000; y2 = 1000 }
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
    fp.keepouts.asInstanceOf[js.Array[js.Dynamic]].foreach { k =>
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

    val bundles = payload.bundles.asInstanceOf[js.Array[js.Dynamic]]
    if (bundles.isEmpty) return
    val cands = bundles(0).candidates.asInstanceOf[js.Array[js.Dynamic]]
    if (cands.isEmpty) return
    val c = cands(math.max(0, math.min(cand, cands.length - 1)))
    // DisplayGeom: draw each segment at its slide-window center with endpoints
    // snapped to connected partners, so junctions visually meet.
    val analysis = c.analysis.asInstanceOf[js.Array[js.Dynamic]]
    val segs = c.segments.asInstanceOf[js.Array[js.Dynamic]]
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
  }
}
