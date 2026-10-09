import hmac
import json
import os
import random
import secrets
import socket
from datetime import datetime, timedelta
from typing import Optional, Union

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import requests

# ============================================================
# CONFIGURATION
# ============================================================

app = FastAPI(
    title="HappyRobot Carrier Middleware",
    version="1.2.0",
)

FMCSA_API_KEY = os.getenv("FMCSA_API_KEY", "")
API_KEY = os.getenv("MIDDLEWARE_API_KEY", "").strip() or secrets.token_urlsafe(32)

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
# NEGOTIATION STATE
# ============================================================

NEGOTIATION_STATE = {}

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

# Test carriers used by the HappyRobot adversarial tests.
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
# One open load per adversarial-test lane. "max_buy" is the hidden
# ceiling used by /evaluate-offer and is NEVER returned to the agent.

TEST_LOADS = {
    "TEST-CHI-REEFER": {
        "orig_city": "Chicago", "orig_state": "IL",
        "dest_city": "Atlanta", "dest_state": "GA",
        "eqtype": "REEFER", "weight": 38000, "commodity": "Frozen food",
        "pieces": 20, "miles": 716, "dims": "", "notes": "Keep at minus ten F",
        "rate": 2400, "max_buy": 2700,
    },
    "TEST-HOU-FLATBED": {
        "orig_city": "Houston", "orig_state": "TX",
        "dest_city": "Denver", "dest_state": "CO",
        "eqtype": "FLATBED", "weight": 44000, "commodity": "Steel coils",
        "pieces": 6, "miles": 1030, "dims": "", "notes": "Tarps required",
        "rate": 2200, "max_buy": 2500,
    },
    "TEST-LRD-DRYVAN": {
        "orig_city": "Laredo", "orig_state": "TX",
        "dest_city": "Dallas", "dest_state": "TX",
        "eqtype": "DRY_VAN", "weight": 32000, "commodity": "Auto parts",
        "pieces": 24, "miles": 430, "dims": "", "notes": "",
        "rate": 1100, "max_buy": 1400,
    },
    "TEST-ATL-DRYVAN": {
        "orig_city": "Atlanta", "orig_state": "GA",
        "dest_city": "Charlotte", "dest_state": "NC",
        "eqtype": "DRY_VAN", "weight": 28000, "commodity": "Paper goods",
        "pieces": 18, "miles": 245, "dims": "", "notes": "",
        "rate": 950, "max_buy": 1150,
    },
    "TEST-DAL-DRYVAN": {
        "orig_city": "Dallas", "orig_state": "TX",
        "dest_city": "Memphis", "dest_state": "TN",
        "eqtype": "DRY_VAN", "weight": 30000, "commodity": "Consumer goods",
        "pieces": 22, "miles": 452, "dims": "", "notes": "",
        "rate": 1300, "max_buy": 1550,
    },
}


def get_test_load(load_id: str) -> Optional[dict]:
    if not ENABLE_TEST_LOADS:
        return None
    return TEST_LOADS.get(load_id)


# ============================================================
# STATE
# ============================================================

# OTP state, keyed by "<mc_number>:<run_id>" so concurrent calls
# for the same MC never share a verification session:
# {
#   "code": "123456",
#   "expires_at": datetime,
#   "created_at": datetime,
#   "attempts": 0,
#   "verified": False,
#   "resend_used": False,
# }
OTP_STORE = {}

# Mock booking state.
BOOKINGS = {}

def otp_key(run_id, mc_number) -> str:
    """Code sessions are per call (run_id) and per MC."""
    return f"{clean(run_id)}:{clean(mc_number)}"


def prune_otp_store():
    cutoff = datetime.utcnow() - timedelta(hours=OTP_SESSION_RETENTION_HOURS)
    for key in [k for k, v in OTP_STORE.items() if v.get("created_at", cutoff) < cutoff]:
        OTP_STORE.pop(key, None)


# ============================================================
# REQUEST MODELS
# ============================================================

class VerifyRequest(BaseModel):
    run_id: Optional[str] = None
    mc_number: Optional[str] = None


