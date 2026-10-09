# Carrier Middleware

Carrier Middleware is a FastAPI prototype for carrier authority checks, OTP verification, load search, rate negotiation, and booking. The `agent/` directory contains a local, text-based OpenAI agent that calls the middleware and saves a call summary to SQLite.

## Project layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI endpoints, FMCSA lookup, TMS TCP client, OTP and negotiation logic |
| `agent/inbound_carrier_sales.py` | Local conversational agent, middleware client, and call-summary writer |
| `agent/call_log.db` | Generated local call-summary database; ignored by Git |
| `tests/` | API tests for verification, negotiation, and booking |
| `.env.example` | Configuration template without live credentials |

## Requirements

- Python 3.10 or newer
- An OpenAI API key to run the local agent
- FMCSA and TMS credentials for live carrier and load operations

## Install and run locally

From the repository root, create a virtual environment and install dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

On a fresh checkout, create the local environment file and set a long, unique middleware key. If `.env` already exists, edit it instead of overwriting it. Use the same key for `MIDDLEWARE_API_KEY` and `CARRIER_API_KEY`; the agent sends the latter when calling the API.

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

To run the local agent, set `OPENAI_API_KEY` in `.env`, keep the API server running, then start the agent from the repository root:

```sh
sh ./agent/setup.sh
```

The agent requires `OPENAI_API_KEY` and `CARRIER_API_KEY`. `CARRIER_API_BASE` defaults to `http://127.0.0.1:8000`. Use `/quit` to end a local call. Call summaries go to `agent/call_log.db` by default; the database can contain carrier identifiers and notes, so keep it private.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MIDDLEWARE_API_KEY` | API key accepted by protected middleware endpoints | Random process-local key if unset; configure a stable key for clients |
| `CARRIER_API_KEY` | Key sent by the local agent; set it to the same value as `MIDDLEWARE_API_KEY` | Empty; agent setup requires it |
| `FMCSA_API_KEY` | FMCSA carrier lookup credential | Empty; live verification is unavailable without it |
| `TMS_HOST`, `TMS_PORT`, `TMS_AUTH` | TMS TCP server connection settings | `localhost`, `17159`, empty |
| `TMS_TIMEOUT` | TMS connection timeout in seconds | `10` |
| `ENABLE_TEST_CARRIERS` | Enables fixed carrier fixtures and mock OTP flow | `false` |
| `ENABLE_TEST_LOADS` | Enables fixed test loads | `false` |
| `ENABLE_TMS_RAW` | Enables the diagnostic `/tms-raw` endpoint | `false` |
| `CARRIER_API_BASE` | Middleware URL used by the local agent | `http://127.0.0.1:8000` |
| `OPENAI_API_KEY` | OpenAI credential used by the local agent | Empty |
| `CALL_LOG_DB` | SQLite call-summary path | `agent/call_log.db` |

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

Run the API test suite from the repository root:

```sh
python -m pytest tests/ -v
```
