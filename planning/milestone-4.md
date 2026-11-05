# Milestone 4: Remote-Ready Server (HTTP, Docker, Bearer Auth)

> **Status: proposed.** This spec is reviewed and merged before any implementation.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Where this spec changes a decision recorded there (the roadmap, D9; Dual Input Mode, D3; Resource Limits, D4), **this spec wins** until the implementation PR updates the overview. The previous milestone is [milestone-3.5.md](milestone-3.5.md).

**Goal:** Let the server run as a network service, not only as a local stdio process:
- a Streamable HTTP transport, next to stdio
- a bearer token that every HTTP request must carry
- no access to the server's own files over HTTP: callers send their data inline
- a Docker image, built and smoke-tested in CI

A live deployment and OAuth (which a claude.ai custom connector needs) are **not** in this milestone. They need a hosting account and an identity provider, so they move to a later spec (D9).

**Layer boundary:** nothing in `profiler_core/` or `cleaner_core/` learns about transports.
- The loader and `run_cleaning` gain one keyword parameter, `allow_file_path` (D3). They don't know why it is off.
- `config.py` gains a few defaults and reads optional environment overrides for limits (D4). It stays plain Python: `os` only, no `mcp`, no web framework.
- **Transport code** sits outside the core:
  - the HTTP wiring and the command line in `server.py`
  - the bearer-token check in a new `http_auth.py`, a small ASGI middleware that uses Starlette and never imports `mcp`
  - the per-transport switch read in `tools/` (D3)
- `tests/test_architecture.py` keeps passing, and is extended (see **Invariant changes**):
  - the core can't import the SDK's own packages (`mcp`, `mcp_types`) or a web stack (`starlette`, `uvicorn`, `httpx2`, `sse_starlette`)
  - `http_auth.py` can't import `mcp` or `mcp_types`
- **No new packages,** but two new direct dependencies: `server.py` and `http_auth.py` import `starlette` and `uvicorn` themselves, so `pyproject.toml` declares them, with the lower bounds `mcp` already requires (`starlette>=0.48.0`, `uvicorn>=0.31.1`). Today they only arrive through `mcp`, and a later `mcp` release could drop or rename one, as it did when it moved from `httpx` to `httpx2`. The lock keeps the same versions (Starlette 1.7.0, uvicorn 0.54.0).

---

## Guiding principle: fail closed, stay simple

The server is single-tenant: one token, one operator, no per-user state.
- **Fail closed:** every rule below refuses rather than guesses. The server won't start on a public interface without a token, and it rejects a `file_path` over HTTP. A limit it can't parse stops the server at startup, with a clear message (D4).
- **Stdio doesn't change:** `data-quality-mcp` with no arguments behaves exactly as in 0.4.3, so Claude Desktop and every existing config keep working. The one accepted exception: a malformed `DATA_QUALITY_MCP_*` limit variable (D4) now stops startup on stdio too, where 0.4.3 ignored it. Unset variables change nothing.
- **Analyzer parity:** nothing to port. Transport, auth and Docker are MCP-only, so this milestone has no **Deviations** table.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Transport and command line | **Streamable HTTP at `/mcp`, stateless, with JSON responses.** `data-quality-mcp --transport http [--host 127.0.0.1] [--port 8000]`, which binds to loopback by default. |
| D2 | Authentication | **One static bearer token**, from the `DATA_QUALITY_MCP_TOKEN` environment variable. A small middleware compares it in constant time. It is required except on a loopback host. OAuth comes later (D9). |
| D3 | `file_path` over HTTP | **Off.** Over HTTP the server rejects `file_path` and `output_path` with an error that says to send `inline_content`. Stdio is unchanged. There is no opt-in in M4. |
| D4 | Limits | **A request-body cap for HTTP (16 MB), plus environment overrides for 5 limits,** validated at startup. The loader's limits keep their defaults. |
| D5 | Host and Origin checks | **The SDK's DNS-rebinding protection,** automatic on loopback. Elsewhere it is enabled with an allowed-hosts list from `DATA_QUALITY_MCP_ALLOWED_HOSTS`, and off by default (the token covers that case). |
| D6 | Docker image | **A slim, non-root image** installed from `uv.lock`, serving HTTP on port 8000, with a health check. The token is supplied at run time and never stored in the image. |
| D7 | CI | **A `Docker image` job in `build.yml`** builds the image and smoke-tests it over HTTP. In-process HTTP tests run in the normal test matrix. |
| D8 | Clients and docs | **Documented HTTP setup for Claude Code, VS Code and the MCP Inspector.** Claude Desktop stays on stdio. Plus a README section, `.env.example` and Makefile targets. |
| D9 | Roadmap | **The live deployment and OAuth move to a later spec,** written once a host and an identity provider are chosen. The M4 row in the overview changes accordingly. |

