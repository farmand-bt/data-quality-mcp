# Milestone 4.5: Live Deployment and OAuth

> **Status: proposed.** This spec is for review. Nothing is implemented yet.
> Shared context (architecture, report schema, design decisions) is in [project-overview.md](project-overview.md). Where this spec changes a decision recorded there, or in [milestone-4.md](milestone-4.md), **this spec wins** until the implementation PR updates them. In particular, it keeps the static token that M4's D2 said would be dropped when OAuth arrives (D5). The previous milestone is [milestone-4.md](milestone-4.md).

**Goal:** run the server live on the public internet, usable from claude.ai (as a custom connector) and from Claude Code, by its owner only, at no cost:
- a free Render web service, built from this repo's `Dockerfile`
- OAuth sign-in through WorkOS AuthKit, with the server as an OAuth resource server
- a server-side check that the signed-in user is the owner
- limits lowered to fit a 512 MB, 0.1 CPU instance, and measured there

**Zero cost is a hard rule.** This is a personal portfolio project, and not even a small monthly fee is acceptable. Every choice below stays on a free tier, and no payment method is added to any account (D1, D2). If a step ever asks for a card, it stops there and we pick again.

**Layer boundary:** nothing in `profiler_core/`, `cleaner_core/` or `config.py` changes.
- **The JWT check** (signature, issuer, audience, expiry, owner) goes in `http_auth.py`, next to the static-token check. It uses PyJWT and the standard library, never `mcp`.
- **The SDK wiring** goes in `server.py`: `server.py` wraps that check in the SDK's `TokenVerifier` protocol, and builds the `AuthSettings`. The wrapper is a few lines that turn the checked claims into the SDK's `AccessToken`.
- **`tests/test_architecture.py`** keeps passing. `jwt` joins the list the core may not import, and the check that `http_auth.py` imports neither `mcp` nor `mcp_types` stays.
- **One new direct dependency, no new package:** `pyjwt[crypto]`. It is already in the lock, because `mcp` requires `pyjwt[crypto]>=2.10.1`. `http_auth.py` imports it directly, so `pyproject.toml` declares it, as M4 did for Starlette and uvicorn (see D3 for the lower bound).

---

## Guiding principle: owner only, fail closed, nothing to pay

- **Owner only:** the live server answers one person. The identity provider may let anyone create an account, so being signed in isn't enough. The server checks that the token belongs to the owner (D4).
- **Fail closed:**
  - **OAuth settings:** OAuth mode won't start with a setting missing or malformed, or with the owner unset (D6).
  - **Tokens:** every claim the server relies on must be present and valid, or the request is refused.
  - **Mixed modes:** mixing auth modes is a startup error, as in M4 (D5).
- **Nothing to pay:**
  - Both providers are used on their free tiers, without a payment method.
  - On Render, running out of free hours, bandwidth or build minutes suspends the service, or stops builds, until the next month. It can only be billed if a payment method is on file (D1).
- **Same build everywhere:** the live server is built from the same `Dockerfile` and commit that CI builds and tests (Render rebuilds it, D1; nothing is published). It is configured only through environment variables, so it can move to another host (Cloud Run, D10) without a code change.
- **Stdio and M4's HTTP modes don't change:**
  - `data-quality-mcp` with no arguments behaves as in 0.5.0.
  - So does `--transport http` with `DATA_QUALITY_MCP_TOKEN` or `--no-auth`.
- **Analyzer parity:** nothing to port. Deployment and auth are MCP-only, so this milestone has no **Deviations** table.

## Decisions to confirm in review

| # | Decision | Recommendation |
|---|---|---|
| D1 | Host | **A Render free web service, built by Render from this repo's `Dockerfile`, described in `render.yaml`.** It deploys from `main` after CI passes. No payment method on the Render account. |
| D2 | Identity provider | **WorkOS AuthKit, staging environment,** with Client ID Metadata Documents (CIMD) and Dynamic Client Registration (DCR) on, and the server's `/mcp` URL as a Resource Indicator. No payment method, no custom domain. |
| D3 | The server as a resource server | **The SDK's `TokenVerifier` and `AuthSettings`.** The SDK serves the protected-resource metadata (RFC 9728) and the 401 that points to it. Tokens are JWTs checked against AuthKit's JWKS with PyJWT. There is no proxy for the authorization server's metadata. |
| D4 | Owner only | **The token's `sub` must equal `DATA_QUALITY_MCP_OAUTH_OWNER`,** the owner's WorkOS user ID. It is required in OAuth mode. |
| D5 | Auth modes | **Three modes, chosen at startup:** a static token (M4), OAuth (new), or `--no-auth` (M4, loopback only). Setting two is a startup error. The static token stays for local and Docker use, CI and VS Code. |
| D6 | Configuration | **Three new variables:** `DATA_QUALITY_MCP_PUBLIC_URL`, `DATA_QUALITY_MCP_OAUTH_ISSUER` and `DATA_QUALITY_MCP_OAUTH_OWNER`, plus an optional `DATA_QUALITY_MCP_OAUTH_JWKS_URL`. In OAuth mode, the allowed hosts default to the public URL's host. |
| D7 | Fitting the free instance | **Lower the D4 limits through `render.yaml`,** chosen by measuring the worst cases in a container capped like Render's (`--memory=512m --cpus=0.1`). |
| D8 | Cold starts | **Accept them and document them.** A free service sleeps after 15 minutes idle and takes about a minute to wake. There is no keep-alive pinger. |
| D9 | CI | **In-process OAuth tests with locally signed tokens,** and an OAuth-mode container in the `Docker image` job. Render deploys only after the checks pass. |
| D10 | Docs | **A deployment guide (`docs/deployment.md`):** Render, tested; Cloud Run, written with its cost guards but marked untested. The README gains client setup for claude.ai and for Claude Code with OAuth. |

