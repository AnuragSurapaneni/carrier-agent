# 07: Document the two-protocol architecture

**What to build:** Anyone reading the README can understand the new two-protocol architecture and how to use it. The README gains an architecture section covering: REST for the production voice workflow (unchanged contract, the six business endpoints) and MCP for agents (streamable HTTP at the MCP path), why the two protocols coexist in one process, the shared framework-agnostic core that keeps them from drifting (one system of record, two protocols), how to point any MCP-compatible client at the server (URL, the shared API key header), and that the local agent is now a LangChain agent consuming the middleware's tools via the standard MCP adapter. The env template documents the MCP path (and makes clear there are no new env variables — `CARRIER_API_KEY` doubles as the MCP credential, `CARRIER_API_BASE` is the base URL). The deployment story is unchanged: same image, same port, the MCP path simply appears.

**Blocked by:**
- 04: MCP server surface + in-memory test suite
- 05: Rebuild the agent on LangChain over MCP
- 06: Structured call summary

**Status:** ready-for-agent

- [ ] The README has an architecture section covering the two protocols, the shared core, and how to point an external MCP client at the server
- [ ] The README's project layout, agent, and dependency descriptions match the new reality (core modules, MCP module, LangChain agent over MCP)
- [ ] The env template documents the MCP path
- [ ] The Dockerfile is unchanged (same image, same port)
- [ ] All test suites (HTTP + MCP) remain green