### D1. Transport and command line

- **Command line** (`argparse` in `server.py`):
  - `data-quality-mcp` and `data-quality-mcp --transport stdio`: stdio, as now.
  - `data-quality-mcp --transport http [--host HOST] [--port PORT]`: Streamable HTTP. The defaults are `127.0.0.1` and `8000`.
  - `--help` prints to stdout, which is fine: it is run by a person, never by an MCP client over stdio.
- **The endpoint** is `/mcp` (the SDK's default `streamable_http_path`). `GET /health` returns `200 {"status": "ok"}` without authentication, for Docker's health check. It doesn't report the version or any other detail.
- **Stateless, with JSON responses** (`stateless_http=True`, `json_response=True`):
  - Every tool, resource and prompt is already stateless. The server never asks the client anything (no sampling, no elicitation), and sends no progress notifications.
  - So nothing needs an `Mcp-Session-Id` or an SSE stream. A restart or a second instance loses nothing.
  - It also avoids the session manager's idle sessions (30 minutes, up to 10,000 by default), which a stateful server would hold in memory.
- **How it is served:** `server.py` builds the Starlette app with `app.streamable_http_app(...)` and wraps it with the auth middleware (D2). `uvicorn.run` then serves it. `app.run(transport="streamable-http")` isn't used, because it gives no place to add the middleware.
- **Concurrency:** the SDK runs synchronous tools in worker threads (`anyio.to_thread`, at most 40 at a time by default), so a long profile doesn't block `/health` or other requests. Concurrent calls on large inputs each need their own memory, and M4 has no cap on that: the server is token-gated and single-tenant. The README's sizing note says so (D6).
- **Logging:** stderr, as for stdio.
  - **Uvicorn's defaults don't do that:** its default `log_config` sends the access log to **stdout**, and its request line includes the query string. A client that put `?token=...` in the URL would get its token logged, even though the server never reads it.
  - So `server.py` passes its own `log_config`, with every handler on stderr, and turns uvicorn's access log off (`access_log=False`). The middleware (D2) logs one line per request instead: method, path **without the query string**, and status. `GET /health` is logged at DEBUG only, so Docker's health check doesn't fill the log.
  - Request headers and bodies are never logged. The startup log names the transport, the address and whether auth is on, never the token.
  - A test sends a token in both the header and the query string and checks that neither appears in any log record.

### D2. Authentication

- **The token:**
  - It comes from `DATA_QUALITY_MCP_TOKEN` in the environment.
  - A token is never accepted as a command-line argument, because process lists show arguments to other users.
  - **Empty means unset:** an empty or whitespace-only value counts as no token. `.env.example` ships `DATA_QUALITY_MCP_TOKEN=`, which `docker --env-file` passes as an empty string, so loopback without a token keeps working (with the warning below).
  - **Format:** at least 32 characters, from the RFC 6750 token set (letters, digits and `-._~+/`, optionally ending in `=`). So no spaces or other whitespace, and no non-ASCII characters. Anything else is a startup error. The README shows how to generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"` (43 characters, all in that set).
  - **`.env` values are unquoted:** `docker --env-file` keeps quotes as part of the value, while `uv run --env-file` strips them, so a quoted token would differ between the two. `.env.example` says so, and a quote character fails the format check anyway.
- **When it's required:**
  - The host isn't one of exactly `127.0.0.1`, `localhost` or `::1`, the same three strings for which the SDK turns on its DNS-rebinding protection (D5), and no token is set: **the server refuses to start**. The error says which variable to set. So `0.0.0.0` in Docker, or another loopback address such as `127.0.0.2`, can't run without a token, and so can't run without either protection.
  - On loopback without a token, it starts and logs a warning. Any local process can reach it, but no remote one can, and D5 blocks browser pages.
  - With a token, every host requires it.
- **The check (`http_auth.py`):** a pure ASGI middleware around the whole app.
  - **The only exemption** is an exact match: method `GET` or `HEAD` (Starlette answers `HEAD` for a `GET` route), and a path equal to `/health`. A prefix match would be an auth bypass. So `/health/`, `/healthz`, `/health/x`, `/mcp/`, `OPTIONS /health` and `POST /health` all need the token. Tests pin each one.
  - It only checks `http` scopes. Other scopes, in particular `lifespan`, pass through unchanged: the SDK's app starts its session manager in its lifespan, and without that every request fails.
  - It reads `Authorization: Bearer <token>`: the scheme, case-insensitive, then one or more spaces, then the token. Whitespace around the whole value is ignored, as HTTP header parsing already does. Anything else after the token, such as `Bearer <token> extra`, is a 401. Tests cover a double space, a trailing space and trailing text.
  - It compares **bytes** with `hmac.compare_digest`: the header value as Starlette decodes it (latin-1), encoded back to latin-1, against the ASCII token. Comparing `str` values would raise `TypeError` on a non-ASCII header and give a 500 instead of a 401. A test sends one.
  - A missing or wrong token gets `401`, with `WWW-Authenticate: Bearer` and a short JSON body. The body doesn't say which case it was.
  - A token in the URL (for example `?token=`) is never read.
- **Not chosen: the SDK's `TokenVerifier`.**
  - The SDK's bearer support is built for OAuth resource servers. `AuthSettings` requires an `issuer_url`, the address of an OAuth authorization server, and this server has none.
  - Filling it with a placeholder would make the server's own configuration claim something false. If `resource_server_url` were set, the server would also advertise protected-resource metadata pointing clients to an OAuth flow that can't complete.
  - The OAuth follow-up (D9) will use `TokenVerifier` and `AuthSettings` with a real authorization server. The middleware is then dropped.
- **Not chosen: several tokens or per-user tokens.** The server is single-tenant. To rotate the token, restart with a new one.
- **TLS:** the server speaks plain HTTP. Anything beyond localhost must sit behind TLS, from a hosting platform or a reverse proxy, or the token travels in clear text. The README says so next to the Docker instructions.

### D3. `file_path` over HTTP

Over HTTP, a `file_path` would name a file on the **server's** disk, not the caller's. A remote caller has no business reading it, and a prompt injection in the data could steer an agent to try. M3's D4 deferred this switch to M4.
- **The rule:**
  - Over stdio, `file_path` and `output_path` work as now.
  - Over HTTP, a `file_path` is rejected before any filesystem call, with a new `FilePathNotAllowedError` (a `DataQualityError`). The tools map it to a `ToolError`, as for every other domain error. The message: "Over HTTP this server can't read or write files on its own disk. Send the data itself: inline_content with the file's contents (CSV as text, .xlsx as base64) and filename_hint with its name."
  - **`output_path` is rejected on its own,** with the same error, whatever the input. M3 only rejects it alongside `inline_content`. Today `run_cleaning` checks `output_path` against the disk (its parent exists, the file doesn't) before it loads any input. So a call with an `output_path` and no input at all would still probe the server's filesystem through those distinct errors (Codex adversarial review). With `allow_file_path=False`, `run_cleaning` checks both `file_path` and `output_path` first, before any other validation or path check.
  - `clean_dataset` returns inline output, as it already does for inline input.
- **There is no opt-in in M4.** The allowed-root alternative (a `file_path` limited to one directory, such as a mounted volume) was considered and not chosen. It adds symlink and path-traversal rules for a case that stdio already covers. It could be a later amendment.
- **The stdio allowed-root option that M3's D4 deferred to M4 is dropped.** Over stdio the server runs as the user, on the user's machine, and can already reach whatever the user can; M3 documents that trust model in the README. An allowed root there would mostly restrict the user's own agent. No-overwrite and the client's approval prompt for `clean_dataset` stay the guards against a steered `output_path`. If it is wanted later, it can share the HTTP allowed-root design above.
- **Where it's enforced:**
  - **In the core:** `load_input` and `run_cleaning` gain `allow_file_path: bool = config.ALLOW_FILE_PATH`, with a default of `True`. The check runs before the network-path check, so no UNC lookup happens either.
  - **How the tools learn the setting:** `create_server(*, allow_file_path=True)` gives the app a lifespan that yields the server's options, on both transports. Each tool takes an optional `ctx: Context | None = None` parameter. The SDK finds it through `find_context_parameter`, which accepts `Optional[Context]`, and leaves it out of the input schema. The tool reads the setting from the request's lifespan context.
  - **Fail closed** (CI review): there are only two cases.
    - `ctx` is `None`: a direct Python call, as in the unit tests, which is local by nature. `file_path` is allowed.
    - `ctx` is present, so the call came through a server: the setting must come from the server's options. If the lifespan context is missing, or isn't the options object, the tool treats `file_path` as **not** allowed. A mis-wired server or an SDK change can then only break local file access, which a stdio test catches at once. It can never open it over HTTP.
    - Tests: an app whose lifespan yields nothing, or something else, rejects `file_path`. The stdio app allows it.
  - **Why not a mutable module setting:** it would let one process's tests leak into another's, and it hides which server a tool belongs to.
  - **Checked on mcp 2.3.0** with a probe server (`streamable_http_app(stateless_http=True, json_response=True)` and Starlette's `TestClient`):
    - the lifespan's value reached the tool on every stateless request, and the lifespan ran once, not per request
    - the tool's input schema listed only its own argument, without `ctx`
    - a direct call without a context received `None`
- **What a remote agent is told:**
  - **The server instructions** are chosen by transport, through the `instructions` argument `create_server` already passes to `MCPServer`. Over HTTP they say to send the data inline and don't mention file paths.
  - **The tools' argument descriptions** stay the same for both transports. The `file_path` description already says it means "the machine running this server".
  - **The `review_data_quality` prompt** keeps one argument and one text for both transports, so its registration doesn't change. A prompt's arguments are fixed when it is registered, so a per-transport argument would need a transport parameter on `register`, a change to the registration pattern.
    - The argument stays `file_path`. Its description becomes "Path to the .csv or .xlsx file to review (on a remote server, the agent reads it and sends its contents)".
    - Step 1 says: call `profile_dataset` with that path; if the server answers that it can't read files, read the file yourself and send its contents as `inline_content`, with its name as `filename_hint`.
    - The last paragraph says `clean_dataset` "writes a new file, or returns the cleaned data inline, and never changes the original".

### D4. Limits

- **Request body:**
  - The SDK caps a request body at 4 MiB by default (`DEFAULT_MAX_REQUEST_BODY_SIZE`), so inline data over HTTP would fail well below the loader's 50 MB.
  - M4 sets `max_request_body_size` from `MAX_HTTP_REQUEST_MB` (default **16**). That is enough for any CSV a model would paste, and for programmatic clients sending a few MB.
  - A larger body gets HTTP `413` from the SDK's `RequestBodyLimitMiddleware` before any tool runs. It rejects a declared `Content-Length` above the cap at once. For a chunked body without one, it counts the bytes as they arrive and stops at the cap. The 413 test covers both cases.
  - The README explains the 413, since the agent sees a transport error rather than a tool error. It also gives the effective sizes: base64 adds about a third, so 16 MB carries an XLSX of about 12 MB. A CSV can use almost the whole 16 MB, minus the JSON-RPC envelope and any escaping.
- **Environment overrides,** read once in `config.py` at import:

  | Variable | Overrides | Default |
  |---|---|---|
  | `DATA_QUALITY_MCP_MAX_FILE_SIZE_MB` | `MAX_FILE_SIZE_MB` | 50 |
  | `DATA_QUALITY_MCP_MAX_ROWS` | `MAX_ROWS` | 500,000 |
  | `DATA_QUALITY_MCP_MAX_XLSX_UNCOMPRESSED_MB` | `MAX_XLSX_UNCOMPRESSED_MB` | 200 |
  | `DATA_QUALITY_MCP_MAX_INLINE_OUTPUT_MB` | `MAX_INLINE_OUTPUT_MB` | 1 |
  | `DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB` | `MAX_HTTP_REQUEST_MB` (new) | 16 |

  - **Validation:** each must parse as a positive, finite number (an integer for the row count). `inf`, `nan`, `0`, a negative number and text are invalid.
  - **How an invalid value surfaces:** raising at import would show a bare traceback, before `main()` runs. So instead:
    - `config.py` keeps the default for an invalid variable, and records the problem in `CONFIG_ERRORS`, a list of messages naming the variable and the value.
    - `server.main()` checks that list before anything else, on both transports. If it isn't empty, it prints each message to stderr as `data-quality-mcp: invalid DATA_QUALITY_MCP_MAX_ROWS='abc': expected a positive whole number`, and exits with status 2, without serving.
    - Python code that imports the package directly gets the defaults, and the list says why.
  - **Effective overrides are logged at startup:** each variable that is set, and its value (none of them is a secret). So a stale variable left in a Claude Desktop config shows up in the log, rather than silently changing a limit.
  - **Tests:** set the variable, reload `config` with `importlib.reload`, and check `CONFIG_ERRORS` and `main()`'s exit and message. Keyword defaults in core modules are bound when those modules are defined, so a test of the overridden value itself reloads them too. Or it runs the server in a subprocess, which is how the CI smoke test checks it.
  - **Scope:** they apply to both transports. They are read before any core module binds its keyword defaults, because those modules import `config` first.
  - **Why so few:** only these 5 are limits an operator would size to a container (D6). The XLSX expansion cap is among them because it, more than the file size, bounds memory: M3's D2 notes that shared strings at that cap can take several times their XML size. Thresholds stay code defaults, so a report's meaning doesn't depend on the deployment.
  - **Not overridable:** `ALLOW_FILE_PATH` (set per transport by D3) and `ALLOW_NETWORK_PATHS` (stays `False`).

### D5. Host and Origin checks

DNS rebinding lets a web page in the user's browser reach a server on `localhost`.
- **On loopback:** the SDK enables its protection automatically when `host` is `127.0.0.1`, `localhost` or `::1`, allowing only loopback `Host` and `Origin` values. M4 passes `host` through, so that applies, and a test pins it. In the same probe, a request with `Host: evil.example:8000` got `421 Misdirected Request`. For IPv6 the SDK's list holds the bracketed forms (`[::1]:*` for `Host`, `http://[::1]:*` for `Origin`), so `Host: [::1]:8000` is accepted. A test pins that too.
- **Elsewhere** (`0.0.0.0` in Docker):
  - With `DATA_QUALITY_MCP_ALLOWED_HOSTS` (a comma-separated list such as `mcp.example.com,mcp.example.com:443`), the SDK's `TransportSecuritySettings` is enabled with those hosts. A request with an `Origin` header is then rejected, which is right for non-browser clients.
  - Without it, the check is off. The required token already stops a browser page, which can't attach it.
  - The later deployment spec should set the variable for its public hostname.
- **`/health` is outside the host check.** The SDK applies `TransportSecuritySettings` inside its MCP transport (`StreamableHTTPSessionManager`), so only `/mcp` is checked. Custom routes such as `/health` aren't. Docker's `HEALTHCHECK`, which calls `http://127.0.0.1:8000/health`, therefore keeps working when `DATA_QUALITY_MCP_ALLOWED_HOSTS` names only the public hostname. A test sets the variable and checks that `/health` from loopback still returns 200 while `/mcp` with the same `Host` is rejected. The CI smoke test runs a second container with the variable set.

### D6. Docker image

- **`Dockerfile`, two stages:**
  - **Build:** `python:3.13-slim` and the `uv` binary from `ghcr.io/astral-sh/uv`, both pinned (base image by digest).
    - Everything is installed from `uv.lock` into `/app/.venv` with `--frozen`, so the image matches the lock exactly, unlike `uv tool install`, which resolves afresh.
    - **Two steps,** so a code change doesn't reinstall the dependencies:
      1. copy `pyproject.toml` and `uv.lock`, then `uv sync --frozen --no-dev --no-install-project`
      2. copy the source (`src/`, plus the files Hatchling's build reads, such as `README.md`), then `uv sync --frozen --no-dev --no-editable`, which builds and installs the project itself into the venv
    - The runtime stage copies only the venv, so the package must be installed there as a real (non-editable) install.
  - **Runtime:** the same base, the venv only, and a non-root user.
    - `PYTHONUNBUFFERED=1`, `EXPOSE 8000`.
    - `CMD ["data-quality-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]`.
    - `HEALTHCHECK` calls `/health` with Python's `urllib`, because the slim image has no `curl`.
- **The token:** passed at run time (`docker run -e DATA_QUALITY_MCP_TOKEN=...` or `--env-file .env`), never stored in the image. Without one the container exits at startup (D2), and the log says why.
- **`.dockerignore`:** `.git`, `.venv`, `data/`, `tests/`, `planning/`, `.claude/`, caches and `.env`, so no local data or secret reaches the build context.
- **Dependabot** gains the `docker` ecosystem (weekly, grouped), so the pinned base image is updated by PR, like the Actions.
- **Sizing:** the implementation measures peak memory for the largest inputs HTTP can deliver at the defaults, and states the figures in **As built** and in the README:
  - a CSV just under the 16 MB body cap
  - an XLSX within the body cap that expands to near the 200 MB uncompressed cap
  - stdio's largest case, a 50 MB file of up to 500,000 rows, for comparison

  The README also says to lower the D4 limits for a smaller container, and that concurrent calls add up (D1).
- **No registry push in M4.** Publishing the image (for example to GHCR) belongs with the deployment (D9).
- **Python 3.13:** the newest release with wide wheel coverage, on pandas 3. CI's test matrix already covers it.

### D7. CI

- **`build.yml` gains a `Docker image` job:**
  - It builds the image with `docker build`. It doesn't push.
  - It starts it with a random token and waits for the health check.
  - Then it smoke-tests it over HTTP with a short Python script that uses only the standard library (`urllib.request`), so it doesn't depend on which HTTP client `mcp` ships:
    - `GET /health` returns 200.
    - `POST /mcp` `initialize` without a token returns 401, as does a wrong token. The right token returns 200.
    - `tools/list` lists the 5 tools.
    - `profile_dataset` with a small inline CSV returns a report with a quality score.
    - `profile_dataset` with a `file_path` returns the D3 error.
    - The container's log doesn't contain the token.
    - A second container started with `DATA_QUALITY_MCP_ALLOWED_HOSTS=example.com` still reports healthy.
  - The job has minimal `permissions:` and its action versions are pinned, as in every workflow.
- **The ruleset:** the new job is a required check, so the owner adds `Docker image` to the `main protection` ruleset's required checks after the implementation PR's first run (CLAUDE.md).
- **In-process tests in the normal test matrix** (Ubuntu × Python 3.10–3.14, Windows × 3.10 and 3.14). They use Starlette's `TestClient` as a context manager (`with TestClient(app) as client:`), which runs the app's lifespan, so they need no Docker and count toward coverage:
  - **Auth:** 401 for a missing token, the wrong scheme, a wrong token or a non-ASCII header; 200 for the right token; `/health` open; no token read from the URL.
  - **Logs:** no token in any log record, from the header or the query string; nothing on stdout.
  - **Startup refusals:** no token (unset, empty or whitespace) on `0.0.0.0` or `127.0.0.2`; a token that is too short or outside the RFC 6750 set; a limit variable that isn't a positive, finite number (`abc`, `0`, `-1`, `inf`, `nan`), which exits with status 2 and the D4 message, on stdio as well.
  - **`/health`:** the exact-match exemption, and its independence from the allowed-hosts list (D5).
  - **Body cap:** 413 above `MAX_HTTP_REQUEST_MB`, with a `Content-Length` and chunked.
  - **Host checks:** on loopback, a request with a foreign `Host` header is rejected, and `[::1]:8000` is accepted on `::1`. Elsewhere, the allowed-hosts list is applied.
  - **`file_path` over HTTP:** rejected by all 5 tools, while `inline_content` works. `clean_dataset` also rejects an `output_path` with inline input, with a file path, and with no input at all, before any filesystem call (a test spies on `check_output_path`). The instructions and the prompt are the HTTP variants.
  - **Stdio:** `create_server()` with no arguments is unchanged. The tool schemas don't include `ctx`. The existing tests pass unmodified.

### D8. Clients and docs

- **Claude Code:** `claude mcp add --transport http data-quality http://localhost:8000/mcp --header "Authorization: Bearer <token>"`.
- **VS Code** (`.vscode/mcp.json`) and the **MCP Inspector:** each with its own config example and the same header. They go in `examples/`, next to `claude_desktop_config.json`.
- **Claude Desktop and claude.ai:** stdio stays the way to use Claude Desktop locally. A claude.ai or Desktop custom connector needs a public HTTPS URL that Anthropic's servers can reach. It also needs OAuth: static request headers are a beta for a limited set of organizations ([connector authentication docs](https://claude.com/docs/connectors/building/authentication)). Both come with D9.
- **README:**
  - a "Run over HTTP" section: the token, the command, the clients, TLS, the 413 limit, sizing, and that `file_path` is off over HTTP
  - a "Docker" section
  - the trust model updated: over HTTP the caller can only send data, never read the server's files
- **`.env.example`:** `DATA_QUALITY_MCP_TOKEN=` (empty, with the generation command in a comment), plus the D4 and D5 variables, commented out with their defaults. `.env` is already gitignored; the implementation checks.
- **Makefile:** `run-http` (`uv run --env-file .env data-quality-mcp --transport http`), `docker-build`, and `docker-run` (`docker run --env-file .env -p 8000:8000 ...`). Both read the token from `.env`, so no shell-specific `export` or `$env:` is needed, and they work from PowerShell and Git Bash alike.
- **`docs/architecture.md`:** a transport section (stdio and HTTP, the middleware, the per-transport switch) and the updated diagram.

### D9. Roadmap

- **M4 becomes "Remote-ready server":** Streamable HTTP, bearer auth, the `file_path` switch, Docker and CI.
- **A later spec covers "Live deployment and OAuth":**
  - a hosting choice
  - publishing the image
  - an identity provider and the SDK's OAuth resource-server support (`TokenVerifier`, `AuthSettings`, protected-resource metadata), so the server works as a claude.ai custom connector
  - the public hostname for D5
- It can come before M5 (as a Milestone 4.5) or be folded into M5. That is decided when it is specced. M5 keeps its scope until then.

---

## Schema changes

**None to the report or to any tool's output.** Every TypedDict in `profiler_core/schema.py` is unchanged.
- **Tool inputs:** no argument is added or removed. `ctx` isn't part of the input schema (D3), and tests pin that.
- **Behaviour:** over HTTP a `file_path` or `output_path` is rejected with a new error (D3). The server instructions differ by transport. The `review_data_quality` prompt keeps its argument, and its text covers both transports.

## Invariant changes (need explicit approval)

Merging this spec also approves these `CLAUDE.md` changes, made in the implementation PR:
- **Layer boundary:** "`profiler_core/`, `cleaner_core/`, and `config.py` are pure Python and **never import `mcp`**" becomes "... **never import the MCP SDK (`mcp`, `mcp_types`) or a web stack (`starlette`, `uvicorn`, `httpx2`, `sse_starlette`)**". `tests/test_architecture.py` enforces the new list. "Only `tools/` and `server.py` may touch the MCP SDK" stays. `http_auth.py` uses Starlette but not the SDK, and the test checks that too.
- **Configuration:** add "A few limits can be overridden by `DATA_QUALITY_MCP_*` environment variables. They are read and validated in `config.py` only (milestone-4.md, D4)."
- **stdio hygiene:** add "Over HTTP, logs go to stderr too, and never include the token or request bodies."

## Tasks

1. **Config (D4):** add `ALLOW_FILE_PATH`, `MAX_HTTP_REQUEST_MB` and the environment overrides, with validation. Declare `starlette` and `uvicorn` in `pyproject.toml`.
2. **Core (D3):**
   - `FilePathNotAllowedError` in `exceptions.py`
   - `allow_file_path` in `load_input` / `load_data` and `run_cleaning`, checked before any filesystem or network-path check. In `run_cleaning` it covers `output_path` too, whatever the input.
3. **Tools (D3):**
   - the optional `ctx` parameter on the 5 tools, and the setting read from the lifespan context
   - the HTTP variant of the instructions, and the prompt's transport-neutral wording
4. **`http_auth.py` (D2):** the bearer middleware, the token rules and the per-request log line.
5. **`server.py` (D1, D2, D4, D5):**
   - the command line and `create_server(*, allow_file_path=True)`
   - the HTTP app: stateless, JSON responses, the body cap, the host checks, `/health`, the middleware
   - serving with uvicorn (its `log_config` on stderr, its access log off) and the startup checks
6. **Tests (D7):** the in-process HTTP tests, plus the architecture test's new forbidden imports.
7. **Docker (D6):** `Dockerfile`, `.dockerignore`, and Dependabot's `docker` entry.
8. **CI (D7):** the `Docker image` job and its smoke-test script.
9. **Docs (D8):**
   - README, `docs/architecture.md`, `.env.example`, the client examples and the Makefile targets
   - `CLAUDE.md` (Invariant changes)
   - the overview:
     - the roadmap rows (D9)
     - Dual Input Mode (D3) and Resource Limits (D4)
     - the stack table's transport and Docker rows
     - the CI table's `build.yml` and Dependabot rows, and the sentence listing the required checks
     - the repository structure: `http_auth.py`, `Dockerfile`, `.dockerignore`, the new `examples/` files, and the `.env.example` comment ("Only needed once remote auth is added (Milestone 4)")
10. **Version 0.5.0** in `pyproject.toml`, `__init__.py` and `uv.lock`.
11. **This spec:** the status line and an **As built** section.

## Verification

- **Commands:** `make format`, `make lint`, `make test`, plus the Python 3.10 / pandas 2.3.3 run in the scratch environment. Coverage stays at or above its current level.
- **Local HTTP check (Windows, no Docker needed):** start `make run-http` with a token, then connect Claude Code with `claude mcp add --transport http ... --header ...`.
  - Profile a CSV inline: the same score as over stdio, 77 for the fixture.
  - Ask for a `file_path`: the D3 error comes back.
  - This is a step for you, since it changes your Claude Code config. I'll give the exact commands.
- **Docker:** CI's `Docker image` job. Docker isn't installed on this machine, so the image is first built in CI. A local build is optional, if you install Docker Desktop.
- **Stdio:** Claude Desktop, after `uv tool install --editable . --reinstall`, gives the same profile as before.
- **Pre-PR review** for the implementation PR, then the CI review under the stop rule.

## Out of scope

- A live deployment, publishing the image, OAuth and claude.ai connectors (D9).
- An allowed-root option for `file_path`, over HTTP or stdio (D3).
- Rate limiting and a concurrency cap: the server is single-tenant and token-gated (D1). The deployment spec should revisit them for a public endpoint.
- SSE (the legacy HTTP transport): Streamable HTTP replaces it.
