"""
MCP protocol surface for the Carrier Middleware system of record.

This module is the MCP protocol surface: the six business operations
exposed as MCP tools on the official Python MCP SDK (FastMCP). Like
the HTTP adapter in app.main, it contains no business rules — every
tool is a thin pass-through to the single, framework-agnostic
implementation in app.core, so the two protocols cannot drift.

Tool names come from the function names and descriptions come from
the docstrings; both are kept identical to the agent's tool
definitions (the TOOLS list in agent/inbound_carrier_sales.py), so
the verbatim production prompt keeps working unchanged. Each tool
also takes a run_id alongside the pre-existing parameters, so the
session-scoped core state (OTP, negotiation) behaves identically
over MCP to over HTTP.

The streamable-HTTP sub-application (mcp_app) is mounted on the
host ASGI application at /mcp in app.main, behind the shared
API-key middleware; its session manager runs in the host
application's lifespan. Health, root, and the dev-only raw TMS
passthrough are not MCP tools.

Stdio remains available as an alternate transport for local
experimentation (Claude-Desktop-style setups) without affecting the
mounted server:

    python -m app.mcp_server
"""

from mcp.server.fastmcp import FastMCP
from starlette.datastructures import Headers

from app.core import auth, booking, loads, negotiation, otp, verification

mcp = FastMCP(
    "Carrier Middleware",
    # The streamable-HTTP sub-application is mounted under /mcp on
    # the host ASGI application, so its endpoint lives at the
    # sub-application's root.
    streamable_http_path="/",
)


# Every tool takes the pre-existing parameters — in their reference
# order, with their reference required-ness — plus an additional
# run_id alongside them (optional, mirroring the HTTP surface's
# optional run_id, which the core answers with reason codes when
# absent). The agent passes a per-call run id with every call, so
# the session-scoped core state behaves identically to over HTTP.


@mcp.tool()
def verify_carrier(mc_number: str, run_id: str = "") -> dict:
    """Call as soon as the caller gives their MC number, before anything else. Checks the carrier's FMCSA operating authority. Returns whether the carrier is eligible, the carrier name, and whether a phone number is on file for the verification code."""
    return verification.verify_carrier(mc_number)


@mcp.tool()
def send_verification_code(mc_number: str, is_resend: bool, run_id: str = "") -> dict:
    """Call right after verify_carrier returns eligible. Texts a six-digit code to the phone number on file for this MC. Never pass a phone number; the server chooses it. Also call once more, with is_resend true, only if the caller says they didn't get the code. Only one resend is allowed per call."""
    return otp.send_code(run_id, mc_number, is_resend)


@mcp.tool()
def check_verification_code(mc_number: str, code: str, run_id: str = "") -> dict:
    """Call every time the caller reads back a code. Pass exactly the digits they said. Returns only whether the code is verified, how many attempts are left, and whether the session is locked or expired. You never see the real code."""
    return otp.check_code(run_id, mc_number, code)


@mcp.tool()
def search_loads(
    origin: str,
    equipment_type: str,
    destination: str = "",
    pickup_date: str = "",
    exclude_load_ids: str = "",
    run_id: str = "",
) -> dict:
    """Call only after the caller is verified, once you know their current location or origin and their equipment type. If the caller already gave these earlier in the call, including before verification, call this right after verification without asking again. Include destination and pickup date only if the caller offered them. Returns matching available loads, best match first, with lane, dates, equipment, weight, commodity and posted rate. It never returns a maximum rate."""
    return loads.search_loads(
        loads.LoadQuery(
            origin=origin,
            destination=destination,
            equipment_type=equipment_type,
            pickup_date=pickup_date,
            exclude_load_ids=exclude_load_ids,
        )
    )


@mcp.tool()
def evaluate_offer(
    load_id: str, carrier_rate: float, round: int, run_id: str = ""
) -> dict:
    """Call every time the carrier names a rate for the pitched load, whether a counter or an acceptance of the posted rate. The server decides. Returns decision (accept, counter, final_counter or reject) and the rate to offer next. Never decide a rate yourself."""
    # request.round is not trusted: the middleware owns the round count.
    return negotiation.evaluate_offer(load_id, carrier_rate)


@mcp.tool()
def book_load(
    load_id: str, mc_number: str, agreed_rate: float, run_id: str = ""
) -> dict:
    """Books a load the caller just agreed to. Call ONLY when all are true: the carrier is verified (check_verification_code returned verified), search_loads returned this exact load_id, and the most recent evaluate_offer call for this load returned accept. Use that accepted rate only. Never call after an empty search, for an ineligible or unverified caller, to end a call, or with a blank load_id, blank MC, or a rate of 0. Call once per accepted offer. Returns booked true or false; if false, a callback task has already been created."""
    return booking.book_load(run_id, load_id, mc_number, agreed_rate)


class McpApiKeyMiddleware:
    """
    Transport-level API-key enforcement for the mounted MCP
    sub-application.

    Host-application dependency injection does not reach mounted
    sub-applications, so the shared key check runs here, at the ASGI
    level, wrapping the MCP sub-application: the same accepted
    header forms and the same constant-time comparison as the HTTP
    surface (app.core.auth), so mounting a second protocol cannot
    create an unauthenticated path to booking.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not auth.is_authorized(
            headers.get("authorization"), headers.get("x-api-key")
        ):
            # The same 401 body the HTTP surface emits (app.core.auth).
            body = auth.INVALID_API_KEY_BODY
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


# ------------------------------------------------------------
# STREAMABLE-HTTP SUB-APPLICATION
#
# The sub-application is wrapped with the API-key middleware and
# mounted on the host ASGI application at /mcp in app.main; the
# session manager runs in the host application's lifespan.
# ------------------------------------------------------------

mcp_http_app = mcp.streamable_http_app()

mcp_app = McpApiKeyMiddleware(mcp_http_app)


if __name__ == "__main__":
    # Alternate transport for local experimentation. The mounted
    # streamable-HTTP server is unaffected.
    mcp.run(transport="stdio")