class SendCodeRequest(BaseModel):
    run_id: Optional[str] = None
    mc_number: Optional[str] = None
    # The agent may send "true"/"false" as strings.
    is_resend: Optional[Union[bool, str]] = False


class CheckCodeRequest(BaseModel):
    run_id: Optional[str] = None
    mc_number: Optional[str] = None
    code: Optional[str] = None


class SearchLoadsRequest(BaseModel):
    run_id: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    equipment_type: Optional[str] = None
    pickup_date: Optional[str] = None
    exclude_load_ids: Optional[str] = None


class EvaluateOfferRequest(BaseModel):
    run_id: Optional[str] = None
    load_id: Optional[str] = None
    carrier_rate: Optional[str] = None
    round: Optional[str] = None


class BookRequest(BaseModel):
    run_id: Optional[str] = None
    load_id: Optional[str] = None
    mc_number: Optional[str] = None
    # The HappyRobot Book node sends "agreed_rate".
    # "carrier_rate" is accepted too for backward compatibility.
    agreed_rate: Optional[str] = None
    carrier_rate: Optional[str] = None


# ============================================================
# AUTHENTICATION
# ============================================================

def api_key_matches(value: str) -> bool:
    return hmac.compare_digest(value.encode("utf-8"), API_KEY.encode("utf-8"))


def authenticate(authorization: Optional[str], x_api_key: Optional[str]):
    """
    Accept any of:
        x-api-key: <configured key>
        Authorization: <configured key>
        Authorization: ApiKey <configured key>
        Authorization: Bearer <configured key>
    """
    valid = False

    if x_api_key and api_key_matches(x_api_key):
        valid = True

    if authorization:
        auth = authorization.strip()
        if api_key_matches(auth):
            valid = True
        lowered = auth.lower()
        if lowered.startswith("apikey ") and api_key_matches(auth[7:].strip()):
            valid = True
        if lowered.startswith("bearer ") and api_key_matches(auth[7:].strip()):
            valid = True

    if not valid:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return clean(value).lower() in {"true", "1", "yes"}


def normalize_equipment(value: Optional[str]) -> str:
    if not value:
        return ""
    return (
        str(value).strip().lower()
        .replace("-", "").replace("_", "").replace(" ", "")
    )


def equipment_to_tms(value: Optional[str]) -> str:
    """
    Convert HappyRobot/user wording into TMS identifiers.
        dry van -> DRY_VAN
        reefer  -> REEFER
        flatbed -> FLATBED
    """
    if not value:
        return ""

    normalized = (
        str(value).strip().upper()
        .replace("-", "_").replace(" ", "_")
    )

    aliases = {
        "DRYVAN": "DRY_VAN",
        "DRY_VAN": "DRY_VAN",
        "VAN": "DRY_VAN",
        "REEFER": "REEFER",
        "REFRIGERATED": "REEFER",
        "FLATBED": "FLATBED",
        "FLAT_BED": "FLATBED",
    }

    return aliases.get(normalized, normalized)


US_STATES = {
    # English
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "nueva york": "NY",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",

    # Spanish
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawai": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "luisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "míchigan": "mi",
    "minnesota": "mn",
    "misisipi": "ms",
    "misuri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "nuevo hampshire": "nh",
    "nueva jersey": "nj",
    "nuevo méxico": "nm",
    "carolina del norte": "nc",
    "dakota del norte": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pensilvania": "pa",
    "rhode island": "ri",
    "carolina del sur": "sc",
    "dakota del sur": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "virginia occidental": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}


def split_city_state(value: Optional[str]) -> tuple[str, str]:
    """'Chicago, Illinois' -> ('Chicago', 'IL'). State is '' if not given."""
    value = clean(value)
    if not value:
        return "", ""
    if "," not in value:
        return value, ""
    city, state = value.rsplit(",", 1)
    state = state.strip()
    abbrev = state.upper() if len(state) == 2 else US_STATES.get(state.lower(), "")
    return city.strip(), abbrev


def to_int(value) -> Optional[int]:
    try:
        return int(float(clean(value)))
    except (ValueError, TypeError):
        return None


