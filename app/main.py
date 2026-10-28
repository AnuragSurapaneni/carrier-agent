import hmac
import random
from datetime import datetime, timedelta
from typing import Optional, Union

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.core import verification

# Configuration and fixtures live in the framework-agnostic core
# layer; they are re-exported here so existing importers (and the
# test suites) keep their original paths.
from app.core.config import (  # noqa: F401
    API_KEY,
    DEMO_CARRIER,
    DEMO_CARRIER_MC,
    ENABLE_TEST_CARRIERS,
    ENABLE_TEST_LOADS,
    ENABLE_TMS_RAW,
    NEGOTIATION_LIMITS,
    OTP_MAX_ATTEMPTS,
    OTP_SESSION_RETENTION_HOURS,
    OTP_TTL_MINUTES,
    TEST_LOADS,
    TMS_AUTH,
    TMS_HOST,
    TMS_PORT,
    TMS_TIMEOUT,
    get_test_carrier,
)
# State stores are the single live objects shared by every protocol;
# re-exported so the test suites keep their original import paths.
from app.core.state import (  # noqa: F401
    BOOKINGS,
    NEGOTIATION_STATE,
    OTP_STORE,
    otp_key,
    prune_otp_store,
)
from app.core.text import (
    clean,
    equipment_to_tms,
    format_tms_datetime,
    split_city_state,
    to_bool,
    to_int,
)
from app.core.tms import strip_max_buy, tms_get_load, tms_request

# ============================================================
# CONFIGURATION
# ============================================================

app = FastAPI(
    title="Carrier Middleware",
    version="1.2.0",
)

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
    # The production voice workflow's Book node sends "agreed_rate".
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
# HEALTH
# ============================================================

@app.get("/")
def root():
    return {"service": "Carrier Middleware", "status": "ok"}


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
    return verification.verify_carrier(request.mc_number)


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

    expires_at = otp.get("expires_at")

    if expires_at is None or datetime.utcnow() > expires_at:
        return {
            "booked": False,
            "booking_id": None,
            "reason": "otp_expired",
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
    try:
        accepted_rate_value = float(accepted_rate)
    except (ValueError, TypeError):
        return {
            "booked": False,
            "booking_id": None,
            "reason": "invalid_accepted_rate",
        }

    if abs(accepted_rate_value - carrier_rate) > 0.01:
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
    # Deterministic production voice workflow test carrier
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
        strip_max_buy(records)
        return {"ok": True, "records": records}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
