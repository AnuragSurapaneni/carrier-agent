"""
Shared API-key authentication.

The single implementation of the key check for every protocol
surface (HTTP today; the MCP transport later): the same accepted
header forms and the same constant-time comparison against the
shared API key, so no protocol can accept what another rejects.
"""

import hmac
from typing import Optional

from app.core.config import API_KEY


def api_key_matches(value: str) -> bool:
    return hmac.compare_digest(value.encode("utf-8"), API_KEY.encode("utf-8"))


def is_authorized(authorization: Optional[str], x_api_key: Optional[str]) -> bool:
    """
    Accept any of:
        x-api-key: <configured key>
        Authorization: <configured key>
        Authorization: ApiKey <configured key>
        Authorization: Bearer <configured key>
    """
    if x_api_key and api_key_matches(x_api_key):
        return True

    if authorization:
        auth = authorization.strip()
        if api_key_matches(auth):
            return True
        lowered = auth.lower()
        if lowered.startswith("apikey ") and api_key_matches(auth[7:].strip()):
            return True
        if lowered.startswith("bearer ") and api_key_matches(auth[7:].strip()):
            return True

    return False
