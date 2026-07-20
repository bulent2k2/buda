// BUDA web frontend — Scala.js.
//
// Build the client bundle:
//   sbt fastLinkJS      # dev  -> target/scala-3.3.4/buda-web-fastopt/main.js
//   sbt fullLinkJS      # prod -> target/scala-3.3.4/buda-web-opt/main.js
//
// Copy the emitted main.js next to web/index.html (or serve via Vite), then open
// index.html against a running backend (PYTHONPATH=build:src uvicorn web.server:app).
// See web/README.md.

enablePlugins(ScalaJSPlugin)

name := "buda-web"
scalaVersion := "3.3.4"

// Emit a single ES module the index.html loads as <script type="module">.
scalaJSLinkerConfig ~= { _.withModuleKind(ModuleKind.ESModule) }

// Call Main.main() on load.
scalaJSUseMainModuleInitializer := true

libraryDependencies += "org.scala-js" %%% "scalajs-dom" % "2.8.0"
