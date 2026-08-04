// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
//
// A JVM stand-in for the sliver of scala.scalajs that DisplayGeom.scala uses,
// so the REAL port compiles and runs unmodified off Scala.js.
//
// Why a shim rather than a Scala.js test: linking Scala.js needs sbt + the
// scalajs plugin + a JS runtime, i.e. a whole toolchain in the gate for one
// 72-line file.  DisplayGeom.compute is pure arithmetic over the serialized
// analysis, so a JVM run exercises the logic that has actually drifted
// (issue #554) at a fraction of the cost.
//
// WHAT THIS DOES NOT COVER, stated plainly: the linked Scala.js artifact.  Any
// divergence that comes from Scala.js semantics rather than from the source --
// notably JS numeric behaviour, where every number is a Double -- is invisible
// here.  The shim therefore models that deliberately: numeric fields are stored
// as Double, and `seg_idx` as Int only because DisplayGeom casts it that way.
package scala.scalajs

import scala.language.dynamics

object js {

  /** An `undefined` field.  DisplayGeom distinguishes undefined/null from a
    * real number via isUndefined/`== null`, so both must be representable. */
  object Undefined

  /** The subset of js.Dynamic DisplayGeom touches: named field access, which
    * Scala desugars to selectDynamic through scala.Dynamic. */
  final class Dynamic(fields: Map[String, Any]) extends scala.Dynamic {
    def selectDynamic(name: String): Any = fields.getOrElse(name, Undefined)
  }

  /** js.Array is only indexed, sized and iterated here. */
  type Array[T] = IndexedSeq[T]

  def isUndefined(v: Any): Boolean = v.asInstanceOf[AnyRef] eq Undefined
}
