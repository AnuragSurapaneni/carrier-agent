# 01: Rename vendor brand to generic names

**What to build:** The repo no longer implies a specific vendor. The service is titled "Carrier Middleware" (API title and the root-endpoint response), and the external production system is referred to as "the production voice workflow" in docs, code comments, docstrings, and prompt text. The agent script and its launcher keep their names and the same environment contract. No file or module renames anywhere; only string content changes.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [ ] A case-insensitive search of the repo (excluding `.venv/`, `agent/.venv/`, `.git/`) finds no occurrence of the old vendor name
- [ ] The FastAPI service title and the `GET /` response both use "Carrier Middleware"
- [ ] Comments and docstrings that named the vendor now say "the production voice workflow"
- [ ] The agent's docstrings/comments are updated with the same vocabulary; the system prompt text is unchanged apart from any vendor naming inside it
- [ ] All three HTTP test suites pass unmodified
- [ ] No file or module was renamed; the agent script name, launcher, and env contract are unchanged

## Comments

- 2026-10-29: Resolved — merged into `int/langchain-mcp-agent` at `08a483b` (two-axis code review passed; branch fix pass `f3a2eda`).
