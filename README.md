# Carrier Middleware

FastAPI service for carrier verification, load search, rate negotiation, and booking. The `agent/` directory contains a local, text-based OpenAI voice-agent workflow that calls this service.

## Requirements

- Python 3.10 or newer
- An OpenAI API key to run the local agent
- FMCSA and TMS credentials to use live carrier and load data

## Configure and run the middleware

Create and activate a virtual environment, then install the dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set a unique `MIDDLEWARE_API_KEY`. Set `FMCSA_API_KEY` and `TMS_HOST` / `TMS_PORT` / `TMS_AUTH` when connecting to live services. The application reads environment variables directly; it does not load `.env` by itself. For local development, export the values before starting the server:

```sh
set -a
. ./.env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The health checks are `GET /` and `GET /health`. The authenticated workflow endpoints are `POST /verify`, `/send-code`, `/check-code`, `/search-loads`, `/evaluate-offer`, and `/book`. The diagnostic `POST /tms-raw` route is disabled by default; enable it with `ENABLE_TMS_RAW=true` only for local development and do not expose it publicly.

## Local test data

Set `ENABLE_TEST_CARRIERS=true` to enable fixed carrier fixtures. Set `ENABLE_TEST_LOADS=true` to enable the fixed test loads. Both features are intended for development and should be disabled in production. The SMS sender is a mock: it does not deliver messages. The test carrier code is fixed for repeatable local use; real SMS delivery is not implemented.

The middleware keeps OTP, negotiation, and booking state in process memory. Negotiation and booking state is keyed by load ID, so concurrent calls working on the same load can share state. Use a single worker; state is lost on restart and is not shared between workers. Move this state to a shared store and scope it to each call before handling concurrent production calls.

## Run the local agent

Set `OPENAI_API_KEY`, `CARRIER_API_KEY` (the same value as `MIDDLEWARE_API_KEY`), and optionally `CARRIER_API_BASE` (defaults to `http://127.0.0.1:8000`). Then run:

```sh
sh ./agent/setup.sh
```

Call summaries are written to `agent/call_log.db` by default. The database may contain carrier identifiers and call notes; keep it private and do not commit it.

## Docker

Build and run the service with a private environment file:

```sh
docker build -t carrier-middleware .
docker run --env-file .env -p 8000:8080 carrier-middleware
```

## Tests

```sh
python -m pytest tests/ -v
```
