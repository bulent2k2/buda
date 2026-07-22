// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web

import buda.web.net.ApiClient
import buda.web.render.Renderer
import org.scalajs.dom
import scala.scalajs.js
import scala.concurrent.ExecutionContext.Implicits.global

/** Entry point: wire the command console, stage/view buttons, candidate stepper,
  * select/pin, the interactive edit panel, and the BDB row to the backend, and
  * render the current stage payload into the SVG.
  *
  * A faithful port of the vanilla reference client (`src/web/static/index.html`);
  * the two share the same `/api` + payload contract, so either can drive the demo.
  */
object Main {
  private var render: js.Dynamic = null
  private var cand: Int = 0
  private var view: String = "generation"
  private var edit: js.Dynamic = null   // null = no edit session open
  private var pinnedHere: Boolean = false   // is the shown candidate the pinned one?

  private def svg = dom.document.getElementById("svg").asInstanceOf[dom.svg.SVG]
  private def byId(id: String) = dom.document.getElementById(id)
  private def defined(v: js.Dynamic): Boolean = !js.isUndefined(v) && v != null

  def main(args: Array[String]): Unit = {
    wire("run", () => runCmds())
    wire("reset", () => ApiClient.reset().foreach { st =>
      showStages(st); render = null; hideEditPanel(); draw() })
    wire("bundler", () => stage("run_bundler"))
    wire("topologies", () => stage("generate_topologies"))
    wire("planner", () => stage("run_planner"))
    wire("nuts", () => stage("run_nuts"))
    wire("dnuts", () => stage("run_detailed_nuts"))
    wire("view-topo", () => setView("generation"))
    wire("view-nuts", () => setView("nuts"))
    wire("view-detailed", () => setView("detailed"))
    wire("bdb-open", () => bdbOpen())
    wire("bdb-save", () => bdbSave())
    wire("bdb-load", () => bdbLoad())
    wire("prev", () => stepCand(-1))
    wire("next", () => stepCand(1))
    wire("pin", () => pinCand())
    wire("edit-open", () => editOpen(cand))                 // int index
    wire("edit-open-new", () => editOpen("new"))
    wire("edit-apply", () => editApply())
    wire("edit-commit", () => editCommit(false))
    wire("edit-commit-pin", () => editCommit(true))
    wire("edit-abort", () => editAbort())
    refresh()
  }

  private def wire(id: String, fn: () => Unit): Unit =
    Option(byId(id)).foreach(_.addEventListener("click", (_: dom.Event) => fn()))

  private def inputVal(id: String): String =
    Option(byId(id)).map(_.asInstanceOf[dom.html.Input].value).getOrElse("")

  private def cmdsText: Seq[String] =
    byId("cmds").asInstanceOf[dom.html.TextArea].value
      .split("\n").map(_.trim).filter(_.nonEmpty).toSeq

  private def log(txt: String): Unit =
    Option(byId("log")).foreach(_.textContent = txt)

  // ── command console ─────────────────────────────────────────────────────────
  private def runCmds(): Unit =
    ApiClient.command(cmdsText).foreach { res => showResults(res); refresh() }

  private def stage(cmd: String): Unit =
    ApiClient.command(Seq(cmd)).foreach { res => showResults(res); refresh() }

  private def showResults(res: js.Dynamic): Unit = {
    val rs = res.results.asInstanceOf[js.Array[js.Dynamic]]
    val txt = rs.map { r =>
      val ok = r.ok.asInstanceOf[Boolean]
      (if (ok) "  " else "x ") +
        Option(r.summary.asInstanceOf[String]).filter(_.nonEmpty).getOrElse("(ok)")
    }.mkString("\n")
    log(txt)
    showStages(res.state)
  }

  private def showStages(state: js.Dynamic): Unit = {
    val sr = state.stages_run
    dom.document.querySelectorAll("#stages span").foreach { n =>
      val e = n.asInstanceOf[dom.html.Element]
      val k = e.getAttribute("data-k")
      val v = sr.selectDynamic(k)
      val on = defined(v) && v.asInstanceOf[Boolean]
      e.classList.toggle("on", on)
    }
  }

  // ── stage / view refresh ────────────────────────────────────────────────────
  private def setView(v: String): Unit = { view = v; refresh() }

  private def refresh(): Unit =
    ApiClient.state().foreach { st =>
      showStages(st)
      val sr = st.stages_run
      // Auto-advance the view to the deepest available stage on a fresh run.
      if (view == "detailed" && !sr.dnuts.asInstanceOf[Boolean])
        view = if (sr.nuts.asInstanceOf[Boolean]) "nuts" else "generation"
      if (view == "nuts" && !sr.nuts.asInstanceOf[Boolean]) view = "generation"
      if (!sr.topologies.asInstanceOf[Boolean]) { render = null; draw() }
      else {
        val bundle = if (view == "generation") Some(1) else None
        ApiClient.render(view, bundle).foreach { p =>
          render = p
          if (view == "generation") {
            val bs = render.bundles.asInstanceOf[js.Array[js.Dynamic]]
            val n = if (bs.nonEmpty) bs(0).candidates.asInstanceOf[js.Array[js.Dynamic]].length else 1
            cand = math.min(cand, math.max(0, n - 1))
          }
          draw()
        }
      }
    }

  private def stepCand(dir: Int): Unit = {
    if (view != "generation" || render == null) return
    val bs = render.bundles.asInstanceOf[js.Array[js.Dynamic]]
    val n = if (bs.nonEmpty) bs(0).candidates.asInstanceOf[js.Array[js.Dynamic]].length else 0
    if (n > 0) { cand = ((cand + dir) % n + n) % n; draw() }
  }

