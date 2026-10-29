"""
Booking guards, duplicate-booking protection, and booking execution.

The single implementation of the /book business operation. Every
guard — OTP verified and unexpired, a previously accepted
negotiation rate, exact rate match, no duplicate booking — is
enforced here, so no protocol can bypass them.
"""

from datetime import datetime

from app.core.state import BOOKINGS, NEGOTIATION_STATE, OTP_STORE, otp_key
from app.core.text import clean
from app.core.tms import tms_request


def book_load(run_id, load_id, mc_number, agreed_rate, carrier_rate=None) -> dict:
    """
    Book a load; returns the /book response dict.

    agreed_rate is the canonical field; carrier_rate is accepted for
    backward compatibility (the legacy workflow sends the same rate
    under that key), and only when agreed_rate is blank.
    """
    load_id = clean(load_id)
    mc_number = clean(mc_number)
    run_id = clean(run_id)

    rate_raw = clean(agreed_rate) or clean(carrier_rate)
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
