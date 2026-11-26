# Meridian Bank · AI Security Range

A self-hosted training range for AI vulnerabilities, built from open-source components. Learners sign in with Google and attack **Meridian Bank**, a fictional bank with a RAG support assistant, an agentic refunds desk and a concierge that uses third-party MCP tools. Every agent has a **vulnerable** and a **hardened** version. Admins manage everything (models, keys, agents, MCP servers, guardrails, traces, vector database) from one portal.

```
  https://range.<domain>        Caddy (apt, automatic HTTPS)
          │
          ▼
  airange-app        FastAPI · LangGraph agents · admin portal · bank MCP endpoint (/mcp/)   127.0.0.1:8080
    ├─ PostgreSQL 16 (apt)            users, agents, bank data, logs; LiteLLM's database      127.0.0.1:5432
    ├─ airange-chroma                 vector database                                          127.0.0.1:8000
    ├─ airange-litellm                LLM gateway → Claude · OpenAI · Groq · Gemini · Ollama  127.0.0.1:4000  (UI: gateway.<domain>)
    ├─ airange-llm-guard              LLM Guard API scanners                                   127.0.0.1:8001
    ├─ airange-mcp-demo               malicious partner MCP server                             127.0.0.1:8765
    └─ Langfuse Cloud                 traces (free Hobby tier; no local install)
```
No Docker: everything is installed natively on one Ubuntu 24.04 VM, with Python virtualenvs and systemd services.

## Open-source components

| Component | Role | Managed in the admin portal |
|---|---|---|
| **LangGraph** | Agent runtime. Every agent compiles to `input_guard → retrieve → model ⇄ tools → output_guard`. Its controls decide what each node does. | **Agents** (deploy, clone, import/export, kill switch); **Agent runtime** (graph and per-agent controls) |
| **LiteLLM** | One gateway for Claude, OpenAI, Groq, Gemini and Ollama. Provider keys are stored encrypted, and it handles virtual keys with budgets and spend logs. | **LLM gateway**: add or test models, create budgeted keys, view spend. A link opens LiteLLM's own UI. |
| **Langfuse** (Cloud) | Tracing of every LangGraph run: prompts, retrieval, tool calls, tokens, latency and cost, per learner and session. | **Tracing**: on/off, keys, recent traces with deep links. The X-ray panel links each turn to its trace. |
| **LLM Guard** | ML scanners (prompt injection, secrets, invisible text, PII, banned output) on the input, on retrieved/tool context and on the output. | **Guardrails**: service status, scanner selection, per-agent switches, a playground, recent guard decisions |
| **MCP** (MCP Python SDK + `langchain-mcp-adapters`) | Agents load tools from MCP servers. A malicious demo server shows tool poisoning and rug pulls, and the bank exposes its own tools over MCP. | **MCP servers**: register servers, inspect the tool descriptions the model sees, pin approved fingerprints, trigger the rug pull, configure the bank endpoint, see every MCP call |
| **Chroma + fastembed** | Vector database and local embeddings | **Vector database**: chunking, embeddings, raw vectors, filtered vs unfiltered search, 2D map, playground |

## Attacks and defences

| Challenge | Vulnerable agent | Defence shown by the hardened agent |
|---|---|---|
| Leak the override code | Aria v1 | input guard, output filter, LLM Guard |
| Read the HR file (RAG) | Aria v1 | permission-aware retrieval |
| Indirect prompt injection → email exfiltration | Aria v1 (poisoned document) | chunk quarantine, context isolation, email allowlist |
| ₹50,000+ refund (excessive agency) | Refund Desk v1 | refund cap, human approval queue |
| Another customer's account (confused deputy) | Refund Desk v1, or the bank MCP endpoint | on-behalf-of authorisation |
| **Poisoned MCP tool** | Max v1 (partner MCP server) | description scanning, **tool pinning** (also blocks rug pulls), LLM Guard on tool results |

The seeded agents use the **offline demo model**, so every attack works before you add a single API key. Add models in the LLM gateway, then use **Agents → Apply to all agents**.

---

## Deploy: GitHub → VM, no Docker (Linode or any Ubuntu 24.04 VM)

**Sizing:** a **Linode 8 GB** (Shared CPU) runs everything. Use 4 GB if you skip LLM Guard (`--no-llm-guard`); LLM Guard alone needs about 2–3 GB of RAM once its models load.