  // ── select / pin ────────────────────────────────────────────────────────────
  // Pin the shown candidate, or unpin if it's already pinned (toggle).
  private def pinCand(): Unit = {
    if (view != "generation" || render == null || edit != null) return
    val call = if (pinnedHere) ApiClient.unpin(1) else ApiClient.select(1, cand)
    call.foreach { res =>
      val s = res.result.summary
      log(if (defined(s)) s.asInstanceOf[String]
          else if (pinnedHere) "unpinned" else "pinned")
      showStages(res.state)
      refresh()
    }
  }

  // ── BDB checkpoint ──────────────────────────────────────────────────────────
  private def bdbPath: String = inputVal("bdbpath").trim

  private def bdbOpen(): Unit =
    ApiClient.bdbOpen(bdbPath).foreach { r => logResult(r.result, "bdb opened"); showStages(r.state) }

  private def bdbSave(): Unit =
    ApiClient.bdbSave(bdbPath + ".snap").foreach { r => logResult(r.result, "snapshot saved") }

  private def bdbLoad(): Unit =
    ApiClient.bdbLoad().foreach { r => logResult(r.result, "pipeline loaded"); showStages(r.state); refresh() }

  private def logResult(result: js.Dynamic, fallback: String): Unit = {
    val s = result.selectDynamic("summary")
    log(if (defined(s) && s.asInstanceOf[String].nonEmpty) s.asInstanceOf[String] else fallback)
  }

  // ── interactive edit ────────────────────────────────────────────────────────
  private def applyEdit(resp: js.Dynamic): Unit = {
    val e = resp.selectDynamic("edit")
    edit = if (defined(e) && e.open.asInstanceOf[Boolean]) e else null
    Option(byId("editpanel")).foreach { p =>
      val pe = p.asInstanceOf[dom.html.Element]
      if (edit != null) pe.removeAttribute("hidden") else pe.setAttribute("hidden", "")
    }
    if (edit != null) {
      val src = edit.selectDynamic("src")
      Option(byId("editsrc")).foreach(_.textContent =
        s"bundle ${edit.bundle_id} — ${if (defined(src)) src.asInstanceOf[String] else ""}")
      val v = edit.selectDynamic("verdict")
      val ok = defined(v) && v.ok.asInstanceOf[Boolean]
      Option(byId("editverdict")).foreach { vd =>
        val vde = vd.asInstanceOf[dom.html.Element]
        vde.className = if (ok) "ok" else "bad"
        val nSegs = edit.topology.segments.asInstanceOf[js.Array[js.Dynamic]].length
        val comps = if (defined(v)) v.components else 0
        vde.textContent =
          if (ok) s"ok · $nSegs segs · $comps component(s)"
          else {
            val viols = if (defined(v) && defined(v.selectDynamic("violations")))
              v.violations.asInstanceOf[js.Array[js.Dynamic]].map(_.kind.asInstanceOf[String]).mkString(", ")
            else ""
            s"NOT ok · $viols · $comps component(s)"
          }
      }
    }
    val res = resp.selectDynamic("result")
    if (defined(res)) logResult(res, if (res.ok.asInstanceOf[Boolean]) "(ok)" else "(failed)")
    draw()
  }

  private def editOpen(candidate: js.Any): Unit = {
    view = "generation"                       // an uncommitted edit is topo-only
    ApiClient.editOpen(1, candidate).foreach(applyEdit)
  }

  private def editApply(): Unit = {
    val cmd = inputVal("editop").trim
    if (cmd.nonEmpty) ApiClient.editOp(cmd).foreach(applyEdit)
  }

  private def editCommit(pin: Boolean): Unit =
    ApiClient.editCommit(pin).foreach { r => applyEdit(r); refresh() }

  private def editAbort(): Unit =
    ApiClient.editAbort().foreach(applyEdit)

  /** Drop any open edit session and hide its panel.  Reset clears `render` but
    * must also clear `edit`, else `draw()` keeps passing the stale working-copy
    * topology to the renderer and it draws over the freshly reset session until
    * the next edit API call happens to clear it. */
  private def hideEditPanel(): Unit = {
    edit = null
    Option(byId("editpanel")).foreach(
      _.asInstanceOf[dom.html.Element].setAttribute("hidden", ""))
  }

  // ── render ──────────────────────────────────────────────────────────────────
  private def draw(): Unit = {
    val label = Renderer.draw(svg, render, view, cand, edit)
    // Is the shown candidate the pinned one? (mirrors the reference client)
    pinnedHere = false
    if (render != null && view == "generation" && edit == null) {
      val st = render.selectDynamic("state")
      if (defined(st)) {
        val bs = st.bundles.asInstanceOf[js.Array[js.Dynamic]]
        if (bs.length > 0) {
          val sb = bs(0)
          pinnedHere = defined(sb.selectDynamic("pinned")) &&
            sb.pinned.asInstanceOf[Boolean] &&
            sb.selected_index.asInstanceOf[Int] == cand
        }
      }
    }
    Option(byId("pin")).foreach(_.textContent = if (pinnedHere) "Unpin" else "Pin")
    Option(byId("candbar")).foreach { bar =>
      val bare = bar.asInstanceOf[dom.html.Element]
      val show = render != null && view == "generation"
      if (show) bare.removeAttribute("hidden") else bare.setAttribute("hidden", "")
      if (show) Option(byId("candlbl")).foreach(_.textContent = label)
    }
  }
}
