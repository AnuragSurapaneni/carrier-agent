"""
OTP lifecycle: send, check, TTL, attempts, resend rules, pruning.

The single implementation of the /send-code and /check-code business
operations. Code sessions are keyed per call (run_id) and per MC
number, in the shared OTP_STORE.
"""

import random
from datetime import datetime, timedelta

from app.core import config
from app.core.state import OTP_STORE, otp_key, prune_otp_store
from app.core.text import clean, to_bool


def send_code(run_id, mc_number, is_resend) -> dict:
    """Issue (or resend) a verification code for this call."""
    prune_otp_store()

    mc_number = clean(mc_number)
    run_id = clean(run_id)
    is_resend = to_bool(is_resend)

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

    test_carrier = config.get_test_carrier(mc_number)

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

    elif config.ENABLE_TEST_CARRIERS and mc_number == config.DEMO_CARRIER_MC:
        masked = "***-***-" + config.DEMO_CARRIER["phone"][-4:]
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
        "expires_at": now + timedelta(minutes=config.OTP_TTL_MINUTES),
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


def check_code(run_id, mc_number, code) -> dict:
    """Check a code read back by the caller."""
    mc_number = clean(mc_number)
    run_id = clean(run_id)
    code = "".join(ch for ch in clean(code) if ch.isdigit())

    if not mc_number or not run_id or not code:
        return {"verified": False, "status": "invalid",
                "attempts_remaining": 0, "reason": "missing_input"}

    otp = OTP_STORE.get(otp_key(run_id, mc_number))

    if not otp or datetime.utcnow() > otp["expires_at"]:
        return {"verified": False, "status": "expired", "attempts_remaining": 0}

    if otp["attempts"] >= config.OTP_MAX_ATTEMPTS:
        return {"verified": False, "status": "locked", "attempts_remaining": 0}

    if code != otp["code"]:
        otp["attempts"] += 1
        remaining = max(0, config.OTP_MAX_ATTEMPTS - otp["attempts"])
        if remaining == 0:
            return {"verified": False, "status": "locked", "attempts_remaining": 0}
        return {"verified": False, "status": "invalid", "attempts_remaining": remaining}

    otp["verified"] = True
    return {"verified": True, "status": "verified",
            "attempts_remaining": config.OTP_MAX_ATTEMPTS - otp["attempts"]}
