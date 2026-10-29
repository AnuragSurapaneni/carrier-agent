"""
FMCSA carrier verification.

The single implementation of the /verify business operation: test
carriers (adversarial lanes), the demo carrier (PoC), or a live
FMCSA lookup. Returns the /verify response dict.
"""

import requests

from app.core import config
from app.core.text import clean


def verify_carrier(mc_number) -> dict:
    """Verify a carrier by MC number."""
    mc_number = clean(mc_number)

    if not mc_number:
        return {
            "eligible": False,
            "carrier_name": None,
            "has_phone_on_file": False,
            "reason": "missing_mc",
        }

    test_carrier = config.get_test_carrier(mc_number)
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

    if config.ENABLE_TEST_CARRIERS and mc_number == config.DEMO_CARRIER_MC:
        result = {
            "eligible": True,
            "carrier_name": config.DEMO_CARRIER["carrier_name"],
            "has_phone_on_file": config.DEMO_CARRIER["has_phone_on_file"],
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

    if not config.FMCSA_API_KEY:
        return fail("fmcsa_not_configured")

    url = (
        "https://mobile.fmcsa.dot.gov"
        f"/qc/services/carriers/docket-number/{mc_number}"
    )

    try:
        response = requests.get(url, params={"webKey": config.FMCSA_API_KEY}, timeout=10)
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
