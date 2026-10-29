# 06: Structured call summary

**What to build:** Post-call transcript summarization moves to LangChain structured output: a Pydantic `CallSummary` model consumed via `with_structured_output` on `EXTRACT_MODEL` at temperature 0, so the summary schema is type-checked instead of a raw `json_object` response parsed by hand. The summary is written to the existing SQLite call-log table with an unchanged schema and the same fields (MC number, outcome, sentiment, load id, agreed rate, language, notes), so downstream review workflows are unaffected. The extraction function accepts an optional injected model so tests can supply a fake; one fake-model test pins the CallSummary → SQLite shape without any LLM call (the spec's single micro-seam — a function parameter, not an architectural seam).

**Blocked by:**
- 02: Add exact-pinned AI dependencies
- 05: Rebuild the agent on LangChain over MCP

**Status:** resolved

- [ ] The extraction uses LangChain structured output with a Pydantic model (no hand-parsed `json_object`)
- [ ] Summaries are written to the existing SQLite call-log table with the unchanged schema and the same fields
- [ ] `EXTRACT_MODEL` is used at temperature 0
- [ ] The extraction function accepts an optional injected model
- [ ] One fake-model test pins the CallSummary → SQLite shape with no LLM call
- [ ] The existing test suites (HTTP + MCP) remain green

## Comments

- 2026-10-29: Resolved — merged into `int/langchain-mcp-agent` at `05b3db4` (two-axis code review passed; branch fix pass `f3a2eda`).
