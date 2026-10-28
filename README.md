# Carrier Middleware

Carrier Middleware is a FastAPI prototype for carrier authority checks, OTP verification, load search, rate negotiation, and booking. It is the system of record: one process serves two protocols — REST for the production voice workflow (the six business endpoints) and MCP (Model Context Protocol) for agents (streamable HTTP at `/mcp`). The `agent/` directory contains a local, text-based LangChain agent that drives the middleware through its MCP tools and saves a call summary to SQLite.

## Project layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | HTTP adapter layer: Pydantic request models, API-key auth, and response dicts; mounts the MCP surface at `/mcp` and runs its session manager in the app lifespan |
| `app/core/` | Framework-agnostic business logic: FMCSA verification, OTP lifecycle, TMS TCP client, load search and formatting, negotiation engine, booking guards, and the shared in-memory state stores |
| `app/mcp_server.py` | MCP surface: the six business operations as MCP tools (official Python MCP SDK, FastMCP), streamable HTTP at `/mcp`, a stdio alternate, and the ASGI API-key middleware |
| `agent/inbound_carrier_sales.py` | Local conversational LangChain agent (`create_agent`): loads the middleware's six tools via the standard MCP adapter, adds two local call-control tools, and writes the post-call summary to SQLite |
| `agent/call_log.db` | Generated local call-summary database; ignored by Git |
| `tests/` | HTTP tests for verification, negotiation, and booking, plus the in-memory MCP suite |
| `.env.example` | Configuration template without live credentials |

## Architecture

One process, two protocols. Carrier Middleware is the trusted system of record for carrier verification, load search, rate negotiation, and booking, and it serves that record over two protocols in the same process:

- **REST for the production voice workflow.** The six business endpoints (`/verify`, `/send-code`, `/check-code`, `/search-loads`, `/evaluate-offer`, `/book`) keep their exact paths, request models, response shapes, reason codes, and API-key authentication, so the production consumer keeps working with zero changes.
- **MCP for agents.** The same six business operations are also exposed as MCP tools — `verify_carrier`, `send_verification_code`, `check_verification_code`, `search_loads`, `evaluate_offer`, `book_load` — by a server built on the official Python MCP SDK (FastMCP), mounted on the ASGI application at `/mcp` and served over the streamable-HTTP transport, so any MCP-compatible client (an IDE agent, a desktop assistant) can list and call the tools.

The two protocols coexist so one system of record can be driven by both a fixed production contract and any modern agent client, without forking state: all in-memory state (OTP store, negotiation state, bookings) lives in the core layer and is shared by both protocols, and the MCP surface enforces the same shared API key as the HTTP surface, so mounting a second protocol cannot create an unauthenticated path to booking.

The shared core keeps the two protocols from drifting. Business logic exists in exactly one place: `app/core/` holds FMCSA verification, the OTP lifecycle, the TMS TCP client, load search and formatting, the round-limited negotiation engine (the negotiation ceilings never leave the server), and the booking guards. `app/main.py` and `app/mcp_server.py` are thin adapters over that one implementation — the HTTP handlers and the MCP tools contain no business rules — so the REST and MCP surfaces cannot diverge. The guards that must hold no matter which protocol a caller uses (verified OTP required, only the server-accepted rate bookable, round limits enforced server-side) are enforced in the core and therefore apply to both.

### Pointing an MCP client at the server

Any MCP-compatible client can connect to the mounted streamable-HTTP server:

- **URL:** `CARRIER_API_BASE` + `/mcp` — for example, `http://127.0.0.1:8000/mcp` locally, or the ngrok HTTPS address plus `/mcp` behind a tunnel.
- **Transport:** streamable HTTP.
- **Authentication:** the shared API key, sent as the `x-api-key` header or an `Authorization` header (raw key, `ApiKey <key>`, or `Bearer <key>` — the same accepted forms as the HTTP surface).

Each tool also accepts an optional `run_id` string alongside its parameters; the core keys its session-scoped state (OTP, negotiation) on it.

For local experimentation, the same server is also launchable over stdio without affecting the mounted server:

```sh
python -m app.mcp_server
```

### The local agent

`agent/inbound_carrier_sales.py` is a LangChain agent built on the canonical agent API (`create_agent`): a chat model constructed provider-agnostically (`init_chat_model`) from `AGENT_MODEL`, the verbatim bilingual (EN/ES) system prompt, and a tool list. The six middleware tools are loaded from the MCP server with the standard LangChain–MCP adapter (`load_mcp_tools`) over streamable HTTP at `CARRIER_API_BASE` + `/mcp` — the agent talks to the system of record exclusively through MCP tool calls, with no raw HTTP and no hand-rolled tool schemas — plus two local call-control tools (`hang_up`, `transfer_to_rep`) that stay agent-side, so ending or escalating a call never depends on a network round trip. The agent generates a per-call run id and injects it into every MCP tool call (the model never sees it), so session-scoped state behaves identically to the old HTTP agent. One process handles one call: a single MCP session is opened for the life of the call and closed cleanly on exit. After the call, the transcript is extracted into a typed `CallSummary` with LangChain structured output and written to the existing SQLite call log with its unchanged schema.