### D1. Host: Render free web service

- **Why Render:**
  - It's free without a card, per its docs (to confirm at sign-up).
  - It builds a Docker image from the repo, and terminates TLS on an `onrender.com` subdomain.
  - Google Cloud Run stays a later option (D10). Its free tier needs a billing account and a card, and its spend cap doesn't cover every charge. The owner prefers Google's features, but not that risk.
- **What the free instance is** ([render.com/docs/free](https://render.com/docs/free), [Blueprint spec](https://render.com/docs/blueprint-spec)):
  - **Size:** 512 MB of memory and 0.1 CPU, one instance, no persistent disk. Files written locally are lost on a restart, which this server never relies on.
  - **Sleep:** it spins down after 15 minutes without inbound traffic, and takes about a minute to wake (D8).
  - **Hours:** 750 free instance hours per workspace per month. A sleeping service uses none, and one service that never slept would use at most 744.
  - **Restarts:** "Render might restart a Free web service at any time." The server is stateless (M4's D1), so a restart loses nothing.
  - **Fit:** Render says not to use free services for production. This is a personal demo, not production.
- **What can cost money, and why it can't here:**
  - **Bandwidth:** past the included bandwidth, Render bills a supplement. Without a payment method, it "suspends all of your Free services for the remainder of the month" instead.
  - **Build minutes:** past the included build minutes, Render bills, unless no payment method is on file. Then it "disables all new builds for your workspace for the remainder of the month".
  - **Hours:** past the free hours, Render suspends free services until the next month.
  - **So the rule is: never add a payment method to the Render workspace.** The README and `docs/deployment.md` say so, and that the service then stops, rather than bills, when a quota runs out.
- **Built from the `Dockerfile`, not a published image:**
  - Render builds the repo's `Dockerfile` with BuildKit on every deploy, so it runs the same build CI tests.
  - **Not chosen: publishing the image** to GHCR and deploying it. Render's prebuilt-image services don't auto-deploy, so they would need a deploy hook, whose URL is a secret in GitHub. That's one more credential for no gain on this host. Publishing can come with Cloud Run, if it's ever used (D10).
- **`render.yaml` (a Blueprint)** records the service in the repo:
  - `type: web`, `runtime: docker`, `plan: free`, `dockerfilePath: ./Dockerfile`, `healthCheckPath: /health`, `autoDeployTrigger: checksPass`
  - **`PORT=8000`:** Render's default `PORT` is 10000, and the image listens on 8000 (M4's D6). Setting `PORT` tells Render which port to route to, so the `Dockerfile` and its `CMD` stay unchanged.
  - **The D7 limits,** as plain values. None of them is a secret.
  - **The D6 variables** with `sync: false`, so their values are typed into Render's dashboard and never committed. None of them is a secret either (the issuer and the resource URL are public in the metadata), but keeping them out of the repo means a fork deploys its own server without editing the file.
  - **No secret is needed on Render at all:** in OAuth mode there is no static token.
  - The Blueprint syntax (`sync: false` and the field names) is checked against Render's current docs during implementation. If creating a service from a Blueprint asks for a payment method, the service is created in the dashboard with the same settings, and `render.yaml` stays as the record.
- **Deploys:**
  - The service deploys `main` with "After CI Checks Pass" (`autoDeployTrigger: checksPass`): Render deploys a commit only after all its GitHub checks pass.
  - A commit with no checks, such as one whose message contains `[skip ci]` (the #27 case), isn't deployed. The next one is.
  - During the implementation PR, the service tracks the PR's branch instead, so the live checks run before the merge (Verification). After the merge, the owner switches it to `main`.
  - **On the PR branch, the wait can be long:** `checksPass` waits for every check on the head commit, including the advisory `Claude review`. That review takes up to 20 minutes, and can fail on a usage limit. If it holds a deploy up, the owner starts a manual deploy from the dashboard. On `main` the review doesn't run.
- **Health check:** `/health`, which M4 already serves without auth. Render routes traffic to a new deploy only once it passes, and restarts an instance after 60 seconds of failures. The `Dockerfile`'s own `HEALTHCHECK` is left for Docker; whether Render reads it doesn't matter.
- **Proxy and TLS:** Render terminates TLS and forwards plain HTTP to the container.
  - The server builds every URL it advertises from `DATA_QUALITY_MCP_PUBLIC_URL` (D6), never from the request. So it doesn't need uvicorn's proxy headers, and `--forwarded-allow-ips` stays unset.
  - The request's `Host` is the public hostname, which D6's allowed hosts accept.
- **Render's GitHub access:** the owner connects Render to GitHub with access to this one repository only.

### D2. Identity provider: WorkOS AuthKit

- **Why AuthKit:**
  - It's free up to 1 million monthly active users.
  - It documents MCP support: CIMD, DCR, Resource Indicators (RFC 8707), and JWT access tokens checked against a JWKS ([workos.com/docs/authkit/mcp](https://workos.com/docs/authkit/mcp)).
  - Its authorization-server metadata advertises what claude.ai requires: `code_challenge_methods_supported: ["S256"]`, and `"none"` among `token_endpoint_auth_methods_supported`.
- **Staging environment:**
  - WorkOS's pricing says "Only production environments are billed." The free 1M MAU applies to production too, but staging can't produce a bill at all, which fits the zero-cost rule.
  - For one user, nothing is lost: the sign-in page may show that it's a staging environment.
  - If staging turns out to block something the connector needs, switching to production is a decision for the owner, and only if it needs no payment method.
- **Client registration:**
  - **CIMD** is off by default in WorkOS. It is turned on under Connect → Configuration.
  - **DCR** is turned on too, because WorkOS's example metadata doesn't show `client_id_metadata_document_supported`, and Claude uses CIMD only when the authorization server advertises it. Claude falls back to DCR otherwise. Which one Claude actually uses is recorded in **As built**.
  - **Open registration is safe here:** any client may register, and anyone may sign in, but only the owner's token is accepted (D4).
- **The Resource Indicator:** the server's `/mcp` URL, e.g. `https://<service>.onrender.com/mcp`, exactly as entered in claude.ai.
  - AuthKit then issues tokens whose `aud` is that URL.
  - Without a Resource Indicator, `aud` would be the environment's client ID, and the server would refuse every token.
- **Redirect URIs:** with CIMD or DCR the client brings its own (`https://claude.ai/api/mcp/auth_callback`, or Claude Code's localhost callback). The WorkOS docs don't ask for any to be registered. If the dashboard does, the owner adds those two.
- **Sign-in methods:** whatever AuthKit offers by default (email with a code or password, or a social login). The owner chooses at sign-up; the server doesn't care which.
- **Sign-ups:** WorkOS doesn't document a setting that limits sign-ups to one email. If the dashboard offers one (sign-ups off, or an allowlist), the owner turns it on as a second guard. D4 holds either way.
- **Not chosen:** a custom AuthKit domain ($99 a month), and refresh-token or session settings beyond the defaults.
  - AuthKit's access tokens last 5 minutes by default.
  - Clients refresh them with a refresh token, which AuthKit issues to CIMD and DCR clients.

### D3. The server as an OAuth resource server

- **The SDK's resource-server support,** checked on mcp 2.3.0 with a probe: a local RSA key stood in for AuthKit's JWKS, and the app was `MCPServer(token_verifier=..., auth=AuthSettings(...))` with `streamable_http_app(stateless_http=True, json_response=True)`.
  - **`AuthSettings`** takes the issuer URL (the AuthKit domain), `resource_server_url` (the public `/mcp` URL), no required scopes, and `validate_token_resource=True`, which also silences the SDK's warning about leaving it unset.
  - **The metadata:** `GET /.well-known/oauth-protected-resource/mcp` returned the metadata, without auth: `{"resource": "https://<host>/mcp", "authorization_servers": ["https://<tenant>.authkit.app"], "bearer_methods_supported": ["header"]}`. That's the minimal response WorkOS's guide shows, at the path RFC 9728 derives from `/mcp`. The root path, `/.well-known/oauth-protected-resource`, is a 404, which is correct for a resource with a path. Clients find the metadata from the 401's header.
  - **A request without a token** to `/mcp` got `401`, with `WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="https://<host>/.well-known/oauth-protected-resource/mcp"`. This is what claude.ai needs to start the OAuth flow.
  - **Unauthenticated routes:** `/health`, a custom route, stayed open. The SDK puts its auth check (`RequireAuthMiddleware`) on `/mcp` only.
  - **Accepted:** the owner's token got 200. Inside the tool, `get_access_token()` returned its subject.
  - **Refused:** another user's token, a wrong audience, a wrong issuer, an expired token and a token without `sub` each got 401.
  - **The header format:**
    - **Extra spaces:** a token after more than one space also got 401. The SDK accepts the scheme in any case, but strips exactly 7 characters (`bearer `), so the extra spaces stay in the token. Clients send one space, as RFC 6750 says.
    - **A difference between modes:** M4's static-token mode accepts several spaces. Both behaviours stay as they are.
  - **The auth middleware runs on every route,** not just `/mcp`. The SDK installs Starlette's `AuthenticationMiddleware` app-wide, and that middleware only catches `AuthenticationError`. So any other exception from `verify_token` would be a 500, even on `/health` or the metadata route, for any request with a `Bearer` header. Hence the never-raise rule below.
- **The check, in `http_auth.py`** (no `mcp`):
  - **Decoding:** `jwt.decode` with:
    - `algorithms=["RS256"]`, fixed, so `none` or an HMAC algorithm are never accepted
    - `issuer` = the issuer
    - `audience` = the public `/mcp` URL
    - `options={"require": ["exp", "iat", "iss", "aud", "sub"]}`
    - a 30-second leeway for clock skew
  - **The leeway doesn't extend `exp`.** After the verifier returns, the SDK itself refuses a token whose `expires_at` has passed, with no leeway, and the wrapper sets `expires_at` to `exp`. So the leeway only helps `iat` and `nbf`. No test expects a token past its `exp` to be accepted.
  - **The owner:** `sub` must equal the owner (D4).
  - **On a refusal:** it returns `None`, and logs one line naming the reason class, e.g. `token refused: InvalidAudienceError` or `token refused: not the owner`, never the token or its claims.
  - **It never raises:**
    - Any exception at all, not only `jwt.PyJWTError`, is logged by class, and the token is refused.
    - The claims the wrapper uses are type-checked before the `AccessToken` is built: `sub` must be a string, `scope` a string if present, and `client_id`/`azp` strings if present. A claim of the wrong type refuses the token.
    - So a strange token gets a 401 on `/mcp`, and can't turn `/health` into a 500.
  - **Cheap checks before any key lookup,** on the unverified header and claims:
    - in this order: the header's `alg` is RS256 and it has a `kid`, then `iss` matches, then `aud`, then `sub` is the owner
    - the first failure is the reason logged, so `token refused: not the owner` also shows that `iss` and `aud` matched
    - anything that fails is refused without touching the JWKS
    - Only a token claiming the owner's ID can trigger a fetch. WorkOS IDs are long and random, and the owner ID is never logged or committed.
    - These checks only decide whether a fetch is worth making. The full signature check still decides acceptance.
  - WorkOS's guide doesn't name the signing algorithm. The implementation confirms RS256 from a real token's header and from the JWKS. If it differs, the fixed list follows the JWKS, never the token.
- **The JWKS:** `jwt.PyJWKClient(<issuer>/oauth2/jwks)`, the URL WorkOS documents, or `DATA_QUALITY_MCP_OAUTH_JWKS_URL` if set.
  - **Fetched lazily,** on the first token, never at startup. A sleeping or unreachable AuthKit then can't stop the server from starting or passing its health check.
  - **Cached:** the key set is kept for 5 minutes (PyJWT's default), with a 5-second fetch timeout.
  - **PyJWT's own throttle isn't enough** (Codex and pre-PR review):
    - **What it does:** an unknown `kid` makes PyJWT refetch the set at most once per 30 seconds (`cooldown_duration`).
    - **What it misses:** that cooldown only counts from the last *successful* fetch. With an empty or expired cache, PyJWT fetches before it checks the cooldown, so while fetches are failing (AuthKit down, DNS trouble) every token would try again.
    - **The cost:** each fetch holds PyJWT's client lock for up to the 5-second timeout. The waiting checks sit in worker threads that tool calls also need.
  - **So the server adds its own fetch gate,** around PyJWT:
    - **One fetch at a time:** a check that needs a fetch while another is in flight doesn't wait. It refuses its token at once (401).
    - **Backoff:** after a failed fetch, no fetch is attempted for 30 seconds. Tokens that need one are refused at once in that window, and a single line is logged when the window opens, not one per request.
    - **After a success,** PyJWT's cache and cooldown apply as usual.
    - So during an outage, at most one outbound fetch every 30 seconds happens, and no request waits on another's fetch. The owner's client retries after a 401.
  - **The lower bound** in `pyproject.toml` is the first PyJWT release with `cooldown_duration`. The lock has 2.15.1; the implementation checks the changelog.
  - **The fetch blocks,** since PyJWT uses `urllib`. The verifier therefore runs the check in a worker thread (`anyio.to_thread.run_sync`), so it never blocks the event loop.
- **The wrapper, in `server.py`:** a `TokenVerifier` whose `verify_token` calls the check and returns an `AccessToken` with these fields:
  - `subject` = `sub`
  - `client_id` = the `client_id` claim, or `azp`, or `""` if neither is present
  - `scopes` = the `scope` claim split on spaces, or `[]`
  - `expires_at` = `exp`
  - `resource` = the public `/mcp` URL (the audience just checked)

  Which claims AuthKit actually sends is recorded in **As built**, from a real token, without its values.
- **Not chosen: a proxy for `/.well-known/oauth-authorization-server`.** WorkOS's guide suggests serving AuthKit's metadata from the MCP server too, for older clients that look for it there.
  - Claude and Claude Code follow the resource metadata to `authorization_servers`, as the MCP spec says.
  - A proxy would add an outbound call on an unauthenticated route.
  - It's added later only if a client the owner uses needs it.
- **Not chosen: token introspection.** AuthKit's introspection endpoint needs client credentials, and a JWT check needs no call per request. A token stays valid for its 5 minutes even if the user is removed in WorkOS. That's acceptable for one user.
- **Request log and WebSockets:** M4's middleware stays outermost in every mode. It logs one line per request (method, escaped path, status) and closes WebSockets. In OAuth mode it skips its own token check, because the SDK's middleware does the auth.

### D4. Owner only

- **The rule:** in OAuth mode, a token is accepted only if its `sub` equals `DATA_QUALITY_MCP_OAUTH_OWNER`, the owner's WorkOS user ID (`user_...`).
- **Why `sub`, not the email:**
  - WorkOS documents `sub` as the user ID. It doesn't say the access token carries an email.
  - `sub` is fixed for the account, while an email can change.
  - The owner finds the ID in the WorkOS dashboard, under Users, after signing up once.
- **Required:** OAuth mode doesn't start without it. There is no "any signed-in user" mode.
  - That mode would make the server public to anyone who signs up, so it's left out rather than left off by default.
  - For a server that several people use, a later spec could add an allowlist.
- **The owner ID isn't a secret.** It's kept out of the repo (`sync: false`) so a fork sets its own, not because it's sensitive. The startup log says the owner check is on, without the ID.
- **A non-owner sees a 401,** like any other refused token. The SDK's 403 is for missing scopes, and none are required.

### D5. Auth modes

M4's D2 said the static-token middleware would be dropped when OAuth came. It stays, because it still has uses OAuth doesn't cover:
- `make run-http`
- `docker run`
- the CI smoke test
- VS Code and the Inspector with a header

So `--transport http` has three modes:

| Mode | Chosen by | Who uses it |
|---|---|---|
| Static token (M4) | `DATA_QUALITY_MCP_TOKEN` set | Local, Docker, CI, Claude Code or VS Code with a header |
| OAuth (new) | `DATA_QUALITY_MCP_OAUTH_ISSUER` set | The live server: claude.ai and Claude Code with sign-in |
| None (M4) | `--no-auth`, loopback hosts only | Quick local experiments |

- **Exactly one:** the token and the OAuth issuer together are a startup error, and so is `--no-auth` with either. The server never picks one silently, as in M4.
- **The startup log** names the mode: `auth: bearer token`, `auth: oauth (issuer https://..., resource https://.../mcp, owner check on)`, or the M4 warning for `--no-auth`.
- **Stdio ignores all of them,** as it ignores the token today. One exception: `--no-auth` with stdio stays a usage error.
- **Not chosen: accepting both a static token and OAuth in one server.** Two ways in is twice the surface, and the live server has no use for the static one.

### D6. Configuration

- **New variables,** read in `http_auth.py` like the token. They aren't limits, so `config.py` doesn't read them, as CLAUDE.md's Configuration rule says.

  | Variable | Example | Rule |
  |---|---|---|
  | `DATA_QUALITY_MCP_OAUTH_ISSUER` | `https://<tenant>.authkit.app` | Chooses OAuth mode. `https`, with no path, query or fragment, and no trailing slash. It is compared exactly with the token's `iss`, so it's taken as written, after stripping whitespace. |
  | `DATA_QUALITY_MCP_PUBLIC_URL` | `https://<service>.onrender.com` | Required in OAuth mode. `https`, with no path, query or fragment; a trailing slash is dropped. The resource is this URL plus `/mcp`. |
  | `DATA_QUALITY_MCP_OAUTH_OWNER` | `user_01H...` | Required in OAuth mode. Non-empty, with no whitespace. |
  | `DATA_QUALITY_MCP_OAUTH_JWKS_URL` | `https://<tenant>.authkit.app/oauth2/jwks` | Optional; the default is the issuer plus `/oauth2/jwks`. `https`. |

- **Plain `http`** is accepted for the issuer, public URL and JWKS only when the host is `localhost`, `127.0.0.1` or `[::1]` (M4's loopback names), for a local OAuth test.
- **Empty means unset,** as for the token in M4: a blank or whitespace-only value counts as not set. So a blank issuer means OAuth mode is off.
- **A malformed or missing value:** in OAuth mode the server exits with status 2, naming the variable, as for the token.
- **Without the issuer,** the other three are ignored with a warning, so a half-removed OAuth setup doesn't pass silently.
- **Allowed hosts:**
  - **The default:** in OAuth mode, if `DATA_QUALITY_MCP_ALLOWED_HOSTS` is unset, it defaults to the public URL's **netloc**: the host, plus the port when the URL gives one. For `https://dq.onrender.com` that is `dq.onrender.com`; for `http://localhost:8000`, `localhost:8000`.
    - The SDK compares the `Host` header exactly, port included, so the port must be part of the entry.
    - With this default, the SDK's DNS-rebinding check (M4's D5) is on for the live server without another setting.
  - **An explicit value** replaces the default, as in M4.
  - **Routes outside the check:** `/health` and the metadata route aren't checked, so Render's health check keeps working whatever `Host` it uses.
  - **`Origin`:** an allowed-hosts list comes with an empty list of allowed origins (M4's `server.py`). So in OAuth mode a request to `/mcp` with an `Origin` header gets 403.
    - Claude Code and other non-browser clients don't send one.
    - It isn't known whether claude.ai's backend does. The live check finds out (Verification).
    - If claude.ai is refused for it, the fallback is a new optional `DATA_QUALITY_MCP_ALLOWED_ORIGINS`, set to `https://claude.ai` on Render. That is a small amendment recorded in **As built**, not an opening of the check.
    - Browser clients, such as the Inspector's web UI, stay refused. They aren't a use case for the live server.
- **`.env.example`** gains the four variables, commented out.

### D7. Fitting the free instance

- **The problem:** M4 measured, at the default limits, an idle server of 168 MiB and a worst case of 603 MiB (a shared-strings XLSX near the 200 MB expansion cap). That's over Render's 512 MB. And with 0.1 CPU, a call that took 21 seconds in M4 could take minutes.
- **The fix:** lower the D4 limits on Render only, through `render.yaml`. The code defaults stay the same for stdio and other deployments, and the tool descriptions already show the effective limits (M4's As built).
- **Proposed starting values,** to be confirmed by measurement:

  | Variable | Default | Proposed for Render |
  |---|---|---|
  | `DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB` | 16 | 8 |
  | `DATA_QUALITY_MCP_MAX_FILE_SIZE_MB` | 50 | 8 |
  | `DATA_QUALITY_MCP_MAX_XLSX_UNCOMPRESSED_MB` | 200 | 60 |
  | `DATA_QUALITY_MCP_MAX_ROWS` | 500,000 | 100,000 |
  | `DATA_QUALITY_MCP_MAX_INLINE_OUTPUT_MB` | 1 | 1 |

- **How they're checked:**
  - **Where:** the image runs locally with `docker run --memory=512m --memory-swap=512m --cpus=0.1`, close to Render's free instance.
  - **What:** each worst case allowed by those limits, one call per fresh container:
    - a CSV at the body cap or the row cap, whichever comes first
    - the largest numeric XLSX within all the caps. With the proposed values that's the row cap, not the body cap: 6 MB of XLSX (what 8 MB of base64 carries) unpacks to about 72 MB, which is over the 60 MB cap and over 100,000 rows.
    - a shared-strings XLSX at the expansion cap
    - `clean_dataset` on the largest CSV, with inline output
  - **The pass rule:** no call is killed for memory, and the peak (VmHWM, as in M4) stays at or under 400 MiB, which leaves headroom below 512.
  - **Time:** each call's time at 0.1 CPU is recorded. If the slowest takes over 60 seconds, the rows or the expansion cap come down until it doesn't, because a client can time a call out.
    - The numeric XLSX is likely to set the row cap: scaling M4's 21.4 s for 290,000 rows to 100,000 rows at a tenth of the CPU gives about 74 s.
    - So expect the proposed 100,000 rows to come down.
  - **Startup:** the time from `docker run` to healthy, under the same caps, is recorded too. At full CPU it was about 5 s. Importing pandas, scipy and the SDK at 0.1 CPU could take much longer. It adds to Render's own wake time (D8).
  - **Recorded:** the final values and figures, in **As built** and `docs/deployment.md`.
- **Live check:** the largest CSV case also runs once against Render, to confirm the local caps match the real instance.
  - **Not through claude.ai:** a model would have to write megabytes of `inline_content` itself, far past any output limit.
  - **A scripted client instead:** the SDK's Streamable HTTP client with its OAuth client provider, which signs in through a localhost callback. The script is a local tool for the owner, kept in `scripts/`, and holds no credentials. It is never run in CI.
- **Concurrency:** one owner means one call at a time in practice. The server still has no concurrency cap (M4). On the free instance, two large calls at once could exhaust the memory, and Render restarts the instance. The README says so.

### D8. Cold starts

- **A sleeping service takes about a minute to wake.** Claude gives discovery, registration and token calls 10 seconds each, so the first connection after a sleep may fail once. A retry a minute later works.
- **The docs say** to open `https://<service>.onrender.com/health` in a browser first, and to connect once it answers.
- **Not chosen: a keep-alive pinger** (a scheduled GitHub Action, or an outside uptime service, calling `/health` every 14 minutes).
  - It would keep a free instance awake on purpose, which is against the spirit of the free tier.
  - It would use nearly all 750 free hours.
  - It would add a workflow that runs whether or not anyone uses the server.

### D9. CI

- **In-process tests** (the normal matrix, no network):
  - **Tokens:** a test RSA key signs the tokens, and the verifier gets that key in place of the JWKS client.
  - **Through the HTTP app** with Starlette's `TestClient`:
    - **Accepted:** the owner's token.
    - **Refused:** wrong `sub`, wrong `aud`, wrong `iss`, expired, missing `exp`/`iat`/`sub`, `alg: none`, an HS256 token signed with the public key, an unknown `kid`, no `kid`, and a garbage token.
    - **Wrong claim types:** a list `scope`, a numeric `client_id` and a numeric `sub` each give 401, never 500.
    - **The 401** carries `resource_metadata` with the public URL.
    - **The metadata** document's exact contents.
    - **Open routes:** `/health` and the metadata route, with no header and with a garbage `Authorization: Bearer` header: 200, never 500.
    - **Host checks:**
      - the default allowed host taken from the public URL, with and without a port (`http://localhost:8000` must accept `Host: localhost:8000`)
      - a foreign `Host` rejected on `/mcp`
      - an `Origin` header rejected on `/mcp`
    - **The request log:** one line, in OAuth mode too, and never the token.
  - **The fetch gate (D3),** with a fake JWKS client that fails or blocks on demand:
    - **Repeated failures:** they make one fetch per 30-second window, with an empty cache and with an expired one.
    - **Concurrency:** a check arriving while a fetch is in flight is refused at once, without waiting.
    - **Prechecks:** a token that fails one never reaches the client.
    - **Recovery:** after a success, a token with a known `kid` needs no fetch.
  - **`main()`:**
    - **Mode conflicts:** the token with the issuer, and `--no-auth` with either.
    - **Missing or malformed values:** a missing public URL or owner, `http` on a public host, a path on the issuer, and the issuer with a trailing slash.
    - **Half-removed setups:** the leftover-variable warning.
    - **Startup:** no network call at startup.
  - **The JWKS client:** its timeout and its 30-second cooldown, through PyJWT's settings, with no real fetch.
  - **Stdio and the static-token tests** pass unchanged.
- **The `Docker image` job** gains a fourth container in OAuth mode:
  - **The address:** it publishes host port 8001, since `dq` already uses 8000.
  - **The settings:** the issuer is `https://issuer.invalid`, the public URL is `http://localhost:8001`, and the owner is a fixed test value.
  - **No network call:** the issuer is never fetched. The JWKS is lazy, and the made-up bearer token isn't JWT-shaped, so it fails before any lookup.
  - **The smoke script** gains an `--oauth` mode, which needs no `DATA_QUALITY_MCP_TOKEN` and skips the tool calls. It checks that:
    - `/health` is 200
    - `/mcp` without a token is 401, with `resource_metadata` naming `http://localhost:8001/.well-known/oauth-protected-resource/mcp`
    - the metadata route returns the expected JSON
    - a made-up bearer token is 401
    - the log has no token
- **Render deploys after CI** (`checksPass`), so a commit that fails CI is never deployed.
- **The ruleset's required checks don't change:** no job is added or renamed.

### D10. Docs

- **`docs/deployment.md` (new):**
  - **Render, tested:** the account steps (below), `render.yaml`, the D7 limits and figures, cold starts, the zero-cost rule, and what happens when a quota runs out.
  - **WorkOS:** the dashboard steps (D2), how to find the owner's user ID, and that the server doesn't depend on WorkOS's JWKS at startup.
  - **Cloud Run, written but untested:** the same image and variables. Its cost guards:
    - **Billing:** a billing account and a card are required. The Free Trial can't charge, but it ends after 90 days.
    - **Spend cap:** a spend-cap budget covering Cloud Run. It pauses the service until the next period, but doesn't cover Artifact Registry or logging, and is computed on gross costs.
    - **Instances:** `--max-instances=1` and request-based billing.
    - **Storage:** an image cleanup policy, or a free registry.

    It's marked clearly as not checked against a real deployment, with its open risks listed. It isn't a recommendation to use it.
- **README:**
  - **"Live deployment":** what's running, that it accepts only its owner, and how to deploy your own (link to `docs/deployment.md`).
  - **claude.ai:** Settings → Connectors → Add custom connector, with the URL `https://<service>.onrender.com/mcp`. Sign in when asked. Auth settings can't be edited after adding a connector, so a change means removing and re-adding it.
  - **Claude Code:** `claude mcp add --transport http data-quality https://<service>.onrender.com/mcp`, then `/mcp` to sign in.
  - **Whether to show the live URL:** the owner decides at implementation. Showing it is harmless, since every request needs the owner's sign-in, and it makes the portfolio checkable. Bots will find it either way.
  - The "Run over HTTP" section gains the OAuth mode (D5) and its variables (D6). Its line saying a custom connector needs OAuth, "which come with the later deployment", points to the new section instead.
- **`docs/architecture.md`:** the auth modes and the OAuth flow (client, server, AuthKit).
- **`planning/project-overview.md`:**
  - **The roadmap:** "Later: Live Deployment and OAuth" becomes Milestone 4.5. Its text drops "a published image" (D1).
  - **The stack table:**
    - the transport row's "behind a bearer token" becomes "behind a bearer token or OAuth"
    - a new hosting row (Render free, WorkOS AuthKit)
  - **The diagram's transport label:** the same change, bearer token or OAuth.
  - **The repository structure:**
    - the `http_auth.py` description gains the JWT check
    - new entries for `render.yaml`, `docs/deployment.md` and the scripted OAuth client (D7)
  - **The CI table's `build.yml` row:** gains the OAuth-mode container.

---

## Your steps (accounts)

You create and own every account. I never see a password, token or key. Labels in the dashboards may differ from these; I'll check them with you as we go. All of this happens during the implementation PR, after this spec is merged.

1. **WorkOS** (workos.com, free sign-up, no card; stop if one is asked):
   1. In the **Staging** environment, open **Connect → Configuration** and turn on **Client ID Metadata Document** and **Dynamic Client Registration**.
   2. Note the **AuthKit domain** (e.g. `https://<tenant>.authkit.app`). It's the issuer, and isn't secret.
2. **Render** (render.com, sign up with GitHub, no card; stop if one is asked):
   1. Give Render's GitHub app access to this repository only.
   2. Create the service from the Blueprint (`render.yaml`) on the PR's branch. Pick the service name: it sets the URL `https://<name>.onrender.com`.
   3. When asked for the `sync: false` values, enter the issuer, `https://<name>.onrender.com` as the public URL, and a placeholder owner such as `pending`. The service starts, and refuses every token until step 4.
3. **WorkOS, again:** add `https://<name>.onrender.com/mcp` as a **Resource Indicator**.
4. **Sign up once** through claude.ai's connector (README steps). The server refuses you, because the owner is still the placeholder.
   1. **Check the reason** in Render's log: it must read `token refused: not the owner`. That shows the issuer and the audience already match (D3's check order). A different reason, such as an audience error, means step 3's Resource Indicator doesn't match, so fix that first.
   2. **Copy your user ID** from WorkOS → Users, and set it as `DATA_QUALITY_MCP_OAUTH_OWNER` in Render. Render restarts the service.
5. **Connect again** from claude.ai, then from Claude Code. Both should now work.
6. **After the merge,** switch the Render service's branch to `main`.

## Schema changes

**None to the report or to any tool's output.** Every TypedDict in `profiler_core/schema.py` is unchanged. No tool argument changes. OAuth only changes who may call the tools, never what they do. Over HTTP, `file_path` stays off (M4's D3).

## Invariant changes (need explicit approval)

Merging this spec also approves these `CLAUDE.md` changes, made in the implementation PR:
- **Layer boundary:** "`http_auth.py` uses Starlette but not the SDK" becomes "`http_auth.py` uses Starlette and PyJWT but not the SDK". `jwt` joins the imports forbidden in the core, and `tests/test_architecture.py` enforces it.
- **stdio hygiene:** "Over HTTP, logs go to stderr too, and never include the token or request bodies" becomes "... and never include a token (static or OAuth), its claims, or request bodies".
- **Working conventions, a new rule:** "The live deployment stays on free tiers, with no payment method on any account it uses (milestone-4.5.md). A change that would need one is a decision for the owner, never part of a PR."

## Tasks

1. **`http_auth.py` (D3, D4, D5, D6):**
   - the OAuth settings reader and its validation
   - the JWT check with its prechecks, the owner rule and the never-raise rule
   - the JWKS client settings and the fetch gate
   - mode resolution, extended to three modes
   - the request-log middleware, with the static check skipped in OAuth mode
2. **`server.py` (D3, D5, D6):**
   - the `TokenVerifier` wrapper and the `AuthSettings`, passed to `MCPServer` for the HTTP app in OAuth mode
   - the allowed-hosts default
   - the startup log line
3. **`pyproject.toml`:** `pyjwt[crypto]` with its lower bound (D3). `uv lock`, then confirm only the root entry changed.
4. **Tests (D9):** the in-process OAuth tests, the `main()` cases, and the architecture test's `jwt` entry.
5. **CI (D9):** the OAuth-mode container in the `Docker image` job, and the smoke script's OAuth checks.
6. **Sizing (D7):**
   - the capped-container measurements, startup time included, then the limits in `render.yaml`
   - the scripted OAuth client in `scripts/`, for the live check
7. **`render.yaml` (D1).**
8. **Docs (D10):**
   - `docs/deployment.md`, the README, `docs/architecture.md` and `.env.example`
   - `CLAUDE.md` (Invariant changes)
   - the overview
9. **Version 0.6.0** in `pyproject.toml`, `__init__.py` and `uv.lock`.
10. **This spec:** the status line and an **As built** section.

## Verification

- **Commands:** `make format`, `make lint`, `make test`, plus the Python 3.10 / pandas 2.3.3 run in the scratch environment. Coverage stays at or above its current level.
- **Docker, locally:** the M4 smoke checks in static-token mode, the OAuth-mode container, and the D7 measurements under `--memory=512m --cpus=0.1`.
- **Live, on Render, during the PR** (after your steps 1 to 5):
  - `/health` answers, and the metadata at `/.well-known/oauth-protected-resource/mcp` has the right `resource` and `authorization_servers`.
  - **claude.ai** (custom connector): sign-in completes, the fixture's inline profile scores 77, as over stdio, and a `file_path` gets M4's D3 error.
  - **Claude Code** with OAuth: the same profile.
  - **Refusals:**
    - Before the owner is set (step 4), your own sign-in is refused with the log reason `not the owner`. That tests D4 against a real token, with the issuer and the audience already matching.
    - A request with no token, and one with a made-up token, get 401.
  - **`Origin`:** whether claude.ai's requests carry one, seen from whether the connector works with D6's allowed hosts. If it's refused for that, the D6 fallback applies.
  - **The logs** on Render show no token.
  - **A cold start:** connect after the service has slept, and record what happens.
  - **The largest D7 case,** through the scripted client (D7), not claude.ai.
  - **Recorded in As built:** which registration Claude used (CIMD or DCR), the token's claim names and algorithm (not their values), whether its `iss` matches the AuthKit domain exactly, and the times.
- **Stdio:** Claude Desktop, after `uv tool install --editable . --reinstall`, gives the same profile as before.
- **Pre-PR review** for the implementation PR, then the CI review under the stop rule.

## Out of scope

- **Cloud Run, actually deployed:** only the untested guide is in scope (D10).
- **Publishing the image** (GHCR or another registry, D1).
- **A custom domain,** for Render or AuthKit.
- **More than one user:** an allowlist of subjects, roles or scopes (D4).
- **Token introspection and revocation checks** (D3).
- **A proxy for the authorization server's metadata** (D3).
- **A keep-alive pinger** (D8).
- **Rate limiting and a concurrency cap:**
  - Unauthenticated requests get a 401 without the body being read.
  - A token that fails D3's prechecks costs an unverified decode and never reaches the JWKS.
  - Outbound fetches are gated to one at a time, with a 30-second backoff after a failure (D3).
  - The only caller who gets through is the owner. Revisit both if the server ever has more than one user.
- **M5** (polish and docs) keeps its scope.
