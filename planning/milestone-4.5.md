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
- **Same image everywhere:** the live server runs the image CI builds and tests. It is configured only through environment variables, so it can move to another host (Cloud Run, D10) without a code change.
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
  - **Refused:** another user's token, a wrong audience, a wrong issuer, an expired token and a token without `sub` each got 401. So did `bearer` in lower case followed by extra spaces, since the SDK strips exactly `Bearer ` (7 characters). Clients send one space, as RFC 6750 says.
- **The check, in `http_auth.py`** (no `mcp`):
  - **Decoding:** `jwt.decode` with:
    - `algorithms=["RS256"]`, fixed, so `none` or an HMAC algorithm are never accepted
    - `issuer` = the issuer
    - `audience` = the public `/mcp` URL
    - `options={"require": ["exp", "iat", "iss", "aud", "sub"]}`
    - a 30-second leeway for clock skew
  - **The owner:** `sub` must equal the owner (D4).
  - **On a refusal:** it returns `None`, and logs one line naming the reason class, e.g. `token refused: InvalidAudienceError` or `token refused: not the owner`, never the token or its claims.
  - WorkOS's guide doesn't name the signing algorithm. The implementation confirms RS256 from a real token's header and from the JWKS. If it differs, the fixed list follows the JWKS, never the token.
- **The JWKS:** `jwt.PyJWKClient(<issuer>/oauth2/jwks)`, the URL WorkOS documents, or `DATA_QUALITY_MCP_OAUTH_JWKS_URL` if set.
  - **Fetched lazily,** on the first token, never at startup. A sleeping or unreachable AuthKit then can't stop the server from starting or passing its health check.
  - **Cached:** the key set is kept for 5 minutes (PyJWT's default), with a 5-second fetch timeout.
  - **Refetches are throttled:** a token with an unknown `kid` makes PyJWT refetch the set, at most once per 30 seconds (`cooldown_duration`). Random `kid`s therefore can't turn requests into outbound calls to WorkOS.
  - **The lower bound** in `pyproject.toml` is the first PyJWT release with `cooldown_duration`. The lock has 2.15.1; the implementation checks the changelog.
  - **The fetch blocks,** since PyJWT uses `urllib`. The verifier therefore runs the check in a worker thread (`anyio.to_thread.run_sync`), so it never blocks the event loop.
  - **A fetch that fails** refuses the token (401) and logs the failure. The client retries.
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

- **Plain `http`** is accepted for the issuer, public URL and JWKS only when the host is `localhost` or `127.0.0.1`, for a local OAuth test.
- **A malformed or missing value:** in OAuth mode the server exits with status 2, naming the variable, as for the token.
- **Without the issuer,** the other three are ignored with a warning, so a half-removed OAuth setup doesn't pass silently.
- **Allowed hosts:**
  - In OAuth mode, if `DATA_QUALITY_MCP_ALLOWED_HOSTS` is unset, it defaults to the public URL's host. The SDK's DNS-rebinding check (M4's D5) is then on for the live server without another setting.
  - Setting the variable explicitly replaces the default, as in M4.
  - `/health` and the metadata route are outside that check, so Render's health check, which may use another `Host`, keeps working.
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
    - a numeric XLSX at the body cap
    - a shared-strings XLSX at the expansion cap
    - `clean_dataset` on the largest CSV, with inline output
  - **The pass rule:** no call is killed for memory, and the peak (VmHWM, as in M4) stays at or under 400 MiB, which leaves headroom below 512.
  - **Time:** each call's time at 0.1 CPU is recorded. If the slowest takes over 60 seconds, the rows or the expansion cap come down until it doesn't, because a client can time a call out.
  - **Recorded:** the final values and figures, in **As built** and `docs/deployment.md`.
- **Live check:** the largest CSV case also runs once through claude.ai against Render, to confirm the local cap matches the real instance.
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
    - **Refused:** wrong `sub`, wrong `aud`, wrong `iss`, expired, missing `exp`/`iat`/`sub`, `alg: none`, an HS256 token signed with the public key, an unknown `kid`, and a garbage token.
    - **The 401** carries `resource_metadata` with the public URL.
    - **The metadata** document's exact contents.
    - **Open routes:** `/health` and the metadata route.
    - **Host checks:** the default allowed host from the public URL, and a foreign `Host` rejected on `/mcp`.
    - **The request log:** one line, in OAuth mode too, and never the token.
  - **`main()`:**
    - **Mode conflicts:** the token with the issuer, and `--no-auth` with either.
    - **Missing or malformed values:** a missing public URL or owner, `http` on a public host, a path on the issuer, and the issuer with a trailing slash.
    - **Half-removed setups:** the leftover-variable warning.
    - **Startup:** no network call at startup.
  - **The JWKS client:** its timeout and its 30-second cooldown, through PyJWT's settings, with no real fetch.
  - **Stdio and the static-token tests** pass unchanged.
- **The `Docker image` job** gains a fourth container in OAuth mode. Its issuer is `https://issuer.invalid`, which is never fetched, because the JWKS is lazy. The smoke script checks that:
  - `/health` is 200
  - `/mcp` without a token is 401, with `resource_metadata`
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
  - The "Run over HTTP" section gains the OAuth mode (D5) and its variables (D6).
- **`docs/architecture.md`:** the auth modes and the OAuth flow (client, server, AuthKit).
- **`planning/project-overview.md`:**
  - the roadmap: "Later: Live Deployment and OAuth" becomes Milestone 4.5
  - the stack table's auth and hosting rows
  - the repository structure: `render.yaml`, `docs/deployment.md`
  - the auth paragraph that says OAuth comes later

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
4. **Sign up once** through claude.ai's connector (README steps). The server refuses you, because the owner is still the placeholder. Then copy your **user ID** from WorkOS → Users, and set it as `DATA_QUALITY_MCP_OAUTH_OWNER` in Render. Render restarts the service.
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
   - the JWT check with the owner rule, and the JWKS client settings
   - mode resolution, extended to three modes
   - the request-log middleware, with the static check skipped in OAuth mode
2. **`server.py` (D3, D5, D6):**
   - the `TokenVerifier` wrapper and the `AuthSettings`, passed to `MCPServer` for the HTTP app in OAuth mode
   - the allowed-hosts default
   - the startup log line
3. **`pyproject.toml`:** `pyjwt[crypto]` with its lower bound (D3). `uv lock`, then confirm only the root entry changed.
4. **Tests (D9):** the in-process OAuth tests, the `main()` cases, and the architecture test's `jwt` entry.
5. **CI (D9):** the OAuth-mode container in the `Docker image` job, and the smoke script's OAuth checks.
6. **Sizing (D7):** the capped-container measurements, then the limits in `render.yaml`.
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
  - **Refusals:** before the owner is set (step 4), your own sign-in is refused, which tests D4 against a real token. A request with no token, and one with a made-up token, get 401.
  - **The logs** on Render show no token.
  - **A cold start:** connect after the service has slept, and record what happens.
  - **The largest D7 case** through claude.ai.
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
  - Unauthenticated requests get a 401 without the body being read, and their cost is a JWT decode at most. JWKS refetches are throttled (D3).
  - The only caller who gets through is the owner. Revisit both if the server ever has more than one user.
- **M5** (polish and docs) keeps its scope.
