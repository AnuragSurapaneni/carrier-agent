# 03: Extract framework-agnostic core layer

**What to build:** Exactly one implementation of each business operation lives in a framework-agnostic core layer: FMCSA verification, the OTP lifecycle (TTL, attempts, resend rules, pruning), the TMS TCP client and record parsing, load search/filtering/formatting, the round-limited negotiation engine, the booking guards and duplicate-booking protection, and the state stores. The HTTP handlers become thin adapters over that one implementation: Pydantic request models, API-key authentication, response dicts — no business rules in the adapters, so the HTTP and MCP protocols (ticket 04) cannot drift apart. The six HTTP endpoints keep their exact paths, request models, response shapes, reason codes, and authentication — byte-for-byte.

This is the riskiest ticket: logic moves out of ~1,500 lines of handler-interleaved code. The three existing HTTP suites are the acceptance gate: run them after **each extraction chunk**, and a red suite means stop and fix before continuing. The suites (which must stay unmodified) import the app, the API key, the OTP store, the negotiation state, the bookings store, and the negotiation limits from the existing application module and directly mutate the state dicts — those objects must remain reachable, as the single live instances, through the same import paths.

**Blocked by:** 01: Rename vendor brand to generic names

**Status:** resolved

- [ ] Core modules exist and hold all business logic; the HTTP adapters contain no business rules
- [ ] All three existing HTTP suites pass with zero modifications
- [ ] The six HTTP endpoints' paths, request models, response shapes, reason codes, and API-key authentication are byte-identical to before
- [ ] The state stores (OTP, negotiation, bookings) are single-sourced — no forked copies — and reachable via the same import paths the tests use
- [ ] `MAX_BUY` never leaves the server (load formatting and raw-record handling preserve the existing stripping behavior)
- [ ] The dev-only raw TMS passthrough endpoint keeps working exactly as before (including its flag gating and MAX_BUY stripping)

## Comments

- 2026-10-29: Resolved — merged into `int/langchain-mcp-agent` at `e8d8c07` (two-axis code review passed; branch fix pass `f3a2eda`).
