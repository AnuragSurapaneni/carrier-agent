import hmac
from datetime import datetime
from typing import Optional, Union

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.core import booking, config, loads, negotiation, otp, verification

# Configuration lives in the framework-agnostic core layer; the names
# below are re-exported here so existing importers (and the test
# suites) keep their original paths.
from app.core.config import (  # noqa: F401
    API_KEY,
    NEGOTIATION_LIMITS,
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
from app.core.text import clean
from app.core.tms import strip_max_buy, tms_request

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
    return otp.send_code(request.run_id, request.mc_number, request.is_resend)


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
    return otp.check_code(request.run_id, request.mc_number, request.code)


# ============================================================
# SEARCH LOADS
# ============================================================

@app.post("/search-loads")
def search_loads(
    request: SearchLoadsRequest,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    authenticate(authorization, x_api_key)
    return loads.search_loads(
        request.origin,
        request.destination,
        request.equipment_type,
        request.pickup_date,
        request.exclude_load_ids,
    )


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
    # request.round is not trusted: the middleware owns the round count.
    return negotiation.evaluate_offer(request.load_id, request.carrier_rate)


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
    return booking.book_load(
        request.run_id,
        request.load_id,
        request.mc_number,
        request.agreed_rate,
        request.carrier_rate,
    )


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
    if not config.ENABLE_TMS_RAW:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        records = tms_request(request.command, request.fields)
        # Never leak MAX_BUY, even from the debug endpoint.
        strip_max_buy(records)
        return {"ok": True, "records": records}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
