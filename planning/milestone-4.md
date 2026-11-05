# Milestone 4: Remote-Ready Server (HTTP, Docker, Bearer Auth)

> **Status: proposed.** This spec is reviewed and merged before any implementation.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Where this spec changes a decision recorded there (the roadmap, D9), **this spec wins** until the implementation PR updates the overview. The previous milestone is [milestone-3.5.md](milestone-3.5.md).

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
- `tests/test_architecture.py` keeps passing, and is extended so the core can't import `starlette`, `uvicorn` or `httpx` either (see **Invariant changes**).
- No new runtime dependencies: `starlette` and `uvicorn` already come with `mcp` (1.7.0 and 0.54.0 in `uv.lock`).

---

## Guiding principle: fail closed, stay simple

The server is single-tenant: one token, one operator, no per-user state.
- **Fail closed:** every rule below refuses rather than guesses. The server won't start on a public interface without a token, and it rejects a `file_path` over HTTP. A limit it can't parse is a startup error.
- **Stdio doesn't change:** `data-quality-mcp` with no arguments behaves exactly as in 0.4.3, so Claude Desktop and every existing config keep working.
- **Analyzer parity:** nothing to port. Transport, auth and Docker are MCP-only, so this milestone has no **Deviations** table.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Transport and command line | **Streamable HTTP at `/mcp`, stateless, with JSON responses.** `data-quality-mcp --transport http [--host 127.0.0.1] [--port 8000]`, which binds to loopback by default. |
| D2 | Authentication | **One static bearer token**, from the `DATA_QUALITY_MCP_TOKEN` environment variable. A small middleware compares it in constant time. It is required except on a loopback host. OAuth comes later (D9). |
| D3 | `file_path` over HTTP | **Off.** Over HTTP the server rejects `file_path` (and so `output_path`) with an error that says to send `inline_content`. Stdio is unchanged. There is no opt-in in M4. |
| D4 | Limits | **A request-body cap for HTTP (16 MB), plus environment overrides for 4 limits,** validated at startup. The loader's limits keep their defaults. |
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
- **Concurrency:** the SDK runs synchronous tools in worker threads (`anyio.to_thread`), so a long profile doesn't block `/health` or other requests. Concurrent calls on large inputs each need their own memory, and M4 has no cap on that: the server is token-gated and single-tenant. The README gives a sizing note (D6).
- **Logging:** stderr, as for stdio. Uvicorn's access log records the method, path and status. Request headers and bodies are never logged, so the token isn't either. The startup log names the transport, the address and whether auth is on, never the token.

### D2. Authentication

- **The token:**
  - It comes from `DATA_QUALITY_MCP_TOKEN` in the environment.
  - A token is never accepted as a command-line argument, because process lists show arguments to other users.
  - A token of fewer than 32 characters is a startup error. The README shows how to generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"` (43 characters).
- **When it's required:**
  - The host isn't a loopback address (`127.0.0.1`, `::1`, `localhost`), including `0.0.0.0` in Docker, and no token is set: **the server refuses to start**. The error says which variable to set.
  - On loopback without a token, it starts and logs a warning. Any local process can reach it, but no remote one can, and D5 blocks browser pages.
  - With a token, every host requires it.
- **The check (`http_auth.py`):** a pure ASGI middleware around the whole app, except `GET /health`.
  - It reads `Authorization: Bearer <token>`. The scheme name is case-insensitive.
  - It compares the token with `hmac.compare_digest`.
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
  - Over HTTP, a `file_path` is rejected before any filesystem call, with a new `FilePathNotAllowedError` (a `DataQualityError`). The tools map it to a `ToolError`, as for every other domain error. The message: "This server runs remotely and can't read files from its own disk. Send the data itself: inline_content with the file's contents (CSV as text, .xlsx as base64) and filename_hint with its name."
  - `output_path` already requires file input (M3 D4), so it is rejected too. `clean_dataset` returns inline output, as it already does for inline input.
- **There is no opt-in in M4.** The allowed-root alternative (a `file_path` limited to one directory, such as a mounted volume) was considered and not chosen. It adds symlink and path-traversal rules for a case that stdio already covers. It could be a later amendment.
- **Where it's enforced:**
  - **In the core:** `load_input` and `run_cleaning` gain `allow_file_path: bool = config.ALLOW_FILE_PATH`, with a default of `True`. The check runs before the network-path check, so no UNC lookup happens either.
  - **How the tools learn the setting:** `create_server(*, allow_file_path=True)` gives the app a lifespan that yields the server's options. Each tool takes an optional `ctx: Context | None = None` parameter. The SDK finds it through `find_context_parameter`, which accepts `Optional[Context]`, and leaves it out of the input schema. The tool reads the setting from the request's lifespan context. A direct Python call without a context, as in the unit tests, behaves as a local call, which is what such a call is.
  - **Why not a mutable module setting:** it would let one process's tests leak into another's, and it hides which server a tool belongs to.
  - **Plan mode checks** that the lifespan context reaches stateless HTTP requests in mcp 2.3.0. If it doesn't, the fallback is `register(app, *, allow_file_path=True)`, with thin wrappers that keep each tool's signature. That would change the registration pattern, so it would need an Amendment.
