# Agent layer on LangChain + MCP; middleware exposed as an in-process MCP server

> Intended as the issue body for the project issue tracker. Triage label: `ready-for-agent`.

## Problem Statement

The prototype's AI agent is coupled to a hand-rolled OpenAI function-calling loop and a hand-coded HTTP client: an endpoint map, manual serialization of tool arguments, and raw HTTP calls per tool. That bespoke glue is the only client of the middleware's HTTP surface today, and it is not the shape modern agent tool-calling is moving to.

The developer wants to rebuild the agent layer on the canonical, industry-standard patterns — a LangChain agent and an MCP (Model Context Protocol) server built on the official Python SDK — both as a deliberate learning exercise and so that any MCP-compatible client (IDE agents, desktop assistants) can drive the system, not just one bespoke script.

At the same time, the middleware is the trusted **system of record**: the production voice workflow consumes its six HTTP endpoints with a fixed contract, and the security model (OTP must be verified, booking rate must match the rate the server accepted, negotiation ceilings never leave the server, round limits enforced server-side) depends on that logic living in exactly one place. The redesign must add the new agent/MCP layer without changing what the system of record does, or who can reach it.

## Solution

- **The agent becomes a LangChain agent.** It is built with the framework's canonical agent API: a chat model, the (verbatim) bilingual system prompt, and a tool list that is loaded from the MCP server via the standard MCP client adapter, plus two local tools for call control. The agent talks to the system of record exclusively through MCP tool calls — no raw HTTP, no hand-rolled tool schemas.
- **The middleware grows an MCP surface in the same process.** The six business operations are exposed as MCP tools by a server built on the official Python MCP SDK (FastMCP), mounted on the existing ASGI application and served over the streamable-HTTP transport. It shares the same in-memory state and the same API-key authentication as the HTTP surface: one system of record, two protocols, no forked state.
- **Business logic is extracted into a framework-agnostic core layer.** FMCSA verification, OTP lifecycle, the TMS TCP client, load search and formatting, the negotiation engine, and the booking guards move into core modules. The HTTP handlers and the MCP tools become thin adapters over that one implementation, so the two protocols cannot drift.
- **The HTTP contract is preserved byte-for-byte** for the production voice workflow: same paths, request models, response shapes, reason codes, and API-key auth. The existing HTTP test suites are unchanged and act as the acceptance gate for the refactor.
- **Post-call transcript summarization moves to LangChain structured output**, writing to the existing SQLite call log with an unchanged schema.
- **The vendor brand name is replaced by generic names**: the service is titled "Carrier Middleware"; the external production system is referred to as "the production voice workflow" in docs, comments, and prompt text.
- **New coverage at the highest practical seam**: an in-memory MCP client suite pins tool identity (exactly the six tools, names/descriptions/params matching the production tool definitions), auth enforcement, and a full offer-evaluation → booking round trip.

## User Stories

1. As a developer learning agent frameworks, I want the agent built with LangChain's canonical agent API (agent + chat model + system prompt), so that I can read and learn from an idiomatic tool-calling agent implementation.
2. As a developer learning MCP, I want the middleware exposed as an MCP server built on the official Python MCP SDK, so that I learn how MCP tools, transports, and sessions actually work rather than from a third-party abstraction.
3. As a developer learning MCP, I want the agent to consume the tools via the standard LangChain–MCP client adapter over streamable HTTP, so that I see a real client–server protocol round trip instead of in-process calls.
4. As a developer, I want exactly one implementation of each business operation in a shared core layer, so that the HTTP and MCP adapters cannot drift apart and the system of record stays single-sourced.
5. As a developer, I want the MCP tool names, descriptions, and parameter shapes to be identical to the current agent tool definitions, so that the verbatim production system prompt keeps working untouched and the string-match guard remains meaningful.
6. As a developer, I want the six MCP tools to be the only tools on the MCP surface, so that health, root, and the dev-only raw TMS passthrough stay outside the LLM's attack surface.
7. As a developer, I want the new AI-related dependencies exact-pinned, so that the learning environment is reproducible weeks from now when the framework ecosystem has moved on.
8. As a developer, I want the README to document the new two-protocol architecture (REST for production, MCP for agents), so that anyone can understand how and why they coexist in one process.
9. As a carrier calling in through the local REPL, I want the full call flow (verify → send code → check code → search loads → negotiate → book → transfer/hang-up) to behave exactly as before, so that the prototype demonstrates the same conversation.
10. As a carrier caller, I want the OTP, negotiation, and booking guards (verified code required, only the server-accepted rate bookable, max negotiation rounds, ceiling never revealed) to keep being enforced server-side, so that they apply no matter which protocol the agent uses.
11. As a carrier caller, I want call-control actions (hang up, transfer to a human rep) to remain instant, agent-local tools, so that ending or escalating a call never depends on a network round trip to the middleware.
12. As the production voice workflow, I want the six HTTP endpoints to keep their exact request/response contract and API-key authentication, so that I continue to work with zero changes.
13. As the production voice workflow, I want to keep working even if the local MCP experiment is disabled or broken, so that the learning exercise cannot take down the real consumer.
14. As an operator, I want the MCP surface to enforce the same shared API key as the HTTP surface, so that mounting a second protocol does not create an unauthenticated path to booking.
15. As an operator, I want to deploy with the same image and port as today, so that the change requires no new infrastructure or run scripts.
16. As any MCP-compatible client (IDE agent, desktop assistant), I want to connect to the middleware over streamable HTTP and list the six tools, so that I can drive carrier verification, load search, negotiation, and booking without a custom HTTP client.
17. As an MCP client, I want tool results to carry the same reason codes and response shapes as today's HTTP responses, so that my prompt logic does not need to special-case the transport.
18. As a broker reviewing calls, I want post-call summaries to be extracted into the existing SQLite call log with the same schema, so that downstream review workflows are unaffected.
19. As a developer, I want the summary extraction to use LangChain structured output with a Pydantic model, so that the summary schema is type-checked instead of a raw json_object response parsed by hand.
20. As a developer performing the refactor, I want the existing HTTP test suites to stay green while logic moves into the core layer, so that I have continuous confidence the refactor preserved behavior.
21. As a developer, I want an in-memory MCP test suite that pins tool identity, auth enforcement, and a full offer-evaluation → booking round trip, so that the new protocol surface is covered without network fixtures.
22. As a maintainer, I want the vendor brand name replaced by generic names (service titled "Carrier Middleware", external system called "the production voice workflow") in the service title, docs, comments, and prompt text, so that the prototype no longer implies a specific vendor.
23. As a developer, I want the agent to keep generating a per-call run id and passing it to every tool call, so that session-scoped state (OTP, negotiation) behaves identically to today.
24. As a developer, I want the bilingual (EN/ES) system prompt preserved verbatim apart from the rename, so that the local replica stays faithful to the production workflow.
25. As a developer experimenting locally, I want the MCP server to remain launchable over stdio as well, so that I can try Claude-Desktop-style setups without affecting the mounted streamable-HTTP server.

