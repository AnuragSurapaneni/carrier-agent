"""
Configuration and fixture data, shared by every protocol.

Environment flags are read at import time, as before. The mutable
fixtures (test carriers, test loads, negotiation limits) are the single
live objects every adapter reads.
"""

import json
import os
import secrets
from typing import Optional

# ============================================================
# API KEY
# ============================================================

# Shared by every protocol surface (HTTP today; MCP later).
API_KEY = os.getenv("MIDDLEWARE_API_KEY", "").strip() or secrets.token_urlsafe(32)

# ============================================================
# CONFIGURATION
# ============================================================

FMCSA_API_KEY = os.getenv("FMCSA_API_KEY", "")

TMS_HOST = os.getenv("TMS_HOST", "localhost")
TMS_PORT = int(os.getenv("TMS_PORT", "17159"))
TMS_AUTH = os.getenv("TMS_AUTH", "")
TMS_TIMEOUT = float(os.getenv("TMS_TIMEOUT", "10"))

# Test carrier fixtures are opt-in. Enable them only for local development.
ENABLE_TEST_CARRIERS = (
    os.getenv("ENABLE_TEST_CARRIERS", "false").strip().lower() == "true"
)

# Set ENABLE_TEST_LOADS=true to serve the fixed test loads below
# for the adversarial tests. Keep it false in production.
ENABLE_TEST_LOADS = (
    os.getenv("ENABLE_TEST_LOADS", "false").strip().lower() == "true"
)

ENABLE_TMS_RAW = os.getenv("ENABLE_TMS_RAW", "false").strip().lower() == "true"

OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 3
# Verified sessions are kept this long so booking can check them.
OTP_SESSION_RETENTION_HOURS = 2

# ============================================================
# DEMO / TEST CARRIERS
# ============================================================

# Demo carrier used by the PoC. Random OTP code.
DEMO_CARRIER_MC = "123456"

DEMO_CARRIER = {
    "carrier_name": "Demo Carrier",
    "has_phone_on_file": True,
    "phone": "+15551231234",
    "email": "demo@example.com",
}

NEGOTIATION_LIMITS = {
    "LD00859": 5500,
    "LD00880": 3500,
    "LD00881": 2200,
}

# Test carriers used by the production voice workflow's adversarial tests.
# These skip FMCSA and use a fixed OTP code so tests are repeatable.
#
# Fields:
#   carrier_name       - returned by /verify
#   eligible           - True / False
#   has_phone_on_file  - True / False
#   reason             - returned by /verify
#   masked_destination - returned by /send-code
#   fixed_code         - OTP code accepted by /check-code
#   force_book_failure - optional; /book always returns booked=false
TEST_CARRIERS = {
    # Ineligible carrier - ends the call at the FMCSA check.
    "9999900": {
        "carrier_name": "Test Carrier Ineligible",
        "eligible": False,
        "has_phone_on_file": False,
        "reason": "not_authorized",
        "masked_destination": None,
        "fixed_code": None,
    },
    # Eligible carrier - full happy path.
    "9999901": {
        "carrier_name": "Test Carrier Pass",
        "eligible": True,
        "has_phone_on_file": True,
        "reason": "verified",
        "masked_destination": "***-***-1234",
        "fixed_code": "424242",
    },
    # Eligible carrier whose booking always fails.
    "9999902": {
        "carrier_name": "Test Carrier Book Fail",
        "eligible": True,
        "has_phone_on_file": True,
        "reason": "verified",
        "masked_destination": "***-***-1234",
        "fixed_code": "424242",
        "force_book_failure": True,
    },
}

# Optional: extra test carriers from an env var, as a JSON object
# in the same shape as TEST_CARRIERS.
_extra = os.getenv("TEST_CARRIERS_JSON", "").strip()
if _extra:
    try:
        TEST_CARRIERS.update(json.loads(_extra))
    except ValueError:
        print("[CONFIG] TEST_CARRIERS_JSON is not valid JSON; ignored")


def get_test_carrier(mc_number: str) -> Optional[dict]:
    if not ENABLE_TEST_CARRIERS:
        return None
    return TEST_CARRIERS.get(mc_number)


# ============================================================
# TEST LOADS (only served when ENABLE_TEST_LOADS=true)
# ============================================================
#
# One open load per adversarial-test lane.

TEST_LOADS = {
    "TEST-CHI-REEFER": {
        "orig_city": "Chicago", "orig_state": "IL",
        "dest_city": "Atlanta", "dest_state": "GA",
        "eqtype": "REEFER", "weight": 38000, "commodity": "Frozen food",
        "pieces": 20, "miles": 716, "dims": "", "notes": "Keep at minus ten F",
        "rate": 2400,
    },
    "TEST-HOU-FLATBED": {
        "orig_city": "Houston", "orig_state": "TX",
        "dest_city": "Denver", "dest_state": "CO",
        "eqtype": "FLATBED", "weight": 44000, "commodity": "Steel coils",
        "pieces": 6, "miles": 1030, "dims": "", "notes": "Tarps required",
        "rate": 2200,
    },
    "TEST-LRD-DRYVAN": {
        "orig_city": "Laredo", "orig_state": "TX",
        "dest_city": "Dallas", "dest_state": "TX",
        "eqtype": "DRY_VAN", "weight": 32000, "commodity": "Auto parts",
        "pieces": 24, "miles": 430, "dims": "", "notes": "",
        "rate": 1100,
    },
    "TEST-ATL-DRYVAN": {
        "orig_city": "Atlanta", "orig_state": "GA",
        "dest_city": "Charlotte", "dest_state": "NC",
        "eqtype": "DRY_VAN", "weight": 28000, "commodity": "Paper goods",
        "pieces": 18, "miles": 245, "dims": "", "notes": "",
        "rate": 950,
    },
    "TEST-DAL-DRYVAN": {
        "orig_city": "Dallas", "orig_state": "TX",
        "dest_city": "Memphis", "dest_state": "TN",
        "eqtype": "DRY_VAN", "weight": 30000, "commodity": "Consumer goods",
        "pieces": 22, "miles": 452, "dims": "", "notes": "",
        "rate": 1300,
    },
}
