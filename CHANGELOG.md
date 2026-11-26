# Changelog

## 2026-11-26
- **Administrators page:** add admins by Google sign-in or a temporary password (forced change at first sign-in), remove admin rights with last-admin and `ADMIN_EMAILS` guards, reset passwords; all audited.
- **Learner progress (Admin):** learner × lab grid with completion, steps, answers and activity; per-learner timeline; CSV export; conversations filtered by learner.
- **Guided labs (now the default):**
  - 21 labs in 8 modules, each with objectives, step-by-step hints, a worked answer and a defence write-up;
  - answer policy (*always*, *after_steps*, *after_completion*, *hidden*) and answer-view counts in **Admin → Labs & CTF**;
  - CTF scoring is still available as a mode.
- **CTF platform:**
  - challenge board, challenge pages, flag submission, scoreboard;
  - personal HMAC flags with shared-flag detection, first-blood bonus, hints with point costs, event window and scoreboard freeze;
  - admin CTF control and scenario records.
- **11 new challenges:**
  - the Vault Keeper prompt-injection ladder (6 levels);
  - an agent-to-agent Treasury heist (Aiden → internal Treasury agent);
  - markdown image exfiltration (Nova), a rogue binding loan offer (Leo), denial of wallet (Echo), FAQ poisoning (Faye).
- **MCP Python SDK 2.3:** both MCP servers migrated from `FastMCP` to `MCPServer`. Our own MCP client (`app/integrations/mcp_client.py`) replaces `langchain-mcp-adapters`, which does not support mcp 2 yet. Pinned tool fingerprints stay valid.
- **MeriDrive:** 30 fictional bank files (PDF, Word, email, CSV, JSON, YAML) with poisoned documents.
  - New file, payment and screening tools.
  - New agents: Ledger (accounts payable), Hira (HR screening) and the Ops copilot; Aria can now read the customer's own documents.
  - Four new challenges, plus admin pages for the drive, vendor payments and screenings.
- **Admin account at install:** password sign-in for admins, **My account** page, `app.cli create-admin`, login lockout.
- **No-Docker deployment:** `install.sh` installs and deploys natively (systemd, apt Postgres and Caddy, Python virtualenvs) in one `curl | bash` command. GitHub Actions deploys over SSH.
- **Open-source agent stack:**
  - LangGraph runtime, LiteLLM gateway, Langfuse tracing, LLM Guard scanners;
  - MCP support: a poisoned partner server with rug pull, and the bank's own MCP endpoint;
  - admin pages for each.
- **First release:** the Meridian Bank site, RAG assistant and refund agent (vulnerable and hardened), the X-ray panel, the admin portal and the vector-database lab.