## Implementation Decisions

- **Agent framework:** LangChain's canonical agent API (`create_agent`) with a chat model constructed provider-agnostically (`init_chat_model`) from the existing `AGENT_MODEL` setting; temperature 0.3 as today. LangGraph is explicitly not used — the workflow is a single tool-calling loop.
- **MCP implementation:** the official Python MCP SDK (FastMCP). Third-party MCP tooling (OpenAPI-to-MCP generators, the open-mcp.org standard) is explicitly not used.
- **Topology — one process, two protocols:** the FastMCP server is mounted on the existing ASGI application at a dedicated path (the MCP endpoint is `CARRIER_API_BASE` + `/mcp`) using the streamable-HTTP transport, with its session manager wired into the application lifespan. All in-memory state (OTP store, negotiation state, bookings) lives in the core layer and is shared by both protocols — there is no forked second state. Stdio remains available as an alternate transport for local experimentation.
- **Three-layer structure:** a framework-agnostic core (FMCSA verification, OTP lifecycle and TTL/pruning, TMS TCP client and record parsing, load search/filtering/formatting, the round-limited negotiation engine, booking guards and duplicate-booking protection, and the state stores) sits under two thin adapters: the HTTP handlers (Pydantic request models, API-key auth, response dicts) and the MCP tools (MCP arguments → core → result). Business logic exists in exactly one place; adapters contain no business rules.
- **HTTP contract preservation:** the six business endpoints keep their exact paths, request models, response shapes, reason codes, and authentication. The existing HTTP test suites are the acceptance gate for the core extraction and must stay green without modification.
- **Tool identity:** the six MCP tools carry names, descriptions, and parameter names identical to the current agent tool definitions (`verify_carrier`, `send_verification_code`, `check_verification_code`, `search_loads`, `evaluate_offer`, `book_load`). FastMCP derives tool names from function names and descriptions from docstrings — that is the synchronization mechanism with the verbatim prompt. The MCP surface exposes exactly these six tools.
- **Call control stays agent-side:** `hang_up` and `transfer_to_rep` remain local agent tools (decorated Python functions), not MCP tools — call control belongs to the agent, not the system of record. The agent's tool list is the MCP-loaded tools plus these two locals, passed to a single agent.
- **MCP authentication:** a small transport-level (ASGI) middleware wraps the mounted MCP application and enforces the same shared API key as the HTTP surface — same accepted header names, same constant-time comparison — because host-application dependencies do not apply to mounted sub-applications. The agent's MCP client sends the key as a static header from the existing `CARRIER_API_KEY`. MCP OAuth / auth-server support is out of scope as overkill.
- **Client integration:** the agent loads MCP tools with the standard LangChain–MCP adapter (`load_mcp_tools`) against `{CARRIER_API_BASE}/mcp`. No new environment variables: `CARRIER_API_KEY` doubles as the MCP credential, and `CARRIER_API_BASE` is the middleware's base URL. One REPL process handles one call, so one MCP session per process, closed cleanly on exit.
- **Structured summary:** a Pydantic `CallSummary` model plus LangChain structured output (`with_structured_output`), using `EXTRACT_MODEL` at temperature 0, written to the existing SQLite call-log table with an unchanged schema. The extraction function accepts an optional injected model so tests can supply a fake.
- **Rename:** the service title becomes "Carrier Middleware"; references to the external production system become "the production voice workflow" in the service title, README, comments, and prompt text. No file or module renames; the agent script and its launcher keep their names and the same environment contract.
- **Dependencies:** the requirements file gains the MCP SDK, LangChain core, the OpenAI provider integration, and the LangChain–MCP adapter — exact-pinned to the versions verified working. Pre-existing dependencies stay unpinned, matching current repo style.
- **Deployment and docs:** the Dockerfile is unchanged (same image, same port; the MCP path simply appears). The env template gains documentation of the MCP path. The README gains an architecture section covering the two protocols, the shared core, and how to point an external MCP client at the server.
- **Run-id semantics unchanged:** the agent generates a per-call run id and passes it with every tool call; the core keys OTP and negotiation state on it exactly as today. `MAX_BUY` never leaves the server on either protocol.

