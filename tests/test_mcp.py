"""
MCP surface tests — the new highest seam for the MCP protocol.

One in-memory MCP client-session suite against the FastMCP server
(the SDK's in-memory transport, the same pattern the SDK's own
tests use), plus the auth assertions exercised at the ASGI
transport the middleware actually protects (the mounted app).

Tests assert only external behavior at the seam: list_tools /
call_tool results at the MCP seam, HTTP status and body at the
ASGI seam. No core internals, no prompt text.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import LATEST_PROTOCOL_VERSION

# The agent's tool definitions are the reference for tool identity:
# names, descriptions, and parameter names must match them.
from agent.inbound_carrier_sales import TOOLS

from app.core.config import NEGOTIATION_LIMITS
from app.core.state import BOOKINGS, NEGOTIATION_STATE, OTP_STORE
from app.main import API_KEY, app
from app.mcp_server import mcp

EXPECTED_TOOL_NAMES = [
    "verify_carrier",
    "send_verification_code",
    "check_verification_code",
    "search_loads",
    "evaluate_offer",
    "book_load",
]

REFERENCE_TOOLS = {
    tool["function"]["name"]: tool["function"] for tool in TOOLS
}


@asynccontextmanager
async def mcp_client_session():
    """
    In-memory MCP client session against the FastMCP server.

    The in-memory transport bypasses the ASGI mount, so the auth
    middleware is not exercised here — auth is asserted separately
    at the ASGI transport (see the ASGI tests below).
    """
    async with create_connected_server_and_client_session(mcp) as session:
        yield session


def test_list_tools_returns_exactly_the_six_mcp_tools():
    async def _run():
        async with mcp_client_session() as session:
            tools = (await session.list_tools()).tools
        return tools

    tools = asyncio.run(_run())

    assert [tool.name for tool in tools] == EXPECTED_TOOL_NAMES


def test_tool_identity_matches_the_agent_tool_definitions():
    async def _run():
        async with mcp_client_session() as session:
            return (await session.list_tools()).tools

    tools = {tool.name: tool for tool in asyncio.run(_run())}

    for name in EXPECTED_TOOL_NAMES:
        reference = REFERENCE_TOOLS[name]
        tool = tools[name]

        # Descriptions are byte-identical to the agent's tool
        # descriptions (the docstring synchronization mechanism).
        assert tool.description == reference["description"]

        properties = tool.inputSchema["properties"]

        # Pre-existing parameter names are identical, plus run_id.
        assert (
            set(properties)
            == set(reference["parameters"]["properties"]) | {"run_id"}
        )

        # Pre-existing parameters keep their types.
        for param, schema in reference["parameters"]["properties"].items():
            assert properties[param]["type"] == schema["type"]

        # The required list is unchanged: run_id is an additional
        # argument alongside the pre-existing parameters (optional,
        # mirroring the HTTP surface's optional run_id).
        assert properties["run_id"]["type"] == "string"
        assert set(tool.inputSchema["required"]) == set(
            reference["parameters"]["required"]
        )


# ============================================================
# BEHAVIOR: call_tool round trips over the in-memory transport
# ============================================================

RUN_ID = "mcp-test-run"
TEST_MC = "9999901"  # test carrier with the fixed code below
TEST_CODE = "424242"
CHI_REEFER = "TEST-CHI-REEFER"
HOU_FLATBED = "TEST-HOU-FLATBED"


def setup_function():
    OTP_STORE.clear()
    NEGOTIATION_STATE.clear()
    BOOKINGS.clear()

    # Seeded the way tests/test_negotiation.py seeds: the negotiation
    # ceilings are server-side configuration, and the loads exercised
    # below need one to be negotiable at all.
    NEGOTIATION_LIMITS[CHI_REEFER] = 2500
    NEGOTIATION_LIMITS[HOU_FLATBED] = 2300


def verify_calls():
    """The verify -> send code -> check code prefix of every flow."""
    return [
        ("verify_carrier", {"mc_number": TEST_MC, "run_id": RUN_ID}),
        (
            "send_verification_code",
            {"mc_number": TEST_MC, "is_resend": False, "run_id": RUN_ID},
        ),
        (
            "check_verification_code",
            {"mc_number": TEST_MC, "code": TEST_CODE, "run_id": RUN_ID},
        ),
    ]


def call_tools(calls):
    """
    Run a sequence of MCP tool calls in one in-memory session.

    Returns the parsed JSON of each tool result — the same response
    shapes and reason codes the HTTP surface returns.
    """

    async def _run():
        results = []
        async with mcp_client_session() as session:
            for name, args in calls:
                result = await session.call_tool(name, args)
                assert not result.isError, f"{name} failed: {result.content}"
                results.append(json.loads(result.content[0].text))
        return results

    return asyncio.run(_run())


def test_test_carrier_verification_round_trip():
    verify, sent, checked = call_tools(verify_calls())

    assert verify == {
        "eligible": True,
        "carrier_name": "Test Carrier Pass",
        "has_phone_on_file": True,
        "reason": "verified",
    }
    assert sent == {
        "sent": True,
        "resend_allowed": True,
        "masked_destination": "***-***-1234",
    }
    assert checked == {
        "verified": True,
        "status": "verified",
        "attempts_remaining": 3,
    }


def test_booking_rejects_unverified_carrier_over_mcp():
    [booked] = call_tools(
        [
            (
                "book_load",
                {
                    "load_id": CHI_REEFER,
                    "mc_number": TEST_MC,
                    "agreed_rate": 2400,
                    # A run id with no verification session on file.
                    "run_id": "mcp-unverified-run",
                },
            ),
        ]
    )

    assert booked == {
        "booked": False,
        "booking_id": None,
        "reason": "carrier_not_verified",
    }


def test_booking_rejects_rate_not_matching_accepted_rate_over_mcp():
    booked = call_tools(
        verify_calls()
        + [
            (
                "evaluate_offer",
                {
                    "load_id": CHI_REEFER,
                    "carrier_rate": 2400,
                    "round": 1,
                    "run_id": RUN_ID,
                },
            ),
            # Books at 2500, but the accepted rate is 2400.
            (
                "book_load",
                {
                    "load_id": CHI_REEFER,
                    "mc_number": TEST_MC,
                    "agreed_rate": 2500,
                    "run_id": RUN_ID,
                },
            ),
        ]
    )[-1]

    assert booked == {
        "booked": False,
        "booking_id": None,
        "reason": "rate_not_matching_accepted_rate",
        "accepted_rate": 2400,
    }


def above_ceiling_offers():
    # Three above-ceiling offers exhaust the rounds; the
    # negotiation for the load ends rejected.
    return [
        (
            "evaluate_offer",
            {
                "load_id": HOU_FLATBED,
                "carrier_rate": 3000,
                "round": round_number,
                "run_id": RUN_ID,
            },
        )
        for round_number in (1, 2, 3)
    ]


def test_booking_rejects_load_with_rejected_negotiation_over_mcp():
    booked = call_tools(
        verify_calls()
        + above_ceiling_offers()
        + [
            (
                "book_load",
                {
                    "load_id": HOU_FLATBED,
                    "mc_number": TEST_MC,
                    "agreed_rate": 2300,
                    "run_id": RUN_ID,
                },
            ),
        ]
    )[-1]

    assert booked == {
        "booked": False,
        "booking_id": None,
        "reason": "carrier_rate_not_accepted",
    }


def test_offer_evaluation_to_booking_round_trip_over_mcp():
    (
        verify,
        sent,
        checked,
        searched,
        counter,
        accepted,
        booked,
    ) = call_tools(
        verify_calls()
        + [
            (
                "search_loads",
                {
                    "origin": "Chicago, IL",
                    "equipment_type": "REEFER",
                    "destination": "",
                    "pickup_date": "",
                    "exclude_load_ids": "",
                    "run_id": RUN_ID,
                },
            ),
            (
                "evaluate_offer",
                {
                    "load_id": CHI_REEFER,
                    "carrier_rate": 2600,
                    "round": 1,
                    "run_id": RUN_ID,
                },
            ),
            (
                "evaluate_offer",
                {
                    "load_id": CHI_REEFER,
                    "carrier_rate": 2400,
                    "round": 2,
                    "run_id": RUN_ID,
                },
            ),
            (
                "book_load",
                {
                    "load_id": CHI_REEFER,
                    "mc_number": TEST_MC,
                    "agreed_rate": 2400,
                    "run_id": RUN_ID,
                },
            ),
        ]
    )

    assert checked == {
        "verified": True,
        "status": "verified",
        "attempts_remaining": 3,
    }

    loads = searched["loads"]
    assert [load["load_id"] for load in loads] == [CHI_REEFER]
    assert loads[0]["posted_rate"] == 2400

    assert counter == {"decision": "counter", "next_rate": 2400, "round": 1}
    assert accepted == {"decision": "accept", "next_rate": 2400, "round": 2}

    assert booked == {
        "booked": True,
        "booking_id": f"TEST-{CHI_REEFER}-BOOKED",
        "reason": "booked",
    }


# ============================================================
# AUTH: at the ASGI transport the middleware actually protects
#
# The in-memory transport above bypasses the ASGI mount (and with
# it the API-key middleware), so the auth assertions are exercised
# here against the mounted application, speaking the streamable-
# HTTP wire protocol (JSON-RPC over HTTP) the way a real MCP
# client would.
# ============================================================

# Streamable-HTTP wire headers, as a real MCP client sends them.
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

INITIALIZE_PARAMS = {
    "protocolVersion": LATEST_PROTOCOL_VERSION,
    "capabilities": {},
    "clientInfo": {"name": "pytest-asgi-client", "version": "0.0.0"},
}


@pytest.fixture(scope="module")
def asgi_client():
    """
    One TestClient lifetime per module, aimed at a localhost base
    URL.

    The MCP session manager starts in the application's lifespan
    and can run only once per instance, as in a real process — so
    all ASGI tests share one TestClient (one lifespan) instead of
    each entering their own. The base URL stands in for the
    production deployment (CARRIER_API_BASE on localhost): it keeps
    the Host header inside the streamable-HTTP transport's allowed
    hosts, and the mount's /mcp -> /mcp/ redirect stays same-host,
    so credentials survive it exactly as they would in production.
    """
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def post_mcp(client, payload, session_id=None, api_key=None):
    headers = dict(MCP_HEADERS)
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    if api_key is not None:
        headers["x-api-key"] = api_key
    return client.post("/mcp", json=payload, headers=headers)


def json_rpc(response):
    """Extract the JSON-RPC message from a JSON or SSE response body."""
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    messages = [
        json.loads(line.removeprefix("data:").strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert messages, f"no JSON-RPC message in response: {response.text!r}"
    return messages[-1]


def test_mcp_rejects_requests_without_a_valid_api_key(asgi_client):
    response = post_mcp(
        asgi_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": INITIALIZE_PARAMS,
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


def test_mcp_accepts_requests_with_the_api_key(asgi_client):
    response = post_mcp(
        asgi_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": INITIALIZE_PARAMS,
        },
        api_key=API_KEY,
    )

    assert response.status_code == 200
    session_id = response.headers["mcp-session-id"]
    result = json_rpc(response)["result"]
    assert result["serverInfo"]["name"] == "Carrier Middleware"

    # Complete the handshake, then list the tools over the same
    # protected transport.
    post_mcp(
        asgi_client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        api_key=API_KEY,
    )

    tools_response = post_mcp(
        asgi_client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id=session_id,
        api_key=API_KEY,
    )

    assert tools_response.status_code == 200
    names = [
        tool["name"] for tool in json_rpc(tools_response)["result"]["tools"]
    ]

    assert names == EXPECTED_TOOL_NAMES


def test_mcp_accepts_the_same_header_forms_as_the_http_surface(asgi_client):
    # The shared key check accepts x-api-key, a raw Authorization
    # header, and Authorization: ApiKey/Bearer — the same accepted
    # forms as the HTTP surface. Pin one additional form here:
    # a Bearer authorization.
    response = asgi_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": INITIALIZE_PARAMS,
        },
        headers={**MCP_HEADERS, "Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200
    assert response.headers["mcp-session-id"]
    assert json_rpc(response)["result"]["serverInfo"]["name"] == (
        "Carrier Middleware"
    )
