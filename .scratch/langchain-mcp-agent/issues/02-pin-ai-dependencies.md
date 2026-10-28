# 02: Add exact-pinned AI dependencies

**What to build:** The dependency manifest gains the AI stack so the learning environment is reproducible weeks from now: the official Python MCP SDK (FastMCP), LangChain core (providing the canonical agent API and `init_chat_model`), the OpenAI provider integration, and the LangChain–MCP adapter. Each is exact-pinned to the versions verified working in a fresh local virtualenv. Pre-existing dependency lines stay unpinned, matching current repo style. The Dockerfile is unchanged (same image, same port).

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] `requirements.txt` contains the MCP SDK, LangChain core, the OpenAI provider integration, and the LangChain–MCP adapter, each exact-pinned to a specific version
- [ ] Pre-existing dependency lines are untouched (still unpinned)
- [ ] A fresh virtualenv created from `requirements.txt` installs cleanly on this machine's Python (3.10); the pinned versions must also support Python 3.12, which the Docker image uses
- [ ] Each new package imports cleanly in that virtualenv (import smoke check, including the agent API and the MCP adapter entry points)
- [ ] The Dockerfile is unchanged

## Comments

- 2026-10-29: Resolved — merged into `int/langchain-mcp-agent` at `7c8e613` (two-axis code review passed; branch fix pass `f3a2eda`).