### 1. Create the VM and point DNS
Create an Ubuntu 24.04 Linode. Add A records for `range.example.com` and `gateway.range.example.com`, both pointing to its IP.

### 2. Install and deploy: one command, as root on the VM
```bash
curl -fsSL https://raw.githubusercontent.com/kinipradeep/meredian-bank/main/scripts/install.sh \
  | bash -s -- range.example.com you@example.com
#   options: --google-id ID --google-secret SECRET   --no-llm-guard   --with-ollama   --repo URL --branch NAME
```
`install.sh` sets up the VM and **deploys the code from GitHub**:
1. installs Postgres 16, Caddy, Python 3.12, a firewall (22/80/443), fail2ban, unattended upgrades and swap;
2. creates the `airange` service user and fetches the repo from GitHub into `/opt/airange`;
3. generates `/opt/airange/.env` with **every secret randomised**, then creates the `airange` and `litellm` databases;
4. builds three virtualenvs (app + Chroma, LiteLLM, LLM Guard) and starts these systemd services:
   `airange-app`, `airange-chroma`, `airange-litellm`, `airange-llm-guard`, `airange-mcp-demo`;
5. configures Caddy for `range.example.com` and `gateway.range.example.com`, sets up nightly backups and waits for the app to report healthy;
6. prints your logins and the optional GitHub Actions secrets.

**To update, run the same command again.** It pulls the latest `main` from GitHub, reinstalls and restarts everything, and keeps your `.env` and data. (If you ever make the repo private, pass an SSH `--repo git@github.com:...` URL; the script then prints a read-only deploy key to add on GitHub.)

### 3. Google sign-in
In Google Cloud Console, go to **APIs & Services**. Set up the OAuth consent screen (External, scopes `openid email profile`), then create an **OAuth client ID** of type Web application with:
- origin `https://range.example.com`
- redirect URI `https://range.example.com/auth/callback`

Put the ID and secret in `/opt/airange/.env`, then run `systemctl restart airange-app`.

### 4. Optional: deploy automatically on every push
Re-running `install.sh` is enough to update. For hands-off updates, add the secrets the installer printed (`VM_HOST`, `VM_USER=airange`, `VM_SSH_KEY`, `VM_KNOWN_HOSTS`) under GitHub → *Settings → Secrets and variables → Actions*.

From then on, every push to **main** runs `.github/workflows/ci-cd.yml`:
- tests, script syntax checks, config validation and an import check;
- then an SSH deploy that runs `scripts/deploy.sh` on the VM. It pulls the commit, reinstalls Python packages only when `requirements.txt` changed, restarts only what changed, and waits for `/healthz`.

The `airange` user's sudo rights are limited to restarting its own services and reloading Caddy. You can add required reviewers to the `production` environment to approve each deploy.