- **What a remote agent is told:** the server instructions are chosen by transport. Over HTTP they say to send the data inline and don't mention file paths.
  - The tools' argument descriptions stay the same for both transports. The `file_path` description already says it means "the machine running this server".
  - The `review_data_quality` prompt takes `filename` over HTTP instead of `file_path`. Its text asks the agent to read that file itself and send its contents inline.

### D4. Limits

- **Request body:**
  - The SDK caps a request body at 4 MiB by default (`DEFAULT_MAX_REQUEST_BODY_SIZE`), so inline data over HTTP would fail well below the loader's 50 MB.
  - M4 sets `max_request_body_size` from `MAX_HTTP_REQUEST_MB` (default **16**). That is enough for any CSV a model would paste, and for programmatic clients sending a few MB.
  - A larger body gets HTTP `413` from the SDK before any tool runs. The README explains this, since the agent sees a transport error rather than a tool error.
- **Environment overrides,** read once in `config.py` at import:

  | Variable | Overrides | Default |
  |---|---|---|
  | `DATA_QUALITY_MCP_MAX_FILE_SIZE_MB` | `MAX_FILE_SIZE_MB` | 50 |
  | `DATA_QUALITY_MCP_MAX_ROWS` | `MAX_ROWS` | 500,000 |
  | `DATA_QUALITY_MCP_MAX_INLINE_OUTPUT_MB` | `MAX_INLINE_OUTPUT_MB` | 1 |
  | `DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB` | `MAX_HTTP_REQUEST_MB` (new) | 16 |

  - **Validation:** each must parse as a positive number (an integer for the row count). Anything else raises at import with a message naming the variable, so the server fails at startup rather than on the first call.
  - **Scope:** they apply to both transports. They are read before any core module binds its keyword defaults, because those modules import `config` first.
  - **Why so few:** only these 4 are limits an operator would size to a container (D6). Thresholds stay code defaults, so a report's meaning doesn't depend on the deployment.
  - **Not overridable:** `ALLOW_FILE_PATH` (set per transport by D3) and `ALLOW_NETWORK_PATHS` (stays `False`).

### D5. Host and Origin checks

DNS rebinding lets a web page in the user's browser reach a server on `localhost`.
- **On loopback:** the SDK enables its protection automatically when `host` is loopback, allowing only loopback `Host` and `Origin` values. M4 passes `host` through, so that applies, and a test pins it.
- **Elsewhere** (`0.0.0.0` in Docker):
  - With `DATA_QUALITY_MCP_ALLOWED_HOSTS` (a comma-separated list such as `mcp.example.com,mcp.example.com:443`), the SDK's `TransportSecuritySettings` is enabled with those hosts. A request with an `Origin` header is then rejected, which is right for non-browser clients.
  - Without it, the check is off. The required token already stops a browser page, which can't attach it.
  - The later deployment spec should set the variable for its public hostname.

### D6. Docker image

- **`Dockerfile`, two stages:**
  - **Build:** `python:3.13-slim` and the `uv` binary from `ghcr.io/astral-sh/uv`, both pinned (base image by digest).
    - Dependencies are installed with `uv sync --frozen --no-dev --no-editable` into `/app/.venv`, so the image matches `uv.lock` exactly. Unlike `uv tool install`, which resolves afresh.
    - Dependencies are installed before the source is copied, so a code change doesn't reinstall them.
  - **Runtime:** the same base, the venv only, and a non-root user.
    - `PYTHONUNBUFFERED=1`, `EXPOSE 8000`.
    - `CMD ["data-quality-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]`.
    - `HEALTHCHECK` calls `/health` with Python's `urllib`, because the slim image has no `curl`.
- **The token:** passed at run time (`docker run -e DATA_QUALITY_MCP_TOKEN=...` or `--env-file .env`), never stored in the image. Without one the container exits at startup (D2), and the log says why.
- **`.dockerignore`:** `.git`, `.venv`, `data/`, `tests/`, `planning/`, `.claude/`, caches and `.env`, so no local data or secret reaches the build context.
- **Dependabot** gains the `docker` ecosystem (weekly, grouped), so the pinned base image is updated by PR, like the Actions.
- **Sizing:** the implementation measures the peak memory of profiling and cleaning an input at the default limits (50 MB, and 500,000 rows). It states the figure in **As built** and in the README, which also says to lower the limits with the D4 variables for a smaller container.
- **No registry push in M4.** Publishing the image (for example to GHCR) belongs with the deployment (D9).
- **Python 3.13:** the newest release with wide wheel coverage, on pandas 3. CI's test matrix already covers it.

