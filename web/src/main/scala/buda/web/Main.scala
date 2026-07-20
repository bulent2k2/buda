// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web

import buda.web.net.ApiClient
import buda.web.render.Renderer
import org.scalajs.dom
import scala.scalajs.js
import scala.concurrent.ExecutionContext.Implicits.global

/** Entry point: wire the command console + stage buttons to the backend and
  * render the generation payload into the SVG.  Mirrors the vanilla reference
  * client (`src/web/static/index.html`); the two share the same API + payload
  * contract, so either can drive the demo.
  *
  * The full module split from the plan (console/, stepper/, panels/, edit/) is
  * the target as the frontend grows; this Main keeps Phase 1 compact.
  */
object Main {
  private var render: js.Dynamic = null
  private var cand: Int = 0

  private def svg = dom.document.getElementById("svg").asInstanceOf[dom.svg.SVG]
  private def byId(id: String) = dom.document.getElementById(id)

  def main(args: Array[String]): Unit = {
    wire("run", () => runCmds())
    wire("reset", () => ApiClient.reset().foreach(_ => { render = null; draw() }))
    wire("bundler", () => stage("run_bundler"))
    wire("topologies", () => stage("generate_topologies"))
    wire("planner", () => stage("run_planner"))
    wire("prev", () => step(-1))
    wire("next", () => step(1))
    refresh()
  }

  private def wire(id: String, fn: () => Unit): Unit =
    Option(byId(id)).foreach(_.addEventListener("click", (_: dom.Event) => fn()))

  private def cmdsText: Seq[String] =
    byId("cmds").asInstanceOf[dom.html.TextArea].value
      .split("\n").map(_.trim).filter(_.nonEmpty).toSeq

  private def runCmds(): Unit =
    ApiClient.command(cmdsText).foreach { res => showResults(res); refresh() }

  private def stage(cmd: String): Unit =
    ApiClient.command(Seq(cmd)).foreach { res => showResults(res); refresh() }

  private def showResults(res: js.Dynamic): Unit = {
    val rs = res.results.asInstanceOf[js.Array[js.Dynamic]]
    val txt = rs.map { r =>
      val ok = r.ok.asInstanceOf[Boolean]
      (if (ok) "  " else "x ") + Option(r.summary.asInstanceOf[String]).filter(_.nonEmpty).getOrElse("(ok)")
    }.mkString("\n")
    Option(byId("log")).foreach(_.textContent = txt)
    showStages(res.state)
  }

  private def showStages(state: js.Dynamic): Unit = {
    val sr = state.stages_run
    dom.document.querySelectorAll("#stages span").foreach { n =>
      val e = n.asInstanceOf[dom.html.Element]
      val k = e.getAttribute("data-k")
      val on = sr.selectDynamic(k).asInstanceOf[Boolean]
      e.classList.toggle("on", on)
    }
  }

  private def refresh(): Unit =
    ApiClient.state().foreach { st =>
      showStages(st)
      if (!st.stages_run.topologies.asInstanceOf[Boolean]) { render = null; draw() }
      else ApiClient.renderGeneration(Some(1)).foreach { p => render = p; draw() }
    }

  private def step(dir: Int): Unit = {
    if (render == null) return
    val n = render.bundles.asInstanceOf[js.Array[js.Dynamic]](0)
      .candidates.asInstanceOf[js.Array[js.Dynamic]].length
    if (n > 0) { cand = ((cand + dir) % n + n) % n; draw() }
  }

  private def draw(): Unit = {
    Renderer.draw(svg, render, cand)
    Option(byId("candbar")).foreach { bar =>
      if (render == null) bar.asInstanceOf[dom.html.Element].setAttribute("hidden", "")
      else {
        bar.asInstanceOf[dom.html.Element].removeAttribute("hidden")
        val b = render.bundles.asInstanceOf[js.Array[js.Dynamic]](0)
        val cs = b.candidates.asInstanceOf[js.Array[js.Dynamic]]
        val c = cs(math.max(0, math.min(cand, cs.length - 1)))
        Option(byId("candlbl")).foreach(_.textContent =
          s"bundle ${b.id} · cand ${cand + 1}/${cs.length} · ${c.`type`} · WL ${c.estimated_wirelength}")
      }
    }
  }
}
