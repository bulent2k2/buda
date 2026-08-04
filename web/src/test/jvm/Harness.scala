// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
//
// Runs buda.web.render.DisplayGeom.compute over fixtures fed in by
// test/tests/test_web_scala_port.py and prints the result for comparison
// against the authority (viz_common.snap_endpoint_extents).
//
// The I/O format is a trivial line protocol rather than JSON on purpose: the
// JDK ships no JSON parser, and hand-rolling one inside a test harness would
// add a second thing that can be wrong.  Python writes it; this parses it with
// split().
//
//   CAND <n_segments>
//   SEG  <perp_lo|NULL> <perp_hi|NULL> <perp_pos> <along_lo> <along_hi> <n_conns>
//   CONN <kind> <seg_idx> <at_pos>          (n_conns of these, after each SEG)
//
// Output, one line per segment, candidates in input order:
//
//   OUT <cand_idx> <seg_idx> <perp> <alo> <ahi>
package buda.web.test

import scala.scalajs.js
import buda.web.render.DisplayGeom

object Harness {

  private def cell(tok: String): Any =
    if (tok == "NULL") null else tok.toDouble

  def main(args: Array[String]): Unit = {
    val src = scala.io.Source.fromFile(args(0))
    val lines = try src.getLines().toVector finally src.close()

    var i = 0
    var candIdx = 0
    val out = new StringBuilder
    while (i < lines.length) {
      val head = lines(i).split("\\s+")
      require(head(0) == "CAND", s"expected CAND at line $i, got ${lines(i)}")
      val nSeg = head(1).toInt
      i += 1

      val segs = scala.collection.mutable.ArrayBuffer.empty[js.Dynamic]
      for (_ <- 0 until nSeg) {
        val s = lines(i).split("\\s+")
        require(s(0) == "SEG", s"expected SEG at line $i, got ${lines(i)}")
        i += 1
        val nConn = s(6).toInt
        val conns = scala.collection.mutable.ArrayBuffer.empty[js.Dynamic]
        for (_ <- 0 until nConn) {
          val c = lines(i).split("\\s+")
          require(c(0) == "CONN", s"expected CONN at line $i, got ${lines(i)}")
          i += 1
          conns += new js.Dynamic(Map(
            // seg_idx is Int because DisplayGeom casts it to Int; every other
            // numeric is Double, mirroring JS where all numbers are Doubles.
            "kind" -> c(1), "seg_idx" -> c(2).toInt, "at_pos" -> c(3).toDouble))
        }
        segs += new js.Dynamic(Map(
          "perp_lo" -> cell(s(1)), "perp_hi" -> cell(s(2)),
          "perp_pos" -> s(3).toDouble,
          "along_lo" -> s(4).toDouble, "along_hi" -> s(5).toDouble,
          "conns" -> conns.toIndexedSeq))
      }

      val placed = DisplayGeom.compute(segs.toIndexedSeq)
      for (k <- 0 until nSeg)
        out ++= s"OUT $candIdx $k ${placed.perp(k)} ${placed.alo(k)} ${placed.ahi(k)}\n"
      candIdx += 1
    }
    print(out.toString)
  }
}
