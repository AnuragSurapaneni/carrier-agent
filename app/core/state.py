"""
In-memory state stores.

Single live objects shared by every protocol. The HTTP adapter
re-exports them from app.main so the test suites keep importing and
mutating them from their original paths.
"""

from datetime import datetime, timedelta

from app.core import config
from app.core.text import clean

# ============================================================
# NEGOTIATION STATE
# ============================================================

NEGOTIATION_STATE = {}

# ============================================================
# STATE
# ============================================================

# OTP state, keyed by "<run_id>:<mc_number>" so concurrent calls
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
    cutoff = datetime.utcnow() - timedelta(hours=config.OTP_SESSION_RETENTION_HOURS)
    for key in [k for k, v in OTP_STORE.items() if v.get("created_at", cutoff) < cutoff]:
        OTP_STORE.pop(key, None)
