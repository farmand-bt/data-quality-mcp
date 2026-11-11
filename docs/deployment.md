# Deploying the server

The live server ([planning/milestone-4.5.md](../planning/milestone-4.5.md)) runs on a free [Render](https://render.com) web service, with sign-in through [WorkOS AuthKit](https://workos.com/docs/authkit/mcp). It accepts only its owner. This guide sets up your own copy.

> **Zero cost.** Both services are used on their free tiers, and **no payment method is ever added to either account.** On Render that matters: past a free quota (instance hours, bandwidth, build minutes), a workspace without a payment method has its free services suspended, or its builds stopped, until the next month. With a card on file, Render bills instead. If a step below ever asks for a card, stop there.

## How it fits together

- **Render** builds this repo's `Dockerfile` (described in [`render.yaml`](../render.yaml)), terminates TLS on `https://<name>.onrender.com`, and deploys a commit only after all its GitHub checks pass.
- **AuthKit** is the authorization server. Clients (claude.ai, Claude Code) register themselves with it (Client ID Metadata Documents or Dynamic Client Registration), the user signs in, and AuthKit issues a short-lived JWT for the server's `/mcp` URL.
- **The server** is the resource server. It points clients to AuthKit (`/.well-known/oauth-protected-resource/mcp`), checks each token's signature against AuthKit's keys, its issuer, audience and expiry, and accepts it only if its `sub` is the owner's user ID. Anyone can create an AuthKit account; only the owner gets past the server.

Nothing on Render is secret: there is no static token in OAuth mode, and the issuer, the public URL and the owner ID aren't credentials.

## Setup

Dashboard labels change from time to time; look for the nearest match.

1. **WorkOS** (workos.com, free sign-up):
   1. Stay in the **Staging** environment. WorkOS bills only production environments.
   2. Under **Connect → Configuration**, turn on **Client ID Metadata Document** and **Dynamic Client Registration**.
   3. Note the **AuthKit domain**, e.g. `https://<tenant>.authkit.app`. It's the issuer.
2. **Render** (render.com, sign up with GitHub):
   1. Give Render's GitHub app access to your fork only.
   2. **New → Blueprint**, pick the repo and the branch. Render reads `render.yaml` and creates one free web service. Its name sets the URL, `https://<name>.onrender.com`.
   3. Render asks for the three `sync: false` values:
      - `DATA_QUALITY_MCP_OAUTH_ISSUER`: the AuthKit domain, exactly, with no trailing slash
      - `DATA_QUALITY_MCP_PUBLIC_URL`: `https://<name>.onrender.com`
      - `DATA_QUALITY_MCP_OAUTH_OWNER`: a placeholder such as `pending` for now
3. **WorkOS, again:** add `https://<name>.onrender.com/mcp` as a **Resource Indicator**. AuthKit then issues tokens whose audience is that URL; without it, the server refuses every token.
4. **Sign in once** from claude.ai (below). The server refuses you, because the owner is still the placeholder.
   1. In Render's log, the refusal must read `token refused: not the owner`. That reason means the issuer and the audience already matched. Another reason, such as `wrong audience`, means step 3 doesn't match the URL.
   2. Copy your user ID (`user_...`) from WorkOS → **Users**, and set it as `DATA_QUALITY_MCP_OAUTH_OWNER` in Render (the service's **Environment** page). Render restarts the service.
5. **Connect again.** It now works.

Render applies `sync: false` values only when the Blueprint is first created. Later changes to them go through the dashboard.

## Connecting

- **claude.ai:** Settings → Connectors → Add custom connector, URL `https://<name>.onrender.com/mcp`, then sign in. A connector's auth settings can't be edited afterwards: remove it and add it again.
- **Claude Code:** `claude mcp add --transport http data-quality https://<name>.onrender.com/mcp`, then `/mcp` to sign in.
- **A script:** [`scripts/oauth_client.py`](../scripts/oauth_client.py) signs in through your browser and profiles a generated CSV, to try large inputs that a model can't type: `uv run python scripts/oauth_client.py https://<name>.onrender.com/mcp --rows 50000`. It keeps tokens in memory only and never prints them.

## Cold starts

A free service sleeps after 15 minutes without traffic, and takes about a minute to wake: Render's own start, plus about 30 seconds for the server to import its libraries on a tenth of a CPU. Claude gives the discovery, registration and token calls 10 seconds each, so **the first connection after a sleep can fail**. Open `https://<name>.onrender.com/health` in a browser first, and connect once it answers `{"status":"ok"}`.

There is no keep-alive pinger. One that never let the service sleep would use up to 744 of the 750 free hours a month, and a second free service in the same workspace would then run out of hours.

## Limits on the free instance

The free instance has 512 MB of memory and 0.1 CPU. `render.yaml` lowers five limits for it; the code defaults stay as they are for stdio and other hosts, and the tool descriptions show whichever limits are in effect.

| Variable | Default | On Render |
|---|---|---|
| `DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB` | 16 | 8 |
| `DATA_QUALITY_MCP_MAX_FILE_SIZE_MB` | 50 | 8 |
| `DATA_QUALITY_MCP_MAX_XLSX_UNCOMPRESSED_MB` | 200 | 35 |
| `DATA_QUALITY_MCP_MAX_ROWS` | 500,000 | 100,000 |
| `DATA_QUALITY_MCP_MAX_INLINE_OUTPUT_MB` | 1 | 1 |

**How they were chosen:** the image ran locally with `docker run --memory=512m --memory-swap=512m --cpus=0.1`, close to the free instance, one call per fresh container, for each worst case those limits allow. The rule was a peak (VmHWM) of at most 400 MiB, leaving headroom below 512, and at most 60 seconds per call, since a client can time a call out.

| Input | Body | Time | Peak |
|---|---|---|---|
| Idle server | - | - | 173 MiB |
| CSV, 99,999 rows × 8 columns (5.1 MB) | 5.0 MiB | 7.0 s | 240 MiB |
| `clean_dataset` on that CSV, inline output | 5.0 MiB | 9.0 s | 240 MiB |
| XLSX, 70,699 rows × 14 small integers (4.2 MB, unpacks to 33.9 MB) | 5.3 MiB | 51.7 s | 250 MiB |
| XLSX, 35,349 rows × 28 small integers (4.1 MB, unpacks to 33.1 MB) | 5.3 MiB | 45.8 s | 244 MiB |
| XLSX, 18,551 distinct 1.8 KB strings (0.26 MB, unpacks to 34.8 MB) | 0.3 MiB | 20.4 s | 252 MiB |

- **The XLSX expansion cap sets the slowest case.** At 0.1 CPU, reading a numeric XLSX takes about 50 seconds per million cells, whatever the sheet's shape. With the first proposal of 60 MB, a numeric XLSX within the 8 MB body took 72.6 seconds (99,999 rows × 14 columns, 5.9 MB, unpacking to 48 MB, 280 MiB peak), so the cap came down to 35 MB, about a million numeric cells. The row cap alone couldn't bound this, since a wider sheet has as many cells in fewer rows.
- **Memory isn't the constraint at these limits:** the largest peak, a shared-strings XLSX at the old 60 MB cap, was 309 MiB.
- **A CSV** reaches the row cap long before the body cap: 100,000 rows of 8 columns is about 5 MB.
- **`clean_dataset` with inline output** returns at most 1 MB, so a cleaned CSV that large is refused with a tool error after the work is done (the 9 seconds above); write smaller results, or profile instead.
- **Startup**, from `docker run` to a healthy `/health`, took 24 to 33 seconds at 0.1 CPU (about 5 at full CPU), mostly importing pandas, SciPy and the SDK. On Render that comes on top of its own wake time.
- **One call at a time:** each call needs its own memory, and the server has no concurrency cap. Two large calls at once can exhaust the instance, and Render then restarts it.

## Security notes

- **Owner only:** the server checks `sub` against `DATA_QUALITY_MCP_OAUTH_OWNER` on every request. There is no "any signed-in user" mode. If WorkOS offers a way to limit sign-ups, turning it on adds a second guard, but the server doesn't depend on it.
- **Tokens:** RS256 only, with a fixed algorithm list, so `none` or an HMAC algorithm is never accepted. `exp`, `iat`, `iss`, `aud` and `sub` are required. Access tokens last 5 minutes; a token stays valid for that long even if the user is removed in WorkOS, since the server doesn't call WorkOS per request.
- **AuthKit's keys** are fetched on the first token, never at startup, and cached for 5 minutes. Fetches go one at a time, at most one every 30 seconds.
  - **Stale if error:** if a refresh fails, the server keeps using the last key set it fetched, so a short AuthKit outage doesn't sign the owner out.
  - **The trade-off:** while AuthKit is unreachable, a token signed with a key AuthKit has since rotated out or revoked is still accepted, until the next successful fetch. For one owner and 5-minute tokens, that's accepted.
- **Hosts:** in OAuth mode the server accepts only the public URL's host on `/mcp` (DNS-rebinding protection), and a request to `/mcp` with an `Origin` header gets 403. `/health` and the metadata route are open.
- **Logs:** one line per request (method, path, status), and one line per refused token naming the reason class. Never a token, its claims or a request body. The owner ID isn't logged either.

## Google Cloud Run (untested)

> **Not tested against a real deployment.** This section is a starting point, not a recommendation. Unlike Render's free tier, Cloud Run needs a billing account with a card, so it doesn't meet the zero-cost rule without the guards below, and even then some charges aren't capped.

The same image and variables work. The image listens on port 8000, so deploy it with `--port 8000` (Cloud Run's default container port is 8080).

**Cost guards, and their open risks:**
- **Billing:** a billing account and a card are required. The Free Trial's credit can't be charged past its end, but the trial ends after 90 days, and the account then needs an explicit upgrade to keep running.
- **Spend cap:** a budget with spend-cap enforcement that covers Cloud Run pauses the service until the next period. It doesn't cover Artifact Registry storage or logging, and it's computed on gross costs, before credits.
- **Instances:** `--max-instances=1`, `--min-instances=0`, and request-based billing, so an idle service costs nothing.
- **Storage:** an Artifact Registry cleanup policy that keeps only the latest image, or an image from a free registry.
- **Memory:** 512 MiB with the limits above, or 1 GiB with the defaults.

```bash
gcloud run deploy data-quality-mcp \
  --image <registry>/data-quality-mcp:<tag> --port 8000 \
  --max-instances 1 --min-instances 0 --memory 512Mi --cpu 1 \
  --set-env-vars DATA_QUALITY_MCP_OAUTH_ISSUER=https://<tenant>.authkit.app,DATA_QUALITY_MCP_PUBLIC_URL=https://<service-url>,DATA_QUALITY_MCP_OAUTH_OWNER=user_... \
  --allow-unauthenticated
```

`--allow-unauthenticated` lets requests reach the container; the server's OAuth check still requires the owner's sign-in. Add the Cloud Run URL plus `/mcp` as a Resource Indicator in WorkOS.
