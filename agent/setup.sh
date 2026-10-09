#!/usr/bin/env sh
set -eu

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY before starting the agent}"
: "${CARRIER_API_KEY:?Set CARRIER_API_KEY to the middleware API key}"

export CARRIER_API_BASE="${CARRIER_API_BASE:-http://127.0.0.1:8000}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/inbound_carrier_sales.py" "$@"