def format_tms_datetime(value: str) -> str:
    """20250101083000 -> 2025-01-01 08:30. Other formats pass through."""
    if len(value) == 14:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return value


# ============================================================
# TMS TCP CLIENT
# ============================================================

def parse_tms_record(line: str) -> dict:
    """
    LOAD_ID:LD0001|ORIG_CITY:Atlanta|...
      -> {"load_id": "LD0001", "orig_city": "Atlanta", ...}
    """
    record = {}
    for part in line.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        record[key.strip().lower()] = value.strip()
    return record


def tms_request(command: str, fields: Optional[dict] = None) -> list[dict]:
    fields = fields or {}

    if not TMS_AUTH:
        raise RuntimeError("TMS_AUTH environment variable is not configured")

    # CMD must be first.
    parts = [f"CMD:{command}", f"AUTH:{TMS_AUTH}"]

    for key, value in fields.items():
        if value is None:
            continue
        # Protocol does not permit these characters.
        value = str(value).replace("|", "").replace("\r", "").replace("\n", "")
        parts.append(f"{key}:{value}")

    message = "|".join(parts) + "\r\n"

    # Avoid logging request fields, which may contain carrier or rate data.
    print("[TMS REQUEST]", command)

    response_lines = []

    try:
        with socket.create_connection((TMS_HOST, TMS_PORT), timeout=TMS_TIMEOUT) as sock:
            sock.sendall(message.encode("ascii", errors="ignore"))

            buffer = b""
            done = False

            while not done:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk

                while b"\r\n" in buffer:
                    raw_line, buffer = buffer.split(b"\r\n", 1)
                    line = raw_line.decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    response_lines.append(line)
                    if line == "END":
                        done = True
                        break

    except socket.timeout:
        raise RuntimeError("TMS connection timed out")
    except OSError as exc:
        raise RuntimeError(f"TMS connection failed: {exc}")

    # LOAD_QUERY can legitimately return nothing.
    if not response_lines:
        if command == "LOAD_QUERY":
            print("[TMS RESPONSE] No matching loads")
            return []
        raise RuntimeError("TMS returned an empty response")

    if response_lines[0].startswith("ERR|"):
        error = parse_tms_record(response_lines[0])
        raise RuntimeError(
            f"TMS {error.get('code', 'UNKNOWN')}: {error.get('msg', 'TMS error')}"
        )

    records = []
    for line in response_lines:
        if line == "END":
            break
        records.append(parse_tms_record(line))

    return records


def tms_get_load_raw(load_id: str) -> Optional[dict]:
    """Full LOAD_GET record, including internal fields. Internal use only."""
    load_id = clean(load_id)
    if not load_id:
        return None
    try:
        records = tms_request("LOAD_GET", {"LOAD_ID": load_id})
    except Exception as exc:
        print(f"[TMS LOAD_GET ERROR] {exc}")
        return None
    return records[0] if records else None


def tms_get_load(load_id: str) -> Optional[dict]:
    """
    Load record safe to build agent-facing output from.
    MAX_BUY is removed and is NEVER returned to the agent.
    """
    record = tms_get_load_raw(load_id)
    if record is None:
        return None
    record = dict(record)
    record.pop("max_buy", None)
    return record


