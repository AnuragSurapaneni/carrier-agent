"""
Framework-agnostic core layer.

Exactly one implementation of each business operation lives here:
FMCSA verification, the OTP lifecycle, the TMS TCP client, load search
and formatting, the round-limited negotiation engine, the booking
guards, and the in-memory state stores they share.

Protocol adapters (the HTTP handlers in app.main today, the MCP tools
later) are thin layers over these modules and contain no business
rules, so the protocols cannot drift apart.
"""
