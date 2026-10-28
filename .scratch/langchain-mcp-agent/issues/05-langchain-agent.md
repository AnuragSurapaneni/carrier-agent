# 05: Rebuild the agent on LangChain over MCP

**What to build:** The local conversational agent is rebuilt on LangChain's canonical agent API: `create_agent` with a chat model constructed provider-agnostically (`init_chat_model`) from the existing `AGENT_MODEL` setting at temperature 0.3, the verbatim bilingual (EN/ES) system prompt (already de-branded by ticket 01), and a tool list of the six middleware tools loaded from the MCP server via the standard LangChain–MCP client adapter (`load_mcp_tools`) over streamable HTTP at `{CARRIER_API_BASE}/mcp` — the agent's MCP client sends `CARRIER_API_KEY` as a static header — plus two agent-local call-control tools, `hang_up` and `transfer_to_rep` (decorated Python functions). Call control belongs to the agent, not the system of record: ending or escalating a call is instant and never depends on a network round trip to the middleware.

The agent keeps generating a per-call run id and passes it to every MCP tool call, so session-scoped state (OTP, negotiation) behaves identically to today. No new environment variables: `CARRIER_API_KEY` doubles as the MCP credential and `CARRIER_API_BASE` is the middleware's base URL. One REPL process handles one call: one MCP session for the life of the call, closed cleanly on exit — including on hang-up and on transfer. No raw HTTP to the middleware and no hand-rolled tool schemas remain; the agent talks to the system of record exclusively through MCP tool calls. The REPL UX, the agent script's and its launcher's names, and the environment contract are unchanged (same `setup.sh`, same `/quit`, same summary-to-SQLite step from ticket 06's predecessor behavior for now).

**Testing (per the spec's testing decisions):** there is deliberately no fake-LLM end-to-end harness for the agent. The agent's correctness decomposes into tool behavior (ticket 04's MCP suite), endpoint behavior (the HTTP suites), and the interactive loop — the interactive loop is verified by a manual smoke run: start the middleware with the test carriers and test loads enabled, run a full call (verify `9999901` → code `424242` → search loads → negotiate → book), and confirm the call ends with the transfer after a successful booking and the summary is saved. `OPENAI_API_KEY` must be supplied by a human for that smoke run; the implementer cannot perform it and must leave clear run instructions for the human.

**Blocked by:**
- 02: Add exact-pinned AI dependencies
- 04: MCP server surface + in-memory test suite

**Status:** resolved

- [ ] The agent is built with the canonical agent API (`create_agent` + `init_chat_model` from `AGENT_MODEL`, temperature 0.3)
- [ ] The six middleware tools arrive from the MCP server via the standard adapter over streamable HTTP; the API key is sent as a static header from `CARRIER_API_KEY`
- [ ] `hang_up` and `transfer_to_rep` remain local agent tools and end the call instantly without a middleware round trip
- [ ] The verbatim bilingual system prompt is preserved apart from the ticket-01 rename
- [ ] A per-call run id is generated and passed to every MCP tool call
- [ ] The MCP session is opened once per process and closed cleanly on exit, including on hang-up and on transfer
- [ ] No raw HTTP calls to the middleware and no hand-rolled tool schemas remain in the agent
- [ ] The existing test suites (HTTP + MCP) remain green
- [ ] Clear human-run instructions exist for the manual smoke call (test carrier `9999901`, code `424242`: verify → code → search → negotiate → book → transfer)

## Comments

- 2026-10-29: Resolved — merged into `int/langchain-mcp-agent` at `54d2bd9` (two-axis code review passed; branch fix pass `f3a2eda`).