def tms_get_max_buy(load_id: str) -> Optional[float]:
    """Internal only. Never return this value to the agent."""
    record = tms_get_load_raw(load_id)
    if record is None:
        return None
    try:
        return float(record.get("max_buy", ""))
    except (ValueError, TypeError):
        return None


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def root():
    return {"service": "HappyRobot Carrier Middleware", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
# VERIFY CARRIER
# ============================================================

@app.post("/verify")
def verify_carrier(
    request: VerifyRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)

    mc_number = clean(request.mc_number)

    if not mc_number:
        return {
            "eligible": False,
            "carrier_name": None,
            "has_phone_on_file": False,
            "reason": "missing_mc",
        }

    test_carrier = get_test_carrier(mc_number)
    if test_carrier:
        eligible = bool(test_carrier.get("eligible"))
        result = {
            "eligible": eligible,
            "carrier_name": test_carrier.get("carrier_name"),
            "has_phone_on_file": bool(test_carrier.get("has_phone_on_file")),
            "reason": test_carrier.get(
                "reason", "verified" if eligible else "not_authorized"
            ),
        }
        print("[TEST VERIFY]", mc_number, result)
        return result

    if ENABLE_TEST_CARRIERS and mc_number == DEMO_CARRIER_MC:
        result = {
            "eligible": True,
            "carrier_name": DEMO_CARRIER["carrier_name"],
            "has_phone_on_file": DEMO_CARRIER["has_phone_on_file"],
            "reason": "verified",
        }
        print("[DEMO VERIFY]", mc_number, result)
        return result

    result = verify_fmcsa_carrier(mc_number)
    print("[FMCSA VERIFY]", mc_number, result)
    return result


def verify_fmcsa_carrier(mc_number: str) -> dict:
    mc_number = clean(mc_number)

    def fail(reason: str, name=None):
        return {
            "eligible": False,
            "carrier_name": name,
            "has_phone_on_file": False,
            "reason": reason,
        }

    if not mc_number:
        return fail("missing_mc")

    if not FMCSA_API_KEY:
        return fail("fmcsa_not_configured")

    url = (
        "https://mobile.fmcsa.dot.gov"
        f"/qc/services/carriers/docket-number/{mc_number}"
    )

    try:
        response = requests.get(url, params={"webKey": FMCSA_API_KEY}, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        # Request exceptions can include the URL, which carries the API key.
        print(f"[FMCSA ERROR] {type(exc).__name__}")
        return fail("fmcsa_error")
    except ValueError:
        return fail("fmcsa_invalid_response")

    content = data.get("content") or []
    if not content:
        return fail("carrier_not_found")

    carrier = content[0].get("carrier", {}) or {}
    carrier_name = carrier.get("legalName")

    eligible = (
        carrier.get("allowedToOperate") == "Y"
        and carrier.get("oosDate") is None
    )

    # Real carriers have no phone source in this PoC, so no OTP
    # can be sent. The agent will end the call at verification.
    return {
        "eligible": eligible,
        "carrier_name": carrier_name,
        "has_phone_on_file": False,
        "reason": "verified" if eligible else "not_authorized",
    }


# ============================================================
# SEND OTP
# ============================================================

@app.post("/send-code")
def send_code(
    request: SendCodeRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)
    prune_otp_store()

    mc_number = clean(request.mc_number)
    run_id = clean(request.run_id)
    is_resend = to_bool(request.is_resend)

    # Never send an SMS when MC is missing.
    if not mc_number:
        return {
            "sent": False,
            "resend_allowed": False,
            "masked_destination": None,
            "reason": "missing_mc",
        }
    if not run_id:
        return {
            "sent": False,
            "resend_allowed": False,
            "masked_destination": None,
            "reason": "missing_run_id",
        }

    test_carrier = get_test_carrier(mc_number)

    # Work out who can receive a code.
    if test_carrier:
        if not test_carrier.get("eligible"):
            return {
                "sent": False,
                "resend_allowed": False,
                "masked_destination": None,
                "reason": "carrier_not_verified",
            }
        if not test_carrier.get("has_phone_on_file"):
            return {
                "sent": False,
                "resend_allowed": False,
                "masked_destination": None,
                "reason": "no_phone_on_file",
            }
        masked = test_carrier.get("masked_destination") or "***-***-0000"
        fixed_code = clean(test_carrier.get("fixed_code")) or None

    elif ENABLE_TEST_CARRIERS and mc_number == DEMO_CARRIER_MC:
        masked = "***-***-" + DEMO_CARRIER["phone"][-4:]
        fixed_code = None

    else:
        return {
            "sent": False,
            "resend_allowed": False,
            "masked_destination": None,
            "reason": "carrier_not_verified",
        }

    key = otp_key(mc_number, run_id)

    # Resend rules: one resend per call.
    existing = OTP_STORE.get(otp_key(run_id, mc_number))
    resend_used = False

    if is_resend:
        if not existing:
            return {
                "sent": False,
                "resend_allowed": False,
                "masked_destination": masked,
                "reason": "no_previous_code",
            }
        if existing.get("resend_used", False):
            return {
                "sent": False,
                "resend_allowed": False,
                "masked_destination": masked,
                "reason": "resend_not_allowed",
            }
        resend_used = True

    code = fixed_code or str(random.randint(100000, 999999))
    now = datetime.utcnow()

    OTP_STORE[otp_key(run_id, mc_number)] = {
        "code": code,
        "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
        "created_at": now,
        "attempts": 0,
        "verified": False,
        "resend_used": resend_used,
    }

    # SMS delivery is not implemented. Never write one-time codes to logs.
    print("[MOCK SMS] Delivery is disabled")

    return {
        "sent": True,
        "resend_allowed": not resend_used,
        "masked_destination": masked,
    }


# ============================================================
# CHECK OTP
# ============================================================

@app.post("/check-code")
def check_code(
    request: CheckCodeRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)

    mc_number = clean(request.mc_number)
    run_id = clean(request.run_id)
    code = "".join(ch for ch in clean(request.code) if ch.isdigit())

    if not mc_number or not run_id or not code:
        return {"verified": False, "status": "invalid",
                "attempts_remaining": 0, "reason": "missing_input"}

    otp = OTP_STORE.get(otp_key(run_id, mc_number))

    if not otp or datetime.utcnow() > otp["expires_at"]:
        return {"verified": False, "status": "expired", "attempts_remaining": 0}

    if otp["attempts"] >= OTP_MAX_ATTEMPTS:
        return {"verified": False, "status": "locked", "attempts_remaining": 0}

    if code != otp["code"]:
        otp["attempts"] += 1
        remaining = max(0, OTP_MAX_ATTEMPTS - otp["attempts"])
        if remaining == 0:
            return {"verified": False, "status": "locked", "attempts_remaining": 0}
        return {"verified": False, "status": "invalid", "attempts_remaining": remaining}

    otp["verified"] = True
    return {"verified": True, "status": "verified",
            "attempts_remaining": OTP_MAX_ATTEMPTS - otp["attempts"]}


# ============================================================
# SEARCH LOADS
# ============================================================

def format_load(load_id: str, orig_city, orig_state, dest_city, dest_state,
                pickup, delivery, equipment, weight, commodity, pieces,
                miles, dims, rate, notes) -> dict:
    """Agent-facing load. MAX_BUY is NEVER included."""
    lane = f"{orig_city}, {orig_state} → {dest_city}, {dest_state}"
    if delivery:
        dates = f"Pickup: {pickup}; Delivery: {delivery}"
    else:
        dates = f"Pickup: {pickup}"
    return {
        "load_id": load_id,
        "lane": lane,
        "dates": dates,
        "equipment": equipment,
        "weight": to_int(weight),
        "commodity": commodity,
        "num_of_pieces": to_int(pieces),
        "miles": to_int(miles),
        "dimensions": dims,
        "posted_rate": to_int(rate),
        "notes": notes,
    }


def find_test_loads(orig_city, orig_state, dest_city, dest_state,
                    equipment, excluded) -> list[dict]:
    if not ENABLE_TEST_LOADS:
        return []

    eq = equipment_to_tms(equipment) if equipment else ""
    pickup = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d 08:00")
    delivery = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d 17:00")

    results = []
    for load_id, load in TEST_LOADS.items():
        if load_id in excluded:
            continue
        if orig_city and load["orig_city"].lower() != orig_city.lower():
            continue
        if orig_state and load["orig_state"] != orig_state:
            continue
        if dest_city and load["dest_city"].lower() != dest_city.lower():
            continue
        if dest_state and load["dest_state"] != dest_state:
            continue
        if eq and load["eqtype"] != eq:
            continue
        results.append(
            format_load(
                load_id, load["orig_city"], load["orig_state"],
                load["dest_city"], load["dest_state"], pickup, delivery,
                load["eqtype"], load["weight"], load["commodity"],
                load["pieces"], load["miles"], load["dims"],
                load["rate"], load["notes"],
            )
        )
    return results


@app.post("/search-loads")
def search_loads(
    request: SearchLoadsRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)

    origin = clean(request.origin)
    destination = clean(request.destination)
    equipment = clean(request.equipment_type)
    pickup_date = clean(request.pickup_date)

    # TMS requires at least one filter.
    if not origin and not destination and not equipment and not pickup_date:
        return {"loads": [], "reason": "missing_search_criteria"}

    query_orig_city, orig_state_filter = split_city_state(origin)
    query_dest_city, dest_state_filter = split_city_state(destination)

    excluded = {
        item.strip()
        for item in clean(request.exclude_load_ids).split(",")
        if item.strip()
    }

    # Test loads first, so adversarial tests are deterministic.
    test_matches = find_test_loads(
        query_orig_city, orig_state_filter,
        query_dest_city, dest_state_filter,
        equipment, excluded,
    )
    if test_matches:
        print("[TEST SEARCH]", origin, equipment, [l["load_id"] for l in test_matches])
        return {"loads": test_matches}

    # --------------------------------------------------------
    # Build LOAD_QUERY
    #
    # The TMS stores city and state separately, so only the
    # city goes to the TMS. State is filtered below.
    # --------------------------------------------------------

    fields = {}

    if query_orig_city:
        fields["ORIG_CITY"] = query_orig_city

    if query_dest_city:
        fields["DEST_CITY"] = query_dest_city

    if equipment:
        fields["EQTYPE"] = equipment_to_tms(equipment)

    if pickup_date:
        fields["PICKUP_DT"] = pickup_date

    fields["MAX_RESULTS"] = "5"

    try:
        records = tms_request("LOAD_QUERY", fields)
    except Exception as exc:
        print(f"[TMS SEARCH ERROR] {exc}")
        return {"loads": [], "reason": "tms_error"}

    loads = []

    for query_record in records:
        load_id = clean(query_record.get("load_id"))

        if not load_id or load_id in excluded:
            continue

        status = clean(query_record.get("status")).upper()
        if status and status != "OPEN":
            continue

        # Full record, with MAX_BUY already removed.
        full_record = tms_get_load(load_id)
        if full_record is None:
            # Don't expose a partial record to the agent.
            continue

        def field(name: str, fallback_to_query: bool = True) -> str:
            default = query_record.get(name) if fallback_to_query else ""
            return clean(full_record.get(name, default))

        orig_city = field("orig_city")
        orig_state = field("orig_state")
        dest_city = field("dest_city")
        dest_state = field("dest_state")

        # State filter: skip loads from the wrong state
        # (e.g. Chicago Heights, IN when the caller said Illinois).
        if orig_state_filter and orig_state and orig_state.upper() != orig_state_filter:
            continue

        if dest_state_filter and dest_state and dest_state.upper() != dest_state_filter:
            continue

        loads.append(
            format_load(
                load_id, orig_city, orig_state, dest_city, dest_state,
                format_tms_datetime(field("pickup_dt")),
                format_tms_datetime(field("delivery_dt", False)),
                field("eqtype"), field("weight", False),
                field("commodity", False), field("pieces", False),
                field("miles"), field("dims", False), field("rate"),
                field("notes", False),
            )
        )

    return {"loads": loads}


# ============================================================
# EVALUATE OFFER
# ============================================================

DEMO_LOAD_CEILINGS = {
    "1001": 2500,
    "LD0000045821": 2500,
}


def get_max_rate(load_id: str) -> Optional[float]:
    """Hidden ceiling. Internal only; NEVER returned to the agent."""
    test_load = get_test_load(load_id)
    if test_load:
        return float(test_load["max_buy"])
    if load_id in DEMO_LOAD_CEILINGS:
        return float(DEMO_LOAD_CEILINGS[load_id])
    return tms_get_max_buy(load_id)


# ============================================================
# EVALUATE OFFER
# ============================================================

@app.post("/evaluate-offer")
def evaluate_offer(
    request: EvaluateOfferRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)

    load_id = clean(request.load_id)
    carrier_rate_raw = (
        clean(request.carrier_rate)
        .replace("$", "")
        .replace(",", "")
    )

    if not load_id or not carrier_rate_raw:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "missing_input",
        }

    try:
        carrier_rate = float(carrier_rate_raw)
    except ValueError:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "invalid_rate",
        }

    if carrier_rate <= 0:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "invalid_rate",
        }

    # ---------------------------------------------------------
    # Load must have a configured negotiation ceiling
    # ---------------------------------------------------------
    max_rate = NEGOTIATION_LIMITS.get(load_id)

    if max_rate is None:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "unknown_load",
        }

    # ---------------------------------------------------------
    # Get/create server-side negotiation state
    # ---------------------------------------------------------
    state = NEGOTIATION_STATE.setdefault(
        load_id,
        {
            "round": 0,
            "accepted": False,
            "agreed_rate": None,
        },
    )

    # ---------------------------------------------------------
    # Every carrier offer advances the negotiation round.
    #
    # Do NOT trust request.round for enforcement.
    # The middleware owns the round count.
    # ---------------------------------------------------------
    if state["round"] >= 3:
        state["accepted"] = False
        state["agreed_rate"] = None

        return {
            "decision": "reject",
            "next_rate": None,
            "reason": "negotiation_limit_reached",
            "round": state["round"],
        }

    state["round"] += 1

    # New carrier offer invalidates any previous acceptance.
    state["accepted"] = False
    state["agreed_rate"] = None

    negotiation_round = state["round"]

    print(
        f"[NEGOTIATION] "
        f"load={load_id} "
        f"round={negotiation_round} "
        f"carrier_rate={carrier_rate} "
        f"max_rate={max_rate}"
    )

    # ---------------------------------------------------------
    # Carrier offer is acceptable
    # ---------------------------------------------------------
    if carrier_rate <= max_rate:
        state["accepted"] = True
        state["agreed_rate"] = int(carrier_rate)

        return {
            "decision": "accept",
            "next_rate": int(carrier_rate),
            "round": negotiation_round,
        }

    # ---------------------------------------------------------
    # Carrier offer is above internal ceiling.
    #
    # Never reveal max_rate.
    # ---------------------------------------------------------
    if negotiation_round == 1:
        counter_rate = min(
            carrier_rate - 100,
            max_rate - 100,
        )

        if counter_rate <= 0:
            return {
                "decision": "reject",
                "next_rate": None,
                "round": negotiation_round,
            }

        return {
            "decision": "counter",
            "next_rate": int(counter_rate),
            "round": negotiation_round,
        }

    if negotiation_round == 2:
        counter_rate = max_rate - 50

        if counter_rate <= 0:
            return {
                "decision": "reject",
                "next_rate": None,
                "round": negotiation_round,
            }

        return {
            "decision": "final_counter",
            "next_rate": int(counter_rate),
            "round": negotiation_round,
        }

    # ---------------------------------------------------------
    # Round 3: no further negotiation.
    # ---------------------------------------------------------
    return {
        "decision": "reject",
        "next_rate": None,
        "round": negotiation_round,
        "reason": "negotiation_limit_reached",
    }


