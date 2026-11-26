# Meridian Bank · AI Security Range

A self-hosted training range for AI vulnerabilities, built from open-source components. Learners sign in with Google and attack **Meridian Bank**, a fictional bank with a RAG support assistant, an agentic refunds desk and a concierge that uses third-party MCP tools. Every agent has a **vulnerable** and a **hardened** version. Admins manage everything (models, keys, agents, MCP servers, guardrails, traces, vector database) from one portal.

```
                         ┌──────────────── Caddy (HTTPS) ─────────────────┐
  range.<domain>         │  app: FastAPI · LangGraph agents · admin portal │──► Postgres  (users, agents, bank data, logs)
  range.<domain>/mcp/    │       bank MCP endpoint (MCP Python SDK)        │──► Chroma    (vector database)
                         └───────┬───────────────┬───────────────┬─────────┘
                                 │               │               │
  gateway.<domain>      LiteLLM gateway     LLM Guard API     MCP servers (partner demo + any you register)
                        Claude · OpenAI ·   (ML scanners)
                        Groq · Gemini ·
                        Ollama
  langfuse.<domain>     Langfuse v4 (traces of every node, prompt, tool call, cost)
```

## Open-source components

| Component | Role | Managed in the admin portal |
|---|---|---|
| **LangGraph** | Agent runtime. Every agent compiles to `input_guard → retrieve → model ⇄ tools → output_guard`. Its controls decide what each node does. | **Agents** (deploy, clone, import/export, kill switch); **Agent runtime** (graph and per-agent controls) |
| **LiteLLM** | One gateway for Claude, OpenAI, Groq, Gemini and Ollama. Provider keys are stored encrypted, and it handles virtual keys with budgets and spend logs. | **LLM gateway**: add or test models, create budgeted keys, view spend. A link opens LiteLLM's own UI. |
| **Langfuse** | Tracing of every LangGraph run: prompts, retrieval, tool calls, tokens, latency and cost, per learner and session. | **Tracing**: on/off, keys, recent traces with deep links. The X-ray panel links each turn to its trace. |
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

## Deploy: GitHub → VM (Linode or any Ubuntu 24.04 VM)

**Sizing:** use a **Linode 8 GB** (Shared CPU) for everything. The core stack alone (`COMPOSE_PROFILES=` empty) runs on 4 GB. LLM Guard downloads about 2 GB of models on first use, and Langfuse adds ClickHouse, Redis and MinIO.

### 1. Push this project to a private GitHub repo
```bash
git remote add origin git@github.com:kinipradeep/meredian-bank.git
git push -u origin main
```

### 2. Create the VM and point DNS
Create an Ubuntu 24.04 Linode. Add A records for `range.example.com`, `gateway.range.example.com`, `langfuse.range.example.com` and `minio.range.example.com`, all pointing to its IP (or one wildcard record).

### 3. Bootstrap the VM (one time)
```bash
ssh root@<vm-ip>
curl -fsSL https://raw.githubusercontent.com/kinipradeep/meredian-bank/main/scripts/bootstrap-vm.sh -o bootstrap.sh
#   (private repo? scp scripts/bootstrap-vm.sh to the VM instead)
bash bootstrap.sh git@github.com:kinipradeep/meredian-bank.git range.example.com you@example.com
```
The script installs Docker, a firewall (22/80/443 only), fail2ban, swap and a `deploy` user. It then:
1. prints a **read-only deploy key** for you to add under GitHub → repo → *Settings → Deploy keys*;
2. clones the repo into `/opt/airange` and generates `.env` with **every secret randomised** (`scripts/gen-env.sh`);
3. prints the **Actions secrets** to add (`VM_HOST`, `VM_USER`, `VM_SSH_KEY`, `VM_KNOWN_HOSTS`).

