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
      // draw.py Pass B (viz_common.snap_endpoint_extents): snap ONLY a
      // connection incident to an endpoint (at_pos within 1 unit of
      // along_lo/hi) to the partner's display perp.  Snapping a mid-segment
      // T-tap (no endpoint gate) stretches the line to a far partner — the
      // overextension bug.  Several partners can be incident to ONE endpoint
      // (the +/-1 tolerance), so take the EXTREME of them: min at the low end,
      // max at the high end.  Doing it per match let the LAST one win and an
      // interior tap could drag the end inside the outermost one, drawing
      // extreme stubs detached from a trunk they are connected to (issue #554).
      // One partner => same as replacing.  NOT extend-only: the extreme is over
      // the incident PARTNERS, never against the segment's own along value.
      val loAdj = scala.collection.mutable.ArrayBuffer.empty[Double]
      val hiAdj = scala.collection.mutable.ArrayBuffer.empty[Double]
      conns.foreach { c =>
        val kind = c.kind.asInstanceOf[String]
        val j = c.seg_idx.asInstanceOf[Int]
        if (kind == "SEG" && j >= 0 && j < n) {
          val adj = perp(j)
          val at = num(c, "at_pos")
          if (math.abs(at - aLo) <= 1) loAdj += adj
          else if (math.abs(at - aHi) <= 1) hiAdj += adj
        }
      }
      if (loAdj.nonEmpty) alo(i) = loAdj.min
      if (hiAdj.nonEmpty) ahi(i) = hiAdj.max
    }
    Placed(perp, alo, ahi)
  }
}
