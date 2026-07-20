// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.net

import org.scalajs.dom
import scala.concurrent.Future
import scala.scalajs.js
import scala.scalajs.js.Thenable.Implicits._
import scala.concurrent.ExecutionContext.Implicits.global

/** Thin fetch wrappers over the BUDA backend `/api/*` routes.
  *
  * The payloads are dynamically typed (`js.Dynamic` via `JSON.parse`) to keep
  * the scaffold dependency-light; a production build may swap in circe/upickle
  * decoders against `Protocol` case classes mirroring `src/web/serialize.py`.
  */
object ApiClient {
  private val base = "/api"

  private def getJson(path: String): Future[js.Dynamic] =
    dom.fetch(base + path).flatMap(_.text()).map(js.JSON.parse)

  private def postJson(path: String, body: js.Any): Future[js.Dynamic] = {
    val init = new dom.RequestInit {}
    init.method = dom.HttpMethod.POST
    init.headers = js.Dictionary("content-type" -> "application/json")
    init.body = js.JSON.stringify(body)
    dom.fetch(base + path, init).flatMap(_.text()).map(js.JSON.parse)
  }

  /** Run one or more `.buda` commands verbatim. */
  def command(cmds: Seq[String]): Future[js.Dynamic] =
    postJson("/command", js.Dynamic.literal(cmds = js.Array(cmds*)))

  def state(): Future[js.Dynamic] = getJson("/state")

  def reset(): Future[js.Dynamic] = postJson("/reset", js.Dynamic.literal())

  /** Generation-stage render payload (floorplan + hanan + bundles). */
  def renderGeneration(bundle: Option[Int] = None): Future[js.Dynamic] = {
    val q = bundle.map(b => s"?bundle=$b").getOrElse("")
    getJson("/render/generation" + q)
  }
}
