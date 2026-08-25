// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web

import buda.web.net.{ApiClient, WsClient}
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
  private var demoList: js.Array[js.Dynamic] = js.Array()
  private var active: js.Dynamic = null      // the selected demo {label, setup, stages}
  private var bundleFocus: Option[Int] = None   // None = show all bundles (nuts/detailed)
  // The generation view renders ONE bundle at a time (?bundle=<id>): `bundleSel`
  // is which one, `bundleIds` the ring to step through (from /state's bundle
  // digests).  Unlike `bundleFocus` — a per-view dimming reset on every refresh —
  // this is a SELECTION and PERSISTS, so pinning a candidate or running a stage
  // leaves the reader on the bundle they were reading.
  private var bundleSel: Int = -1               // -1 = not resolved yet
  private var bundleIds: Seq[Int] = Seq.empty

  private def svg = dom.document.getElementById("svg").asInstanceOf[dom.svg.SVG]
  private def byId(id: String) = dom.document.getElementById(id)
  private def defined(v: js.Dynamic): Boolean = !js.isUndefined(v) && v != null

  def main(args: Array[String]): Unit = {
    wire("run", () => runCmds())
    wire("reset", () => ApiClient.reset().foreach { st =>
      showStages(st); render = null; hideEditPanel(); draw() })
    wire("bundler", () => runDemoStage("bundler"))
    wire("topologies", () => runDemoStage("topologies"))
    wire("planner", () => runDemoStage("planner"))
    wire("nuts", () => runDemoStage("nuts"))
    wire("dnuts", () => runDemoStage("dnuts"))
    wire("ripup", () => runStage("ripup", ""))     // rip-up & re-route the residual (WS)
    wire("negotiate", () => runStage("negotiate", ""))   // measured-congestion negotiate (WS)
    wire("check", () => runCheck())                // design audit at the current stage
    wire("view-topo", () => setView("generation"))
    wire("view-nuts", () => setView("nuts"))
    wire("view-detailed", () => setView("detailed"))
    wire("bdb-open", () => bdbOpen())
    wire("bdb-save", () => bdbSave())
    wire("bdb-load", () => bdbLoad())
    wire("prev", () => stepCand(-1))
    wire("next", () => stepCand(1))
    wire("focus-prev", () => stepBundle(-1))
    wire("focus-next", () => stepBundle(1))
    wire("bundle-prev", () => stepBundleSel(-1))
    wire("bundle-next", () => stepBundleSel(1))
    wire("pin", () => pinCand())
    wire("edit-open", () => editOpen(cand))                 // int index
    wire("edit-open-new", () => editOpen("new"))
    wire("edit-apply", () => editApply())
    wire("edit-commit", () => editCommit(false))
    wire("edit-commit-pin", () => editCommit(true))
    wire("edit-abort", () => editAbort())
    Option(byId("demo")).foreach(
      _.addEventListener("change", (_: dom.Event) => loadDemo()))
    dom.window.addEventListener("keydown", (e: dom.KeyboardEvent) => onKey(e))
    WsClient.connect(setRunning, st => showStages(st), txt => log(txt), () => refresh())
    initDemos()
    refresh()
  }

  private def setRunning(txt: Option[String]): Unit =
    Option(byId("running")).foreach { el =>
      val e = el.asInstanceOf[dom.html.Element]
      txt match {
        case Some(t) => e.textContent = t; e.removeAttribute("hidden")
        case None    => e.setAttribute("hidden", "")
      }
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

  // ── demo picker ─────────────────────────────────────────────────────────────
  // The catalog (GET /api/demos) gives each demo its setup text + a per-stage
  // command map, so the same "setup + click stages" UX drives the flat AND the
  // hierarchy-aware flow (which needs run_hier_bundler / run_planner hier / …).
  private def initDemos(): Unit =
    ApiClient.demos().foreach { resp =>
      val ds = resp.selectDynamic("demos")
      demoList = if (defined(ds)) ds.asInstanceOf[js.Array[js.Dynamic]] else js.Array()
      Option(byId("demo")).foreach { sel =>
        val s = sel.asInstanceOf[dom.html.Select]
        s.innerHTML = ""
        demoList.zipWithIndex.foreach { case (dmo, i) =>
          val o = dom.document.createElement("option").asInstanceOf[dom.html.Option]
          o.value = i.toString; o.textContent = dmo.label.asInstanceOf[String]
          s.appendChild(o)
        }
      }
      if (demoList.nonEmpty) loadDemo()
    }

  private def loadDemo(): Unit = {
    val i = Option(byId("demo")).map(_.asInstanceOf[dom.html.Select].value)
      .flatMap(v => scala.util.Try(v.toInt).toOption).getOrElse(0)
    active = if (i >= 0 && i < demoList.length) demoList(i) else null
    val setup = if (active != null) active.setup.asInstanceOf[String] else ""
    Option(byId("cmds")).foreach(_.asInstanceOf[dom.html.TextArea].value = setup)
  }

  // The long stages go through the WS-progress endpoint POST /api/stage/{stage}
  // (WsStage maps a button id to the stage key; WsBase is the command prefix used
  // to peel the demo command's args, e.g. "run_planner hier 5" -> args "hier 5").
  // The instant stages (bundler/topologies) stay on /api/command.
  private val WsStage = Map("planner" -> "planner", "nuts" -> "nuts", "dnuts" -> "detailed_nuts")
  private val WsBase  = Map("planner" -> "run_planner", "nuts" -> "run_nuts", "dnuts" -> "run_detailed_nuts")
  // Fallback .buda command per stage key, if the /api/stage endpoint is unavailable.
  private val StageCmd = Map("planner" -> "run_planner", "nuts" -> "run_nuts",
    "detailed_nuts" -> "run_detailed_nuts", "ripup" -> "ripup_reroute",
    "negotiate" -> "negotiate_congestion")

  /** Run the ACTIVE demo's command for stage `key` (falls back to the key itself
    * if no demo is loaded).  Long stages (planner/nuts/dnuts) go through the WS
    * progress path; instant ones through /api/command. */
  private def runDemoStage(key: String): Unit = {
    val cmd =
      if (active != null && defined(active.selectDynamic("stages"))) {
        val v = active.stages.selectDynamic(key)
        if (defined(v)) v.asInstanceOf[String] else key
      } else key
    WsStage.get(key) match {
      case Some(wsName) =>
        val base = WsBase(key)
        val args = if (cmd.startsWith(base)) cmd.substring(base.length).trim else ""
        runStage(wsName, args)
      case None => stage(cmd)                    // bundler / topologies
    }
  }

  /** POST a long stage to /api/stage/{stage}; the WS drives the running indicator
    * while it runs, and the response carries the final result/state/notable so a
    * client without a live WS still updates.  Mirrors the reference `runStage`. */
  private def runStage(stageName: String, args: String): Unit = {
    setRunning(Some(s"running $stageName…"))
    ApiClient.stage(stageName, args).foreach { res =>
      if (defined(res.selectDynamic("error"))) {  // unknown stage -> command path
        val fb = StageCmd.getOrElse(stageName, stageName)
        stage(if (args.nonEmpty) s"$fb $args" else fb)
      } else {
        val notable = res.selectDynamic("notable")
        val nstr =
          if (defined(notable)) {
            val a = notable.asInstanceOf[js.Array[String]]
            if (a.nonEmpty) a.mkString("\n") + "\n" else ""
          } else ""
        val summary = res.result.selectDynamic("summary")
        log(nstr + (if (defined(summary) && summary.asInstanceOf[String].nonEmpty)
                    summary.asInstanceOf[String] else "(ok)"))
        showStages(res.state)
        refresh()
      }
      setRunning(None)                            // done frame may have cleared it
    }
  }

  /** Run the design audit at the current stage.  `check_design` auto-detects its
    * mode (topo / nuts / dnuts) from how far the pipeline has run — SERVER-side
    * (buda_cmds/verify_viz_cmds.py) — so the client just runs a bare command and
    * shows the full audit output.  Read-only: no render refresh (no view/focus
    * reset). */
  private def runCheck(): Unit =
    ApiClient.command(Seq("check_design")).foreach { res =>
      val r = res.results.asInstanceOf[js.Array[js.Dynamic]](0)
      val lines = r.selectDynamic("log_lines")
      val body =
        if (defined(lines) && lines.asInstanceOf[js.Array[String]].nonEmpty)
          lines.asInstanceOf[js.Array[String]].mkString("\n")
        else {
          val s = r.selectDynamic("summary")
          if (defined(s) && s.asInstanceOf[String].nonEmpty) s.asInstanceOf[String] else "(ok)"
        }
      log(body)
      showStages(res.state)
    }

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

  private def refresh(): Unit = {
    bundleFocus = None                 // a new stage / view starts showing all bundles
    ApiClient.state().foreach { st =>
      showStages(st)
      val sr = st.stages_run
      // Auto-advance the view to the deepest available stage on a fresh run.
      if (view == "detailed" && !sr.dnuts.asInstanceOf[Boolean])
        view = if (sr.nuts.asInstanceOf[Boolean]) "nuts" else "generation"
      if (view == "nuts" && !sr.nuts.asInstanceOf[Boolean]) view = "generation"
      if (!sr.topologies.asInstanceOf[Boolean]) { render = null; draw() }
      else {
        // Re-bundling can retire an id, so re-resolve the selection against the
        // live digest list — but keep it when it is still there, so a stage run
        // or a pin does not throw the reader back to the first bundle.
        bundleIds = st.bundles.asInstanceOf[js.Array[js.Dynamic]]
          .map(_.id.asInstanceOf[Double].toInt).toSeq
        if (!bundleIds.contains(bundleSel)) {
          bundleSel = bundleIds.headOption.getOrElse(1)
          cand = 0
        }
        val bundle = if (view == "generation") Some(bundleSel) else None
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
  }

  private def stepCand(dir: Int): Unit = {
    if (view != "generation" || render == null) return
    val bs = render.bundles.asInstanceOf[js.Array[js.Dynamic]]
    val n = if (bs.nonEmpty) bs(0).candidates.asInstanceOf[js.Array[js.Dynamic]].length else 0
    if (n > 0) { cand = ((cand + dir) % n + n) % n; draw() }
  }

  // ── bundle selection (generation view) ──────────────────────────────────────
  // Distinct from the focus ring below: that DIMS the other bundles of one placed
  // render, this changes WHICH bundle's candidate pool is fetched.  Nothing is
  // needed server-side — GET /api/render/generation has always taken ?bundle=<id>
  // (serialize_generation filters on original_bundle.id).
  private def stepBundleSel(dir: Int): Unit = {
    // An edit session is bound to its bundle server-side, so stepping away would
    // draw another bundle's frame under the working copy.
    if (view != "generation" || edit != null || bundleIds.isEmpty) return
    val i = math.max(0, bundleIds.indexOf(bundleSel))
    bundleSel = bundleIds(((i + dir) % bundleIds.length + bundleIds.length) % bundleIds.length)
    cand = 0                       // a different bundle has its own candidate pool
    ApiClient.render("generation", Some(bundleSel)).foreach { p => render = p; draw() }
  }

  private def updateBundleLabel(): Unit =
    Option(byId("bundlelbl")).foreach { l =>
      l.textContent =
        if (bundleIds.isEmpty) ""
        else s"bundle $bundleSel · ${bundleIds.indexOf(bundleSel) + 1}/${bundleIds.length} · [/]"
    }

  // ── bundle focus (NUTS / detailed views) ────────────────────────────────────
  /** The sorted distinct bundle ids present in the current view's segments. */
  private def currentBundleIds(): Seq[Int] = {
    if (render == null) return Seq.empty
    val segs: js.Array[js.Dynamic] = view match {
      case "nuts" =>
        val n = render.selectDynamic("nuts")
        if (defined(n)) n.segments.asInstanceOf[js.Array[js.Dynamic]] else js.Array()
      case "detailed" =>
        val de = render.selectDynamic("detailed")
        if (defined(de)) de.net_segments.asInstanceOf[js.Array[js.Dynamic]] else js.Array()
      case _ => js.Array()
    }
    segs.map(_.bundle_id.asInstanceOf[Double].toInt).distinct.sorted.toSeq
  }

  /** Cycle the focus ring `[all, id0, id1, …]`; `n`/`p` isolate one bundle. */
  private def stepBundle(dir: Int): Unit = {
    if (view == "generation" || render == null) return
    val ids = currentBundleIds()
    if (ids.isEmpty) return
    val ring: Seq[Option[Int]] = None +: ids.map(Some(_))   // None = "all bundles"
    val i = math.max(0, ring.indexOf(bundleFocus))
    bundleFocus = ring(((i + dir) % ring.length + ring.length) % ring.length)
    draw()
  }

  private def updateFocusLabel(): Unit = {
    val ids = currentBundleIds()
    Option(byId("focuslbl")).foreach { l =>
      l.textContent = bundleFocus match {
        case None      => s"all ${ids.length} bundles · n/p to isolate one"
        case Some(bid) => s"bundle $bid · ${ids.indexOf(bid) + 1}/${ids.length} · n/p to step"
      }
    }
  }

  // ── keyboard: step candidates (topo) / bundles (nuts, detailed) ─────────────
  private def onKey(e: dom.KeyboardEvent): Unit = {
    val tag = Option(e.target).map(_.asInstanceOf[dom.html.Element].tagName.toLowerCase).getOrElse("")
    if (tag == "textarea" || tag == "input" || tag == "select") return
    // n/p (and the arrows) were already fully consumed — candidates in the
    // generation view, bundle focus in nuts/detailed — so the bundle SELECTION
    // gets [ and ].
    if (e.key == "[" || e.key == "]") {
      e.preventDefault()
      stepBundleSel(if (e.key == "]") 1 else -1)
      return
    }
    val fwd = e.key == "n" || e.key == "ArrowRight"
    val back = e.key == "p" || e.key == "ArrowLeft"
    if (!fwd && !back) return
    e.preventDefault()
    if (view == "generation") stepCand(if (fwd) 1 else -1)
    else stepBundle(if (fwd) 1 else -1)
  }

  // ── select / pin ────────────────────────────────────────────────────────────
  // Pin the shown candidate, or unpin if it's already pinned (toggle).
  private def pinCand(): Unit = {
    if (view != "generation" || render == null || edit != null) return
    val call = if (pinnedHere) ApiClient.unpin(bundleSel) else ApiClient.select(bundleSel, cand)
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
    ApiClient.editOpen(bundleSel, candidate).foreach(applyEdit)
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
    val label = Renderer.draw(svg, render, view, cand, edit, bundleFocus)
    // Is the shown candidate the pinned one? (mirrors the reference client)
    pinnedHere = false
    if (render != null && view == "generation" && edit == null) {
      val st = render.selectDynamic("state")
      if (defined(st)) {
        // serialize_state() lists EVERY bundle, not just the rendered one, so the
        // shown bundle's digest must be looked up by id — bundles(0) was only ever
        // right while the view was stuck on the first bundle.
        val bs = st.bundles.asInstanceOf[js.Array[js.Dynamic]]
          .filter(_.id.asInstanceOf[Double].toInt == bundleSel)
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
      if (show) {
        Option(byId("candlbl")).foreach(_.textContent = label)
        updateBundleLabel()
      }
    }
    // The focus bar (bundle isolation) belongs to the NUTS/detailed views only.
    Option(byId("focusbar")).foreach { bar =>
      val bare = bar.asInstanceOf[dom.html.Element]
      val show = render != null && (view == "nuts" || view == "detailed")
      if (show) { bare.removeAttribute("hidden"); updateFocusLabel() }
      else bare.setAttribute("hidden", "")
    }
  }
}