### 4. Google OAuth
In Google Cloud Console, go to **APIs & Services**. Set up the OAuth consent screen (External, scopes `openid email profile`) and add test users or publish the app. Then create an **OAuth client ID** of type Web application with:
- origin `https://range.example.com`
- redirect URI `https://range.example.com/auth/callback`

Put the ID and secret in `/opt/airange/.env`.

### 5. First start
```bash
sudo -u deploy bash -c 'cd /opt/airange && scripts/deploy.sh'
```
Caddy obtains TLS certificates automatically. Sign in at `https://range.example.com` with an email in `ADMIN_EMAILS`.

### 6. Continuous deployment
`.github/workflows/ci-cd.yml` runs on every push and pull request. It runs the tests, validates the compose file for every profile and builds the image. On **main** it then SSHes into the VM and runs `scripts/deploy.sh`, which pulls the commit, rebuilds, restarts and waits for `/healthz`. If the VM secrets aren't set, the deploy job skips itself. You can add a protection rule (required reviewers) to the `production` environment under *Settings → Environments*.

### 7. Add real models
Go to **Admin → LLM gateway → Add a model**. For example, name `claude-haiku`, model string `anthropic/claude-haiku-5-5`, plus your Anthropic key. Or `groq-llama-8b` = `groq/llama-3.1-8b-instant`, which is cheap and gullible. Click **Test**, then **Generate key** with a budget and "use for agents" ticked. Finally, switch agents to the *LiteLLM gateway* endpoint with that model name.

| Where | Login |
|---|---|
| Range and admin portal | Google (emails in `ADMIN_EMAILS` become admins) |
| `gateway.<domain>/ui` | `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD` from `.env` |
| `langfuse.<domain>` | `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` from `.env` (sign-up disabled) |

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
# full stack on a laptop:
scripts/gen-env.sh localhost you@example.com  # then set ENVIRONMENT=development, DEV_LOGIN_ENABLED=true, COMPOSE_PROFILES=
docker compose up -d --build
```
`scripts/smoke_test.py` drives a running dev instance end to end: every admin page and every attack, against vulnerable and hardened agents.

## Operations
- **Backups:** `scripts/backup.sh` dumps all three databases (range, LiteLLM, Langfuse) and archives Chroma, keeping 14 days. Add it to the `deploy` user's crontab.
- **Cost control:** set LiteLLM virtual-key budgets, `MAX_MESSAGES_PER_USER_PER_DAY`, the per-minute rate limit, per-agent `max_tokens` and `max_steps`, and the kill switch.
- **Reset a cohort:** use Admin → Challenges → Reset, Users → Reset bank, and MCP → Revoke all tokens.
- **Never change `LITELLM_SALT_KEY`** after first start. It encrypts the provider keys stored in the gateway.

## Security notes
- The **bank** is deliberately vulnerable; the **platform** is not. Platform controls:
  - Google OAuth with verified email and an optional domain allowlist; secure, HttpOnly session cookies; CSRF tokens on every form
  - a strict CSP, encrypted secrets (Fernet in the app, salt key in LiteLLM), admin-only routes and an audit log of every admin action
  - internal-only Postgres, Chroma, LLM Guard and the MCP demo server
- `send_email` never sends anything: it writes to the Outbox table. Seed "secrets" are fake. Never load real data.
- Learner prompts go to whichever model providers you configure. Tell learners so.
- LiteLLM and Langfuse are exposed on their own subdomains behind their own logins. If you don't need them public, remove those blocks from the `Caddyfile`; the app reaches both over the internal network.

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
deploy/          litellm/config.yaml · llm-guard/scanners.yml · postgres/init.sh
scripts/         bootstrap-vm.sh · gen-env.sh · deploy.sh · backup.sh · smoke_test.py
.github/workflows/ci-cd.yml   docker-compose.yml   Dockerfile   Caddyfile
```
**Add a native tool:** write a function with `@tool(...)` in `app/agents/tools.py`; it appears in the agent form automatically. **Add an external tool:** register its MCP server in the portal and tick it on an agent.