### Deployment

Deployment is unchanged: the same image and the same port. The Dockerfile is untouched; the MCP endpoint simply appears at `/mcp` on the existing host, behind the same API key.

## Requirements

- Python 3.10 or newer
- An OpenAI API key to run the local agent (the agent's models run on OpenAI through the `langchain-openai` provider integration)
- FMCSA and TMS credentials for live carrier and load operations

`requirements.txt` carries two groups of dependencies. Pre-existing dependencies (FastAPI, uvicorn, Pydantic, requests, openai, pytest, httpx) remain unpinned, matching the repository style. The AI stack — the official MCP SDK (`mcp`), LangChain core (`langchain`), the OpenAI provider integration (`langchain-openai`), and the LangChain–MCP adapter (`langchain-mcp-adapters`) — is exact-pinned for reproducibility, so the environment resolves the same versions weeks from now.

## Install and run locally

From the repository root, create a virtual environment and install dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

On a fresh checkout, create the local environment file and set a long, unique middleware key. If `.env` already exists, edit it instead of overwriting it. Use the same key for `MIDDLEWARE_API_KEY` and `CARRIER_API_KEY`; the agent sends the latter when calling the API and when connecting to the middleware's MCP server.

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Paste the generated value into both variables in `.env`. Add `FMCSA_API_KEY`, `TMS_HOST`, `TMS_PORT`, and `TMS_AUTH` when live integrations are configured. The service reads environment variables directly and does not load `.env` automatically. Export the file's values and start the API:

```sh
set -a
. ./.env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check that it is running in another terminal:

```sh
curl http://127.0.0.1:8000/health
```

## Expose the API with ngrok

For a temporary public endpoint, leave the middleware running locally and start an ngrok HTTP tunnel in a second terminal:

```sh
ngrok http 8000
```

Copy the HTTPS forwarding address printed by ngrok. Set it as the agent's base URL before starting the agent:

```sh
export CARRIER_API_BASE="https://your-ngrok-domain.ngrok-free.app"
sh ./agent/setup.sh
```

Replace the example address with the one ngrok assigned to your tunnel. Keep both the middleware and tunnel running while the remote client uses it; a temporary ngrok address can change when the tunnel restarts. Treat the URL as public: protected routes (the API endpoints and the MCP endpoint at `/mcp`) still require the configured `CARRIER_API_KEY`, but health checks and FastAPI's documentation pages are public. Keep test carriers, test loads, and `/tms-raw` disabled, and never put API keys in the URL. An ngrok tunnel is useful for development and demos; it does not provide persistent application hosting.

To run the local agent, set `OPENAI_API_KEY` in `.env`, keep the API server running, then start the agent from the repository root:

```sh
sh ./agent/setup.sh
```

The agent requires `OPENAI_API_KEY` and `CARRIER_API_KEY`. `CARRIER_API_BASE` defaults to `http://127.0.0.1:8000`; the agent connects to the middleware's MCP server at `CARRIER_API_BASE` + `/mcp` (streamable HTTP) using `CARRIER_API_KEY` as the `x-api-key` header. Use `/quit` to end a local call. Call summaries go to `agent/call_log.db` by default; the database can contain carrier identifiers and notes, so keep it private.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MIDDLEWARE_API_KEY` | API key accepted by the middleware's protected endpoints and by the MCP endpoint | Random process-local key if unset; configure a stable key for clients |
| `CARRIER_API_KEY` | Key sent by the local agent over REST and as the MCP credential; set it to the same value as `MIDDLEWARE_API_KEY` | Empty; agent setup requires it |
| `FMCSA_API_KEY` | FMCSA carrier lookup credential | Empty; live verification is unavailable without it |
| `TMS_HOST`, `TMS_PORT`, `TMS_AUTH` | TMS TCP server connection settings | `localhost`, `17159`, empty |
| `TMS_TIMEOUT` | TMS connection timeout in seconds | `10` |
| `ENABLE_TEST_CARRIERS` | Enables fixed carrier fixtures and mock OTP flow | `false` |
| `ENABLE_TEST_LOADS` | Enables fixed test loads | `false` |
| `ENABLE_TMS_RAW` | Enables the diagnostic `/tms-raw` endpoint | `false` |
| `CARRIER_API_BASE` | Middleware base URL used by the local agent; the MCP endpoint is `CARRIER_API_BASE` + `/mcp` | `http://127.0.0.1:8000` |
| `OPENAI_API_KEY` | OpenAI credential for the local agent's models (via the `langchain-openai` provider integration) | Empty |
| `CALL_LOG_DB` | SQLite call-summary path | `agent/call_log.db` |

There are no new environment variables for the MCP surface: `CARRIER_API_KEY` doubles as the MCP credential (the agent's MCP connection sends it as the `x-api-key` header; any external MCP client may present it as `x-api-key` or an `Authorization` header in the same accepted forms), and `CARRIER_API_BASE` is the middleware's base URL, so the MCP endpoint is `CARRIER_API_BASE` + `/mcp`.

Do not commit `.env`. It is excluded by `.gitignore` and `.dockerignore`; `.env.example` contains placeholders only. If the middleware key is unset, the app generates a random key at startup, which external clients cannot know. Configure the same stable key for both services.

## API overview

`GET /` and `GET /health` are health endpoints. The following POST endpoints require an API key:

| Endpoint | Purpose |
| --- | --- |
| `/verify` | Check carrier operating authority |
| `/send-code` | Start or resend OTP verification |
| `/check-code` | Verify an OTP |
| `/search-loads` | Search available loads |
| `/evaluate-offer` | Evaluate a carrier rate offer |
| `/book` | Validate an accepted offer and submit a booking |
| `/tms-raw` | Development diagnostic route, disabled unless `ENABLE_TMS_RAW=true` |

The same six business operations are also exposed as MCP tools at the MCP endpoint (see [Architecture](#architecture)). Health, root, and the dev-only `/tms-raw` passthrough stay outside the MCP surface.

## Local fixtures

Set `ENABLE_TEST_CARRIERS=true` and/or `ENABLE_TEST_LOADS=true` in `.env` for local development. Test fixtures are disabled by default and should remain disabled outside development. The test carrier `9999901` follows the eligible path and accepts the fixed OTP `424242`; `9999900` is ineligible and `9999902` forces a booking failure.

SMS delivery is not implemented. The endpoint uses mock responses and does not send text messages. For real carriers, the current FMCSA flow has no phone-number source, so it cannot complete OTP verification. Live booking also depends on a compatible TMS TCP service and valid credentials.

## Docker

Build the image and pass a private environment file at runtime:

```sh
docker build -t carrier-middleware .
docker run --env-file .env -p 8000:8080 carrier-middleware
```

The image does not contain `.env` or local SQLite databases. Configure a stable API key and disable all test flags before deploying. The `/tms-raw` diagnostic route should remain disabled and must not be exposed publicly.

## Current limitations

- OTP, negotiation, and booking state is stored in process memory and is lost on restart.
- Negotiation and booking state is keyed by load ID, so simultaneous calls working on the same load can share state.
- Multiple workers do not share state. Use one worker for this prototype; move state to a shared store and scope it to each call before handling concurrent production traffic.
- SMS delivery and phone-number lookup are mocks, not live integrations.
- The TMS client uses a custom TCP protocol and requires network access to the configured TMS host.
- Call transcripts and extracted summaries are sent to OpenAI; summaries are stored locally in SQLite.

## Possible future implementations

1. **Production verification:** Add a real SMS provider and an approved phone-number source, then track delivery, resend, expiry, and provider failures without logging OTP values.
2. **Concurrent state and booking safety:** Move OTP, negotiation, and booking state to Redis or a relational database. Scope state by call and carrier, add transaction boundaries and booking idempotency keys, and reconcile ambiguous TMS responses.
3. **TMS integration hardening:** Define and validate command and response schemas, add TLS or a private authenticated transport, and isolate the diagnostic route from production builds.
4. **Authentication and secrets:** Replace the shared API key with scoped service credentials, rotate keys through a secrets manager, add request rate limits, and audit access without recording credentials or carrier PII.
5. **Operations:** Add structured redacted logs, metrics, tracing, readiness checks for FMCSA/TMS dependencies, and deployment health checks.
6. **Agent and call data:** Add retention controls and encryption for call summaries, validate extracted summary fields, and provide a review/export workflow for authorized staff.
7. **Quality gates:** Add CI for the existing tests, API contract tests for external integrations, and deterministic tests for concurrent negotiations, retries, and partial booking failures.

## Tests

Run the test suites (HTTP and MCP) from the repository root:

```sh
python -m pytest tests/ -v
```

The HTTP suites pin the production contract and the in-memory MCP suite pins tool identity, auth enforcement, and the booking guards over the MCP surface. Together they act as the acceptance gate that the two protocol adapters cannot drift from the shared core.