## Testing Decisions

- **What makes a good test here:** tests assert only external behavior at a seam — an HTTP request yielding the expected response at the HTTP seam, and `list_tools`/`call_tool` results at the MCP seam. No test inspects core internals, private functions, or prompt text.
- **Seam 1 (existing, highest for the system of record):** the three existing HTTP suites, which drive the ASGI application in-process through the test client, using the existing test-carrier and test-load dev flags. Unchanged; green after the core extraction is the definition of a safe refactor. Prior art: the existing suites themselves.
- **Seam 2 (new, highest for the MCP surface):** one in-memory MCP client-session suite against the FastMCP application (the SDK's in-memory transport — the same pattern the SDK's own tests use). It asserts: (a) tool identity — exactly the six expected tools with the expected names; (b) auth — requests without a valid key are rejected and with the valid key are accepted, exercised at the transport the middleware protects; (c) behavior — `call_tool` round trips covering a test-carrier verification and a full offer-evaluation → booking flow that pins the booking guards (unverified OTP, wrong rate, rejected load) through the MCP surface.
- **No automated seam for the LangChain agent REPL:** deliberately no fake-LLM end-to-end harness. The agent's correctness decomposes into tool behavior (Seam 2), endpoint behavior (Seam 1), and the interactive loop (covered by the manual smoke run with a test carrier in the build plan). Adding a third seam would cost more than it is worth in a learning exercise.
- **One micro-seam (function parameter, not architectural):** the summary-extraction function accepts an injected model; one fake-model test pins the `CallSummary` → SQLite shape without any LLM call.
- **Prior art:** the in-process ASGI test-client style of the existing suites, and the MCP SDK's documented in-memory session pattern for the new suite.

## Out of Scope

- Changes to the production voice workflow itself — it keeps consuming the HTTP contract unchanged.
- LangGraph, multi-agent orchestration, conversation memory/checkpointing, RAG, or any new LLM features.
- Real SMS delivery (OTP remains mocked), TMS protocol changes, or any TMS-side work.
- MCP OAuth / auth-server, multi-tenant or per-client keys, and any auth model beyond the existing shared API key.
- New environment variables, new ports, new services, new run scripts, or CI changes.
- Persistent or multi-worker state — the documented limitation (in-memory state, single worker) remains.
- End-to-end tests that spawn a real HTTP server for MCP clients; UI/TUI work; file renames or repository reorganization beyond the new core modules and MCP module.
- Changes to the SQLite schema or to consumers of the call log.
- Pinning pre-existing (non-AI) dependencies.
- Docker image changes.

## Further Notes

- The core extraction is the riskiest step: logic moves out of ~1,500 lines of handler-interleaved code. The three HTTP suites run after each extraction chunk; a red suite means stop and fix.
- The mounted streamable-HTTP MCP app requires its session manager to be running as part of the application lifespan — a small, standard addition to the host app's lifespan configuration.
- The auth middleware must wrap the mounted MCP sub-application at the ASGI level, because host-application dependency injection does not reach mounted sub-applications.
- FastMCP tool descriptions come from docstrings: keeping the docstrings byte-identical to the current tool descriptions is what keeps the verbatim prompt and the string-match guard intact — the in-memory MCP suite's tool-identity assertion is the automated check on that.
- The LangChain–MCP adapter's connection is per-process; the agent opens one session for the life of one call and closes it on exit (including on hang-up and on transfer).
- The rename touches the service title, the README, code comments, and any prompt text that names the vendor; it does not touch the tool names, which are pinned by the tool-identity decision.
- This is a learning exercise: the implementation should favor the canonical public APIs (agent API, MCP adapter, FastMCP + ASGI mount, structured output, ASGI middleware) over clever minimization, with the seams above as the only places the design bends for testability.
