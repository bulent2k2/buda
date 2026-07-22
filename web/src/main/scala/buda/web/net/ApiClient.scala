// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.net

import org.scalajs.dom
import scala.concurrent.Future
import scala.scalajs.js
import scala.scalajs.js.Thenable.Implicits._
import scala.concurrent.ExecutionContext.Implicits.global

/** Thin fetch wrappers over the BUDA backend `/api` routes.
  *
  * The payloads are dynamically typed (`js.Dynamic` via `JSON.parse`) to keep
  * the scaffold dependency-light; a production build may swap in circe/upickle
  * decoders against `Protocol` case classes mirroring `src/web/serialize.py`.
  */
object ApiClient {
  private val base = "/api"

  private def getJson(path: String): Future[js.Dynamic] =
    dom.fetch(base + path).flatMap(_.text()).map(s => js.JSON.parse(s))

  private def postJson(path: String, body: js.Any): Future[js.Dynamic] = {
    val init = new dom.RequestInit {}
    init.method = dom.HttpMethod.POST
    init.headers = js.Dictionary("content-type" -> "application/json")
    init.body = js.JSON.stringify(body)
    dom.fetch(base + path, init).flatMap(_.text()).map(s => js.JSON.parse(s))
  }

  /** Run one or more `.buda` commands verbatim. */
  def command(cmds: Seq[String]): Future[js.Dynamic] =
    postJson("/command", js.Dynamic.literal(cmds = js.Array(cmds*)))

  def state(): Future[js.Dynamic] = getJson("/state")

  /** The built-in demo catalog (flat + hierarchical): each entry carries its
    * setup script and a per-stage command map, so the same stage buttons drive
    * both the flat and the hierarchy-aware flows. */
  def demos(): Future[js.Dynamic] = getJson("/demos")

  /** Run a long stage through the WS-progress endpoint `POST /api/stage/{stage}`.
    * `stage` ∈ {planner, nuts, detailed_nuts, ripup}; `args` is appended verbatim
    * to the mapped `.buda` command server-side (e.g. "hier 5" → `run_planner
    * hier 5`). The socket (WsClient) streams progress; this returns the final
    * `{result, state, notable}` so a client with no live WS still gets the
    * outcome. */
  def stage(stage: String, args: String): Future[js.Dynamic] =
    postJson("/stage/" + stage, js.Dynamic.literal(args = args))

  def reset(): Future[js.Dynamic] = postJson("/reset", js.Dynamic.literal())

  /** Stage render payload — `stage` is "generation", "nuts", or "detailed".
    * The generation view narrows to one bundle. */
  def render(stage: String, bundle: Option[Int] = None): Future[js.Dynamic] = {
    val q = if (stage == "generation") bundle.map(b => s"?bundle=$b").getOrElse("") else ""
    getJson("/render/" + stage + q)
  }

  /** Generation-stage render payload (floorplan + hanan + bundles). */
  def renderGeneration(bundle: Option[Int] = None): Future[js.Dynamic] =
    render("generation", bundle)

  /** Pin a bundle to a candidate topology (0-based index; backend adds +1). */
  def select(bundle: Int, candidate: Int): Future[js.Dynamic] =
    postJson("/select", js.Dynamic.literal(bundle = bundle, candidate = candidate))

  def unpin(bundle: Int): Future[js.Dynamic] =
    postJson("/unpin", js.Dynamic.literal(bundle = bundle))

  // ── interactive edit ───────────────────────────────────────────────────────
  /** Open an edit session on a candidate index or the literal string "new". */
  def editOpen(bundle: Int, candidate: js.Any): Future[js.Dynamic] =
    postJson("/edit/open", js.Dynamic.literal(bundle = bundle, candidate = candidate))

  def editOp(command: String): Future[js.Dynamic] =
    postJson("/edit/op", js.Dynamic.literal(command = command))

  def editCommit(pin: Boolean): Future[js.Dynamic] =
    postJson("/edit/commit", js.Dynamic.literal(pin = pin))

  def editAbort(): Future[js.Dynamic] =
    postJson("/edit/abort", js.Dynamic.literal())

  // ── BDB checkpoint ─────────────────────────────────────────────────────────
  def bdbOpen(path: String): Future[js.Dynamic] =
    postJson("/bdb/open", js.Dynamic.literal(path = path))

  def bdbSave(path: String): Future[js.Dynamic] =
    postJson("/bdb/save", js.Dynamic.literal(path = path))

  def bdbLoad(): Future[js.Dynamic] =
    postJson("/bdb/load_pipeline", js.Dynamic.literal())
}
