// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.render

import scala.scalajs.js

/** Display-geometry math ported from `src/viz_explorer/draw.py` +
  * `src/viz_main/draw_abstract.py`.  Pure geometry over the serialized ConnSeg
  * `analysis`; it never makes routing decisions (all of those stay on the
  * server) — it only computes presentation offsets so junctions visually meet.
  *
  * Validated against the golden-JSON snapshot
  * (`test/tests/data/web_golden/b44_generation.json`), which is the same
  * payload the vanilla reference client renders — so the two clients cannot
  * drift from the server contract.
  */
object DisplayGeom {

  /** Per-segment display coordinates for a candidate's `analysis` list.
    * `perp(i)` is the segment's display perpendicular position (slide-window
    * center, or nominal when unbounded); `alo(i)`/`ahi(i)` are its along-extent
    * after snapping each connecting endpoint out to its partner's display perp.
    */
  final case class Placed(perp: Array[Double], alo: Array[Double], ahi: Array[Double])

  private def num(o: js.Dynamic, k: String): Double = o.selectDynamic(k).asInstanceOf[Double]
  private def isNull(o: js.Dynamic, k: String): Boolean = {
    val v = o.selectDynamic(k); js.isUndefined(v) || v == null
  }

  def compute(analysis: js.Array[js.Dynamic]): Placed = {
    val n = analysis.length
    val perp = Array.tabulate(n) { i =>
      val a = analysis(i)
      if (!isNull(a, "perp_lo") && !isNull(a, "perp_hi"))
        (num(a, "perp_lo") + num(a, "perp_hi")) / 2
      else num(a, "perp_pos")
    }
    val alo = Array.tabulate(n)(i => num(analysis(i), "along_lo"))
    val ahi = Array.tabulate(n)(i => num(analysis(i), "along_hi"))
    for (i <- 0 until n) {
      val aLo = num(analysis(i), "along_lo")
      val aHi = num(analysis(i), "along_hi")
      val conns = analysis(i).conns.asInstanceOf[js.Array[js.Dynamic]]
      conns.foreach { c =>
        val kind = c.kind.asInstanceOf[String]
        val j = c.seg_idx.asInstanceOf[Int]
        if (kind == "SEG" && j >= 0 && j < n) {
          val adj = perp(j)
          val at = num(c, "at_pos")
          // draw.py Pass B: snap ONLY a connection incident to an endpoint
          // (at_pos within 1 unit of along_lo/hi) and REPLACE that endpoint with
          // the partner's display perp. NOT min/max — extend-only overdraws when
          // the true endpoint moves inward, and snapping a mid-segment T-tap (no
          // endpoint gate) stretches the line to a far partner (overextension).
          if (math.abs(at - aLo) <= 1) alo(i) = adj
          else if (math.abs(at - aHi) <= 1) ahi(i) = adj
        }
      }
    }
    Placed(perp, alo, ahi)
  }
}
