// Copyright 2026 Ben Bulent Basaran — Apache-2.0.
package buda.web.net

import org.scalajs.dom
import scala.scalajs.js

/** WebSocket progress client for the long stages (planner / nuts / detailed_nuts
  * / ripup). Connects to `/api/ws`, parses the server's JSON progress frames, and
  * invokes the handlers; auto-reconnects on close (1.5 s). A faithful port of the
  * reference client's `connectWS` (`src/web/static/index.html`), so both clients
  * consume the same frame schema:
  *   - `stage` `status:"started"`  → running indicator on
  *   - `heartbeat`                 → running indicator with elapsed seconds
  *   - `stage` `status:"done"`     → indicator off, refresh stages/log/render
  *
  * The socket carries no commands (stages are driven by `POST /api/stage/{stage}`);
  * it exists only to stream progress, so a failed connect (e.g. a server built
  * without the uvicorn websocket extra) degrades gracefully — the POST path still
  * returns the final result and the caller still toggles the indicator itself. */
object WsClient {
  def connect(
      setRunning: Option[String] => Unit,
      showStages: js.Dynamic => Unit,
      log: String => Unit,
      refresh: () => Unit): Unit = {
    val proto = if (dom.window.location.protocol == "https:") "wss" else "ws"
    val ws = new dom.WebSocket(s"$proto://${dom.window.location.host}/api/ws")

    ws.onmessage = (ev: dom.MessageEvent) => {
      val f = js.JSON.parse(ev.data.asInstanceOf[String])
      val kind = str(f, "kind")
      val status = str(f, "status")
      if (kind == "stage" && status == "started")
        setRunning(Some(s"running ${str(f, "name")}…"))
      else if (kind == "heartbeat")
        setRunning(Some(s"running ${str(f, "name")}… ${str(f, "elapsed")}s"))
      else if (kind == "stage" && status == "done") {
        setRunning(None)
        val st = f.selectDynamic("state")
        if (!js.isUndefined(st) && st != null) showStages(st)
        val notable = f.selectDynamic("notable")
        if (!js.isUndefined(notable) && notable != null) {
          val a = notable.asInstanceOf[js.Array[String]]
          if (a.nonEmpty) log(a.mkString("\n"))
        }
        refresh()
      }
    }
    ws.onclose = (_: dom.CloseEvent) => {
      setRunning(None)
      dom.window.setTimeout(() => connect(setRunning, showStages, log, refresh), 1500)
    }
    ws.onerror = (_: dom.Event) => ws.close()
  }

  private def str(o: js.Dynamic, k: String): String = {
    val v = o.selectDynamic(k)
    if (js.isUndefined(v) || v == null) "" else v.toString
  }
}