### 5. Tracing (Langfuse Cloud)
Create a free project at [cloud.langfuse.com](https://cloud.langfuse.com) (or `us.cloud.langfuse.com`) and copy its API keys into **Admin → Tracing**. The project ID is the part after `/project/` in the project's URL. Traces contain learner prompts, which is another reason the bank's data is fictional.

### 6. Add real models
Go to **Admin → LLM gateway → Add a model**. For example:
- name `claude-haiku`, model string `anthropic/claude-haiku-5-5`, plus your Anthropic key;
- or `groq-llama-8b` = `groq/llama-3.1-8b-instant`, which is cheap and gullible.

**Test** it, then **Generate key** with a budget and "use for agents" ticked, and switch agents to the *LiteLLM gateway* endpoint with that model name.

| Where | Login |
|---|---|
| Range and admin portal | Google (emails in `ADMIN_EMAILS` become admins) |
| `gateway.<domain>/ui` | `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD` from `.env` |
| Langfuse Cloud | your Langfuse account |

**Day-to-day:** `journalctl -u airange-app -f` (or another service name) for logs; `systemctl restart airange-<name>` to restart; `/opt/airange/scripts/backup.sh` to back up by hand.

## Teaching flow
1. **Vector DB tour.** Open the chunks, the embedding bars and the 2D map. The side-by-side search shows the HR file reaching a customer when the search has no access filter.
2. **Prompt leakage, then sensitive data via RAG.** Use Aria v1, then repeat against v2.
3. **Indirect injection.** Ask Aria v1 about partner offers. Check the Outbox for the leaked email, and open the trace in Langfuse to replay it node by node.
4. **Excessive agency and confused deputy.** Use Refund Desk v1 vs v2, and look at the human approval queue.
5. **MCP supply chain.**
   - On **MCP servers → partner-services**, look at what the model sees: `partner_offers` hides an `<IMPORTANT>` block.
   - Ask Max v1 for partner offers: it emails the customer's data out.
   - Pin the clean tools, **trigger the rug pull**, and ask both Max versions for a USD rate. v1 is hijacked; v2 refuses the changed tool.
6. **Bypass the model entirely.** Learners create a token in the Training lab and connect Claude Desktop or MCP Inspector to `https://range.example.com/mcp/`. Switch the endpoint's profile between `refund-v1` and `refund-v2` and watch the calls in Admin → MCP.
7. **Defence in depth.** Clone an agent and switch one control off at a time (regex guard, LLM Guard, isolation, pinning) to see which attack comes back.

These exercises map to the ISC2 AI security domains, the OWASP Top 10 for LLM Applications (LLM01, 02, 03, 06, 07, 08) and MITRE ATLAS.

## Local development
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
pytest -q                                   # unit tests: no services needed
# run locally: Postgres + Chroma + the app (LiteLLM, LLM Guard, MCP demo are optional)
chroma run --path ./.chroma --port 8000 &
python -m app.mcp_servers.partner &
DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/airange DEV_LOGIN_ENABLED=true uvicorn app.main:app --reload
```
`scripts/smoke_test.py` drives a running dev instance end to end: every admin page and every attack, against vulnerable and hardened agents.

## Operations
- **Backups:** `scripts/backup.sh` runs nightly. It dumps both databases (range, LiteLLM) and archives Chroma into `/opt/airange/backups`, keeping 14 days. Copy them off the VM, or enable Linode Backups.
- **Cost control:** set LiteLLM virtual-key budgets, `MAX_MESSAGES_PER_USER_PER_DAY`, the per-minute rate limit, per-agent `max_tokens` and `max_steps`, and the kill switch.
- **Reset a cohort:** use Admin → Challenges → Reset, Users → Reset bank, and MCP → Revoke all tokens.
- **Never change `LITELLM_SALT_KEY`** after first start. It encrypts the provider keys stored in the gateway.

## Security notes
- The **bank** is deliberately vulnerable; the **platform** is not. Platform controls:
  - Google OAuth with verified email and an optional domain allowlist; secure, HttpOnly session cookies; CSRF tokens on every form
  - a strict CSP, encrypted secrets (Fernet in the app, salt key in LiteLLM), admin-only routes and an audit log of every admin action
  - Postgres, Chroma, LLM Guard and the MCP demo server listen on 127.0.0.1 only, and systemd hardening (`NoNewPrivileges`, `ProtectSystem`) applies to every service
- `send_email` never sends anything: it writes to the Outbox table. Seed "secrets" are fake. Never load real data.
- Learner prompts go to whichever model providers you configure. Tell learners so.
- The LiteLLM UI is exposed at `gateway.<domain>` behind its own login. If you don't need it public, delete that block from `deploy/caddy/Caddyfile`; the app reaches the gateway on localhost.
- LLM Guard is archived upstream (0.3.16 is the final release). It still works; pinning keeps installs reproducible.

## Project layout
```
app/
  agents/        runner.py (LangGraph graph) · tools.py · guards.py · detectors.py · context.py
  integrations/  litellm.py · langfuse_client.py · llmguard.py · mcp_client.py · settings.py
  mcp_servers/   bank.py (mounted at /mcp/) · partner.py (malicious demo server)
  providers/     endpoints → LangChain chat models (gateway / OpenAI-compatible / offline mock)
  rag/           chunking · embeddings · Chroma store · ingest
  routers/       bank · chat · admin · admin_stack (gateway, tracing, guardrails, MCP, runtime)
  seed/  templates/  static/
deploy/          systemd/*.service · caddy/Caddyfile · litellm/config.yaml · llm-guard/scanners.yml
scripts/         install.sh · gen-env.sh · deploy.sh · backup.sh · smoke_test.py
.github/workflows/ci-cd.yml
```
**Add a native tool:** write a function with `@tool(...)` in `app/agents/tools.py`; it appears in the agent form automatically. **Add an external tool:** register its MCP server in the portal and tick it on an agent.
