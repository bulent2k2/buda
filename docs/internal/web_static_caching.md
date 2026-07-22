# Web static caching — `Cache-Control: no-cache`

The web backend serves its demo clients from one `StaticFiles` mount at `/`
(`src/web/server.py`): the reference client (`static/index.html`), the Scala.js
client (`static/scala/index.html`), and the Scala bundle
(`static/scala/main.js`). That mount is a `_NoCacheStatic` subclass that stamps
`Cache-Control: no-cache` on every response.

## Why

`main.js` is a **build product** rebuilt by `bb web` (`sbt fullLinkJS`). With no
explicit `Cache-Control`, browsers apply *heuristic freshness* — they guess how
long a static asset stays fresh from its `Last-Modified` age — so a plain reload
after a rebuild can keep running the **stale** bundle until the guessed window
expires or you hard-refresh. The symptom is "the `/scala/` client is missing a
feature I just built"; the reference client at `/` doesn't hit this because its
logic is inline in `index.html` rather than a separately-cached module.

`no-cache` does **not** mean "don't cache". It means "cache, but **revalidate**
before reuse": the browser keeps the file but sends a conditional request
(`If-None-Match` / `If-Modified-Since`) on every load. `StaticFiles` still emits
`ETag` + `Last-Modified`, so:

- **unchanged file** → `304 Not Modified`, the browser reuses its copy (a few
  bytes over the wire, no re-download);
- **rebuilt file** → `200` with the new bytes, picked up on the **next normal
  reload** — no hard refresh needed.

`StaticFiles` **raises** `HTTPException(404)` for a missing file rather than
returning a response, so `_NoCacheStatic` catches the 404 and stamps the header
on it too. That matters for the not-built → built transition: `/scala/` before
`bb web` 404s on the git-ignored `main.js`, and 404 is a heuristically-cacheable
status — without `no-cache` a browser could keep reusing the cached failure (and
the "not built" banner) after the bundle exists. Other statuses (405/401) are
re-raised unchanged.

## Downsides (why this is a deliberate, scoped choice)

1. **A revalidation round-trip on every load.** Each page load makes a
   conditional GET for `index.html` + `main.js`, normally answered `304`. You
   lose the "serve from cache with zero network" fast path. Negligible on a
   localhost demo (one tiny request); it is *not* free, and would matter if this
   server were ever fronting many clients or large assets over a real network.
2. **Custom code.** `StaticFiles` has no header knob, so this is a subclass
   overriding `get_response` — a few lines of maintenance surface that did not
   exist before.
3. **Whole-mount scope.** It applies to the single `/` mount, i.e. **both**
   clients and every asset under `static/`, not surgically just `main.js`. Fine
   here (everything under it is demo content), but it is not targeted.
4. **Fixes only the *cache* class of staleness.** If a `bb web` didn't actually
   rebuild (stale checkout, sbt failure), the file on disk is old and `no-cache`
   changes nothing — you would still see old behavior. It removes one source of
   "why is it stale?" confusion, not all of them.

## Alternative considered

Content-hashed filenames (`main.<hash>.js`) with an immutable, far-future
`Cache-Control` avoid even the revalidation round-trip and are the standard
production approach. Rejected here as overkill: the HTML would need to learn the
hash at build time (extra `bb web` machinery), for a single-user localhost demo
where a per-load `304` costs nothing. If the web frontend ever grows into a
hosted, multi-user deployment, revisit this in favor of hashed assets.

## Test

`test/tests/test_web_server.py::test_static_assets_send_no_cache_but_revalidate`
asserts the header is present and that an `If-None-Match` with the returned ETag
still short-circuits to `304`.