### D7. CI

- **`build.yml` gains a `Docker image` job:**
  - It builds the image with `docker build`. It doesn't push.
  - It starts it with a random token and waits for the health check.
  - Then it smoke-tests it over HTTP with a short Python script using `httpx`, already a dependency of `mcp`:
    - `GET /health` returns 200.
    - `POST /mcp` `initialize` without a token returns 401, as does a wrong token. The right token returns 200.
    - `tools/list` lists the 5 tools.
    - `profile_dataset` with a small inline CSV returns a report with a quality score.
    - `profile_dataset` with a `file_path` returns the D3 error.
    - The container's log doesn't contain the token.
  - The job has minimal `permissions:` and its action versions are pinned, as in every workflow.
- **The ruleset:** the new job is a required check, so the owner adds `Docker image` to the `main protection` ruleset's required checks after the implementation PR's first run (CLAUDE.md).
- **In-process tests in the normal matrix** (Ubuntu and Windows, every Python version). They use Starlette's `TestClient` or `httpx.ASGITransport`, so they need no Docker and count toward coverage:
  - **Auth:** 401 for a missing token, the wrong scheme or a wrong token; 200 for the right token; `/health` open; no token read from the URL.
  - **Startup refusals:** no token on `0.0.0.0`; a token of fewer than 32 characters; an unparseable limit variable.
  - **Body cap:** 413 above `MAX_HTTP_REQUEST_MB`.
  - **Host checks:** on loopback, a request with a foreign `Host` header is rejected. Elsewhere, the allowed-hosts list is applied.
  - **`file_path` over HTTP:** rejected by all 5 tools, while `inline_content` works. The instructions and the prompt are the HTTP variants.
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
- **Makefile:** `run-http` (`uv run data-quality-mcp --transport http`), `docker-build` and `docker-run` (`--env-file .env -p 8000:8000`). They are plain commands, so they work in PowerShell as well as Git Bash.
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
- **Behaviour:** over HTTP a `file_path` is rejected with a new error (D3). The server instructions and the `review_data_quality` prompt's argument differ by transport.

## Invariant changes (need explicit approval)

Merging this spec also approves these `CLAUDE.md` changes, made in the implementation PR:
- **Layer boundary:** "`profiler_core/`, `cleaner_core/`, and `config.py` are pure Python and **never import `mcp`**" becomes "... **never import `mcp` or a web framework** (`starlette`, `uvicorn`, `httpx`)". `tests/test_architecture.py` enforces the new list. "Only `tools/` and `server.py` may touch the MCP SDK" stays. `http_auth.py` uses Starlette but not the SDK.
- **Configuration:** add "A few limits can be overridden by `DATA_QUALITY_MCP_*` environment variables. They are read and validated in `config.py` only (milestone-4.md, D4)."
- **stdio hygiene:** add "Over HTTP, logs go to stderr too, and never include the token or request bodies."

## Tasks

1. **Config (D4):** add `ALLOW_FILE_PATH`, `MAX_HTTP_REQUEST_MB` and the environment overrides, with validation.
2. **Core (D3):**
   - `FilePathNotAllowedError` in `exceptions.py`
   - `allow_file_path` in `load_input` / `load_data` and `run_cleaning`, checked before any filesystem or network-path check
3. **Tools (D3):**
   - the optional `ctx` parameter on the 5 tools, and the setting read from the lifespan context
   - the HTTP variants of the instructions and of the prompt
4. **`http_auth.py` (D2):** the bearer middleware and the token rules.
5. **`server.py` (D1, D2, D4, D5):**
   - the command line and `create_server(*, allow_file_path=True)`
   - the HTTP app: stateless, JSON responses, the body cap, the host checks, `/health`, the middleware
   - serving with uvicorn and the startup checks
6. **Tests (D7):** the in-process HTTP tests, plus the architecture test's new forbidden imports.
7. **Docker (D6):** `Dockerfile`, `.dockerignore`, and Dependabot's `docker` entry.
8. **CI (D7):** the `Docker image` job and its smoke-test script.
9. **Docs (D8):**
   - README, `docs/architecture.md`, `.env.example`, the client examples and the Makefile targets
   - `CLAUDE.md` (Invariant changes)
   - the overview: the roadmap rows (D9), Dual Input Mode, the Resource Limits section and the stack table's M4 rows
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
- An allowed-root option for `file_path` over HTTP (D3).
- Rate limiting and a concurrency cap: the server is single-tenant and token-gated (D1). The deployment spec should revisit them for a public endpoint.
- SSE (the legacy HTTP transport): Streamable HTTP replaces it.