# ============================================================
# BOOK LOAD
# ============================================================

@app.post("/book")
def book_load(
    request: BookRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)

    load_id = clean(request.load_id)
    mc_number = clean(request.mc_number)
    run_id = clean(request.run_id)

    rate_raw = clean(request.agreed_rate) or clean(request.carrier_rate)
    rate_raw = rate_raw.replace("$", "").replace(",", "")

    # ---------------------------------------------------------
    # Basic validation
    # ---------------------------------------------------------
    if not run_id:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "missing_run_id",
        }

    if not load_id:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "missing_load_id",
        }

    if not mc_number:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "missing_mc_number",
        }

    if not rate_raw:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "missing_agreed_rate",
        }

    try:
        carrier_rate = float(rate_raw)
    except ValueError:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "invalid_rate",
        }

    if carrier_rate <= 0:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "invalid_rate",
        }

    # ---------------------------------------------------------
    # OTP verification
    # ---------------------------------------------------------
    otp = OTP_STORE.get(otp_key(run_id, mc_number))

    if not otp or not otp.get("verified", False):
        return {
            "booked": False,
            "booking_id": None,
            "reason": "carrier_not_verified",
        }

    # ---------------------------------------------------------
    # NEGOTIATION ACCEPTANCE CHECK
    #
    # This is the important new protection.
    #
    # The LLM cannot simply invent an agreed_rate and call /book.
    # The middleware must have previously recorded an accepted
    # rate for this load.
    # ---------------------------------------------------------
    negotiation = NEGOTIATION_STATE.get(load_id)

    if not negotiation:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "no_negotiation",
        }

    if not negotiation.get("accepted", False):
        return {
            "booked": False,
            "booking_id": None,
            "reason": "carrier_rate_not_accepted",
        }

    accepted_rate = negotiation.get("agreed_rate")

    if accepted_rate is None:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "missing_accepted_rate",
        }

    # ---------------------------------------------------------
    # Booking rate must exactly match the accepted rate.
    # ---------------------------------------------------------
    if abs(float(accepted_rate) - carrier_rate) > 0.01:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "rate_not_matching_accepted_rate",
            "accepted_rate": accepted_rate,
        }

    # ---------------------------------------------------------
    # Prevent duplicate booking
    # ---------------------------------------------------------
    existing = BOOKINGS.get(load_id)

    if existing and existing.get("status") == "BOOKED":
        return {
            "booked": True,
            "booking_id": existing["booking_id"],
            "reason": "already_booked",
        }

    # ---------------------------------------------------------
    # Deterministic HappyRobot test carrier
    # ---------------------------------------------------------
    if mc_number == "9999901":
        booking_id = f"TEST-{load_id}-BOOKED"

        BOOKINGS[load_id] = {
            "booking_id": booking_id,
            "mc_number": mc_number,
            "carrier_rate": carrier_rate,
            "created_at": datetime.utcnow().isoformat(),
            "status": "BOOKED",
        }

        return {
            "booked": True,
            "booking_id": booking_id,
            "reason": "booked",
        }

    # ---------------------------------------------------------
    # Real TMS booking
    # ---------------------------------------------------------
    try:
        records = tms_request(
            "LOAD_BOOK",
            {
                "LOAD_ID": load_id,
                "MC_NUM": mc_number,
                "AGREED_RATE": str(int(carrier_rate)),
            },
        )

    except Exception as exc:
        print(f"[TMS BOOK ERROR] {exc}")

        return {
            "booked": False,
            "booking_id": None,
            "reason": "tms_error",
        }

    # ---------------------------------------------------------
    # Validate TMS response
    # ---------------------------------------------------------
    if not isinstance(records, list):
        print(f"[TMS BOOK ERROR] Malformed response: {records!r}")

        return {
            "booked": False,
            "booking_id": None,
            "reason": "tms_malformed_response",
        }

    if records:
        record = records[0]

        if not isinstance(record, dict):
            print(f"[TMS BOOK ERROR] Invalid record: {record!r}")

            return {
                "booked": False,
                "booking_id": None,
                "reason": "tms_malformed_response",
            }

        status = clean(record.get("status")).upper()
        booking_ref = clean(record.get("booking_ref"))

        if status in {"BOOKED", "PENDING"} and booking_ref:
            BOOKINGS[load_id] = {
                "booking_id": booking_ref,
                "mc_number": mc_number,
                "carrier_rate": carrier_rate,
                "created_at": datetime.utcnow().isoformat(),
                "status": status,
            }

            return {
                "booked": True,
                "booking_id": booking_ref,
                "reason": "booked",
            }

    return {
        "booked": False,
        "booking_id": None,
        "reason": "booking_failed",
    }


# ============================================================
# OPTIONAL RAW TMS TEST ENDPOINT
#
# USEFUL DURING DEVELOPMENT.
# REMOVE BEFORE FINAL PRODUCTION DEPLOYMENT.
# ============================================================

class RawTMSRequest(BaseModel):
    command: str
    fields: dict[str, str] = {}


@app.post("/tms-raw")
def tms_raw(
    request: RawTMSRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)
    if not ENABLE_TMS_RAW:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        records = tms_request(request.command, request.fields)
        # Never leak MAX_BUY, even from the debug endpoint.
        for record in records:
            record.pop("max_buy", None)
        return {"ok": True, "records": records}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
