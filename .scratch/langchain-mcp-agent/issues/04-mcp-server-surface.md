# 04: MCP server surface + in-memory test suite

**What to build:** The middleware's six business operations are exposed as MCP tools on the official Python MCP SDK (FastMCP), mounted in the same process on the existing ASGI application at the dedicated MCP path (base URL + `/mcp`) using the streamable-HTTP transport, with its session manager wired into the application lifespan. Any MCP-compatible client (IDE agent, desktop assistant) can connect, list the six tools, and drive carrier verification, load search, negotiation, and booking over the wire — no custom HTTP client needed. Tool results carry the same reason codes and response shapes as today's HTTP responses, so prompt logic does not special-case the transport.

**Tool identity:** the MCP surface exposes exactly these six tools — `verify_carrier`, `send_verification_code`, `check_verification_code`, `search_loads`, `evaluate_offer`, `book_load` — with names, descriptions, and parameter names identical to the current agent tool definitions. FastMCP derives names from function names and descriptions from docstrings; that is the synchronization mechanism with the verbatim production prompt, so the docstrings must be kept byte-identical to the current tool descriptions (the reference list lives in the agent script's tool definitions). Health, root, and the dev-only raw TMS passthrough stay outside the MCP surface — exactly six tools, no more.

**Run-id decision (design note):** the agent passes a per-call run id with every tool call so session-scoped state (OTP, negotiation) behaves identically to today; therefore the tools accept `run_id` as an additional string argument alongside the existing parameters. The tool-identity assertion pins the six expected tool names; the pre-existing parameter names stay identical to the current definitions.

**Authentication:** a small transport-level (ASGI) middleware wraps the mounted MCP sub-application and enforces the same shared API key as the HTTP surface — the same accepted header names and the same constant-time comparison — because host-application dependency injection does not reach mounted sub-applications. Mounting the second protocol must not create an unauthenticated path to booking.

**Transports:** streamable HTTP is the mounted default; stdio remains launchable as an alternate transport for local experimentation (Claude-Desktop-style setups) without affecting the mounted server.

**Tests (new highest seam for the MCP surface):** one in-memory MCP client-session suite against the FastMCP application (the SDK's in-memory transport — the same pattern the SDK's own tests use), asserting:
- (a) tool identity — exactly the six expected tools with the expected names;
- (b) auth — requests without a valid key are rejected and with the valid key are accepted, exercised at the transport the middleware protects;
- (c) behavior — `call_tool` round trips covering a test-carrier verification and a full offer-evaluation → booking flow that pins the booking guards (unverified OTP, wrong rate, rejected load) through the MCP surface.

The test-carrier and test-load fixtures are env-flag gated and the app reads those flags at import time, so the suite must enable the flags before importing the application.

**Blocked by:**
- 02: Add exact-pinned AI dependencies
- 03: Extract framework-agnostic core layer

**Status:** ready-for-agent

- [ ] `list_tools` against the MCP surface returns exactly the six expected tools with the expected names
- [ ] Tool names, descriptions, and pre-existing parameter names match the current agent tool definitions (docstrings byte-identical to the tool descriptions)
- [ ] The MCP app is mounted at the MCP path on the existing ASGI application and its session manager runs as part of the application lifespan
- [ ] Requests to the MCP path without a valid key are rejected; with the valid key they are accepted (same accepted header names, constant-time comparison)
- [ ] The in-memory round trip covers a test-carrier verification and a full offer-evaluation → booking flow, including guard failures: unverified OTP, rate not matching the accepted rate, rejected load
- [ ] Health, root, and the raw TMS passthrough are not exposed as MCP tools
- [ ] The server remains launchable over stdio
- [ ] The three HTTP suites remain green (one system of record, two protocols, no forked state)
