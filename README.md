# CollabAce AI Cyber Range · Meridian Bank

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

**Docs:** [Trainer guide](docs/TRAINER_GUIDE.md) (session plan and solutions) · [Learner guide](docs/LEARNER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## Open-source components

| Component | Role | Managed in the admin portal |
|---|---|---|
| **LangGraph** | Agent runtime. Every agent compiles to `input_guard → retrieve → model ⇄ tools → output_guard`. Its controls decide what each node does. | **Agents** (deploy, clone, import/export, kill switch); **Agent runtime** (graph and per-agent controls) |
| **LiteLLM** | One gateway for Claude, OpenAI, Groq, Gemini and Ollama. Provider keys are stored encrypted, and it handles virtual keys with budgets and spend logs. | **LLM gateway**: add or test models, create budgeted keys, view spend. A link opens LiteLLM's own UI. |
| **Langfuse** (Cloud) | Tracing of every LangGraph run: prompts, retrieval, tool calls, tokens, latency and cost, per learner and session. | **Tracing**: on/off, keys, recent traces with deep links. The X-ray panel links each turn to its trace. |
| **LLM Guard** | ML scanners (prompt injection, secrets, invisible text, PII, banned output) on the input, on retrieved/tool context and on the output. | **Guardrails**: service status, scanner selection, per-agent switches, a playground, recent guard decisions |
| **MCP** (official MCP Python SDK v2) | Agents load tools from MCP servers. A malicious demo server shows tool poisoning and rug pulls, and the bank exposes its own tools over MCP. | **MCP servers**: register servers, inspect the tool descriptions the model sees, pin approved fingerprints, trigger the rug pull, configure the bank endpoint, see every MCP call |
| **MeriDrive** (bank file estate) | 30+ fictional bank files the agents read: statements and KYC records, invoices, a vendor master, resumes, runbooks, secret configs, board minutes. Formats include PDF, Word, email, CSV, JSON, YAML and Markdown. Poisoned files hide instructions in white 1-pt text, hidden Word runs, HTML comments or zero-width characters. | **MeriDrive**: browse, upload, edit access levels, see *what the model sees vs what a human sees*, re-index, reset for a new cohort. Also **Vendor payments** (approve/reject) and **Candidate screenings**. |
| **Labs & CTF engine** | **21 guided labs in 8 modules** with steps, answers and defences; or run them as a CTF. Personal HMAC flags (sharing doesn't work), auto-detected and submit-a-flag challenges, first blood, hints with point costs, scoreboard with freeze, event start/end | **Learner progress**: learner × lab grid, per-learner timeline, CSV export. **Labs & CTF**: mode, answer policy, event window, per-challenge points/difficulty/hints, submission log with shared-flag detection, reset. **Scenario records**: treasury transfers, loan offers, FAQ moderation |
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
| **Redirect a vendor payment**: a hidden instruction in an invoice plus a business-email-compromise email | Ledger v1 (accounts payable) | `strip_hidden_content`, quarantine of injected files, payee verification against the vendor master, approval above ₹1 lakh |
| **Shortlist an unqualified applicant**: white text in a resume; another resume tries to email the salary file | Hira v1 (HR screening) | hidden-text stripping, folder sandbox (`safe_paths`), email allowlist |
| **Steal the gateway key**: `../` path traversal out of the runbooks folder | Ops copilot v1 | `safe_paths` (normalise before checking), output secret filter |
| **Leak board M&A minutes** via unfiltered document search | Ops copilot v1 | `file_user_rights` (search only what the user may read) |
| **Vault Keeper ladder**: 6 levels of escalating guardrails around a password | Vault Keeper 1–6 | prompt rule → literal filter → keyword block → fuzzy decode → 4-char decode check (beaten by multi-turn slicing) |
| **Agent-to-agent heist**: the front desk forwards your words to a trusting Treasury agent | Aiden v1 | structured delegation, identity propagation |
| **Markdown image exfiltration** (insecure output handling) | Nova v1 | markdown sanitising, token redaction |
| **Rogue "binding" loan offer** | Leo v1 | policy checks on the tool |
| **Denial of wallet** | Echo v1 | step limit, per-turn token budget |
| **FAQ / knowledge poisoning** through customer feedback | Faye v1 | moderation queue, reviewed-only retrieval |
| **Another customer's statement**: `../MB-200101/...` from the learner's own folder | Aria v1 | `safe_paths` + `file_user_rights` |

The seeded agents use the **offline demo model**, so every attack works before you add a single API key. Add models in the LLM gateway, then use **Agents → Apply to all agents**.

---

## Deploy: GitHub → VM, no Docker (Linode or any Ubuntu 24.04 VM)

**Sizing:** a **Linode 8 GB** (Shared CPU) runs everything. Use 4 GB if you skip LLM Guard (`--no-llm-guard`); LLM Guard alone needs about 2–3 GB of RAM once its models load.

### Easiest: the single-file installer
One file holds the installer and all of the range's code, so the VM never needs to reach GitHub. Your settings go in one file, `range.env`.

1. **Get the two files.** Download `meridian-range-installer.sh` and `range.env.example` from the latest **Actions** run on `main` (*Artifacts → meridian-range-installer*), or build them yourself with `scripts/make-installer.sh` (they land in `dist/`).
2. **Fill in the settings.** Copy `range.env.example` to `range.env` and set at least `ADMIN_EMAIL`. Optionally set `DOMAIN` (leave it blank to use `<VM-IP>.sslip.io`, which needs no DNS), `ADMIN_PASSWORD`, `GROQ_API_KEY` with `AGENT_MODEL=groq-gpt-oss-20b`, and the Google sign-in keys. Every setting is explained in the file.
3. **Copy both files to the VM** (Ubuntu 24.04, as root):
   ```bash
   scp meridian-range-installer.sh range.env root@<VM-IP>:
   ```
4. **Run it:**
   ```bash
   ssh root@<VM-IP>
   sudo bash meridian-range-installer.sh
   ```
   It takes 10–20 minutes and ends by printing the admin login. Run it without a `range.env` and it creates one for you to fill in.

**To update**, run `sudo meridian-update` on the VM. It pulls the latest `main` from GitHub and reinstalls with `/etc/airange/range.env`. **To change a setting**, edit `/etc/airange/range.env`, then run `sudo meridian-update`. Your data, secrets and admin password are kept on every run.

**What `range.env` does:** provider keys go to `/etc/airange/providers.env`, which only the LiteLLM gateway can read. `AGENT_MODEL` points every agent at that model. `LLM_GUARD=auto` installs LLM Guard only on VMs with 6 GB or more, and turns it off on smaller ones to save memory. `ADMIN_PASSWORD` only sets a password when the account doesn't have one yet; after that, change it in **Admin → My account**.

### Or: from GitHub, one command
#### 1. Create the VM and point DNS
Create an Ubuntu 24.04 Linode. Add A records for `range.example.com` and `gateway.range.example.com`, both pointing to its IP.

#### 2. Install and deploy, as root on the VM
```bash
curl -fsSL https://raw.githubusercontent.com/kinipradeep/meredian-bank/main/scripts/install.sh \
  | bash -s -- range.example.com you@example.com
#   options: --admin-password PASS   --google-id ID --google-secret SECRET   --no-llm-guard   --with-ollama
#            --repo URL --branch NAME
```
`install.sh` sets up the VM and **deploys the code from GitHub**:
1. installs Postgres 16, Caddy, Python 3.12, a firewall (22/80/443), fail2ban, unattended upgrades and swap;
2. creates the `airange` service user and fetches the repo from GitHub into `/opt/airange`;
3. generates `/opt/airange/.env` with **every secret randomised**, then creates the `airange` and `litellm` databases;
4. builds three virtualenvs (app + Chroma, LiteLLM, LLM Guard) and starts these systemd services:
   `airange-app`, `airange-chroma`, `airange-litellm`, `airange-llm-guard`, `airange-mcp-demo`;
5. configures Caddy for `range.example.com` and `gateway.range.example.com`, sets up nightly backups and waits for the app to report healthy;
6. **creates the admin account** for the email you passed: it uses `--admin-password` if given, otherwise it generates a password (shown once, never stored);
7. prints the admin login, the other logins and the optional GitHub Actions secrets.

**Admin account.** Sign in at `https://range.example.com/login` → *Administrator sign-in*. This works before Google sign-in is set up. The same email also gets admin rights when it signs in with Google. Change your own password under **Admin → My account**. Add more administrators under **Admin → Administrators** (see below). Re-running the installer keeps the existing password unless you pass `--admin-password`. Five wrong attempts lock that email from that IP for 15 minutes. If you forget the password, reset it on the VM:
```bash
sudo -u airange bash -c 'cd /opt/airange && set -a && . ./.env && .venv/bin/python -m app.cli create-admin --email you@example.com'
```

**To update, run the same command again.** It pulls the latest `main` from GitHub, reinstalls and restarts everything, and keeps your `.env` and data. (If you ever make the repo private, pass an SSH `--repo git@github.com:...` URL; the script then prints a read-only deploy key to add on GitHub.)

#### 3. Google sign-in
In Google Cloud Console, go to **APIs & Services**. Set up the OAuth consent screen (External, scopes `openid email profile`), then create an **OAuth client ID** of type Web application with:
- origin `https://range.example.com`
- redirect URI `https://range.example.com/auth/callback`

Put the ID and secret in `/opt/airange/.env`, then run `systemctl restart airange-app`.

#### 4. Optional: deploy automatically on every push
Re-running `install.sh` is enough to update. For hands-off updates, add the secrets the installer printed (`VM_HOST`, `VM_USER=airange`, `VM_SSH_KEY`, `VM_KNOWN_HOSTS`) under GitHub → *Settings → Secrets and variables → Actions*.

From then on, every push to **main** runs `.github/workflows/ci-cd.yml`:
- tests, script syntax checks, config validation and an import check;
- then an SSH deploy that runs `scripts/deploy.sh` on the VM. It pulls the commit, reinstalls Python packages only when `requirements.txt` changed, restarts only what changed, and waits for `/healthz`.

The `airange` user's sudo rights are limited to restarting its own services and reloading Caddy. You can add required reviewers to the `production` environment to approve each deploy.

#### 5. Tracing (Langfuse Cloud)
Create a free project at [cloud.langfuse.com](https://cloud.langfuse.com) (or `us.cloud.langfuse.com`) and copy its API keys into **Admin → Tracing**. The project ID is the part after `/project/` in the project's URL. Traces contain learner prompts, which is another reason the bank's data is fictional.

#### 6. Add real models

**Option A: API keys in an env file (recommended).** The gateway already defines `claude-haiku`, `claude-sonnet`, `gemini-flash-lite`, `groq-gpt-oss-20b`, `groq-gpt-oss-120b` and `groq-gpt-oss-20b` in `deploy/litellm/config.yaml`. They only need keys:

```bash
sudo nano /etc/airange/providers.env      # ANTHROPIC_API_KEY=… GEMINI_API_KEY=… GROQ_API_KEY=…
sudo systemctl restart airange-litellm
```

`install.sh` creates that file (root-only, mode 600) from `deploy/providers.env.example`. Only the LiteLLM service loads it. The app, LLM Guard and the MCP demo server never get the keys, so an agent that a learner tricks into dumping its environment or reading files can't leak them. Leave a key blank to skip that provider. **Test** each model under **Admin → LLM gateway**, then point agents at one:

```bash
cd /opt/airange && sudo -u airange .venv/bin/python -m app.cli set-agent-model --list
sudo -u airange .venv/bin/python -m app.cli set-agent-model --model claude-haiku                       # all agents
sudo -u airange .venv/bin/python -m app.cli set-agent-model --model groq-gpt-oss-20b --agents aria-v1,refund-v1
```

(Or set each agent's endpoint to *LiteLLM gateway* and its model name under **Admin → Agents**.)

**Option B: from the admin portal.** Go to **Admin → LLM gateway → Add a model**. For example:
- name `claude-haiku`, model string `anthropic/claude-haiku-5-5`, plus your Anthropic key;
- or `groq-gpt-oss-20b` = `groq/openai/gpt-oss-20b`, which is cheap and has a free tier. (Groq retired its Llama 3.x models on 16 Aug 2026.)

**Test** it, then **Generate key** with a budget and "use for agents" ticked, and switch agents to the *LiteLLM gateway* endpoint with that model name.

| Where | Login |
|---|---|
| Range and admin portal | Admin: email + password from the installer (*Administrator sign-in*). Learners: Google |
| `gateway.<domain>/ui` | `LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD` from `.env` |
| Langfuse Cloud | your Langfuse account |

**Day-to-day:** `journalctl -u airange-app -f` (or another service name) for logs; `systemctl restart airange-<name>` to restart; `/opt/airange/scripts/backup.sh` to back up by hand.

## Look and branding
The training platform is branded **CollabAce AI Cyber Range** (a white bar with the CollabAce logo, plus Labs, OWASP map, Agents & MCP and Admin). Below it, **Meridian Bank** has its own look:
- a modern, mobile-first Indian retail-banking style with an account card, quick actions and transactions as a list;
- a bottom tab bar on phones;
- the Mukta typeface (Ek Type, SIL Open Font License, bundled in `app/static/fonts`).

Change the platform name or upload another logo (PNG, JPEG or WebP) in **Admin → Settings → Branding**. The bundled logo is in `app/static/brand`.

## OWASP Top 10 mapping
Every lab is mapped to the **OWASP Top 10 for LLM Applications 2026**:
- **On each lab page:** a card with the lab's main risk and any secondary risks, a one-line definition, and *why it matters* in a bank. After the lab is completed (or its answer opened), a section shows *how the hardened version mitigates it*.
- **Labs board:** each lab's main category as a chip.
- **`/labs/owasp`:** all 10 categories, with the labs covering each and your progress.

The mapping and texts live in `app/seed/owasp.py`.

## Model failover
If an agent's model is slow, rate-limited, missing its key, retired or down, the turn moves to the next model in the failover list. If every model fails, the built-in offline model answers, so learners always get a reply. A failing model is skipped for a cool-down (2 minutes, or 15 for a missing key or retired model). The X-ray panel shows which model answered and why the others were skipped.

Configure it in **Admin → LLM gateway → Failover**:
- the time limit per model (default 20 s);
- the order of models to try (default `groq-gpt-oss-20b, gemini-flash-lite, groq-gpt-oss-120b, claude-haiku`);
- the offline fallback;
- a button to try every model again.

**Refusals:** safety-tuned models (gpt-oss, Claude, Gemini) often refuse a lab's attack outright, which would make the lab impossible. When a real model refuses on a lab's **target** agent, the turn is redone on the built-in local model, which plays each level as designed. The X-ray shows the refusal and which model answered. Hardened agents that are only a lab's "repeat on hardened" step keep their refusals, since refusing is their job. You can turn this off and rename the local model (default *Meridian Lite (local small model)*) on the same page.

The lab matrix (`scripts/lab_matrix.py`) measures the real model alone. Add `--as-learners` to include these fallbacks and see what learners actually get.

## Privacy, acceptable use and terms
The range has a **privacy notice**, an **acceptable use policy** (what learners may and may not attack) and **terms of use** at `/legal/…`, linked from the sign-in page and every footer. Everyone accepts them once before using the range.

- **Before your first class,** fill in **Admin → Policies**: who runs the range, a contact email, a grievance officer (India's DPDP Act), where the server is hosted, and how many days to keep conversations (default 180; 0 = keep).
- The privacy notice is generated from the live setup. It names the AI providers your agents actually use and says whether Google sign-in and Langfuse are on, so it changes when you switch models.
- **Publish a new version** after a meaningful change, and everyone accepts again at their next page load.
- Learners can **download their data** as JSON from *Your data*. Admins can **delete a user and all their data** from **Admin → Users**.
- Old conversations and activity are purged every night at 02:45 (`python -m app.cli purge-data`).

The text is a sound starting point for a training range, not legal advice. Have it reviewed if you sell courses or train an organisation's staff.

## More administrators
**Admin → Administrators** lists every admin and lets you:
- **Add an administrator** by email, choosing how they sign in:
  - *Google:* they get admin access the next time they sign in with Google using that address;
  - *temporary password:* shown to you once; they must set their own password before they can use the panel.
- **Remove admin** rights. Access ends on their next click and they keep a normal learner account. You can't remove yourself or the last admin.
- Issue a **new temporary password**, or switch an admin to **Google only**.

Admins listed in `ADMIN_EMAILS` in `.env` are tagged *.env* and can only be removed there, since they'd be promoted again at their next sign-in. Every change is recorded in the Activity log.

## Labs
**Labs** in the top menu opens 21 guided labs in 8 modules (prompt injection, sensitive data, indirect injection, excessive agency, MCP supply chain, agent-to-agent, output handling, resource abuse). Each lab has objectives, a link to the vulnerable v1 agent, free step-by-step hints, a worked answer, the hardened v2 agent to retry against, and a defence write-up. Labs complete automatically when the attack works.

Trainers can switch to a scored CTF (personal flags, first blood, scoreboard, event window) and set when answers become visible in **Admin → Labs & CTF**. See the [trainer guide](docs/TRAINER_GUIDE.md).

## Teaching flow
1. **Vector DB tour.** Open the chunks, the embedding bars and the 2D map. The side-by-side search shows the HR file reaching a customer when the search has no access filter.
2. **Prompt leakage, then sensitive data via RAG.** Use Aria v1, then repeat against v2.
3. **Indirect injection.** Ask Aria v1 about partner offers. Check the Outbox for the leaked email, and open the trace in Langfuse to replay it node by node.
4. **Excessive agency and confused deputy.** Use Refund Desk v1 vs v2, and look at the human approval queue.
5. **MCP supply chain.**
   - On **MCP servers → partner-services**, look at what the model sees: `partner_offers` hides an `<IMPORTANT>` block.
   - Ask Max v1 for partner offers: it emails the customer's data out.
   - Pin the clean tools, **trigger the rug pull**, and ask both Max versions for a USD rate. v1 is hijacked; v2 refuses the changed tool.
6. **Bypass the model entirely.** Learners create a token on the Agents & MCP page (`/lab`) and connect Claude Desktop or MCP Inspector to `https://range.example.com/mcp/`. Switch the endpoint's profile between `refund-v1` and `refund-v2` and watch the calls in Admin → MCP.
7. **Defence in depth.** Clone an agent and switch one control off at a time (regex guard, LLM Guard, isolation, pinning) to see which attack comes back.

These exercises map to the ISC2 AI security domains, the OWASP Top 10 for LLM Applications (LLM01, 02, 03, 06, 07, 08) and MITRE ATLAS.

## The bank's files (MeriDrive)
Agents work on real-looking documents, not just chat:

| Folder | Contents | Access |
|---|---|---|
| `public/` | brochure (PDF), schedule of charges, branch directory, FX rates (CSV) | public |
| `customers/<id>/` | statements (PDF/CSV), KYC profiles (JSON), a loan sanction letter. Every learner also gets their own `statement-latest.csv` | owner only |
| `finance/ap/inbox/` | invoices (PDF, DOCX); **Nimbus** hides a payment redirect; **acme-bank-change-request.eml** is a look-alike-domain BEC | finance |
| `staff/` | runbooks, an incident report, the **vendor master** (approved payee accounts), a support ticket queue with zero-width obfuscation | staff / finance |
| `hr/` | compensation, performance reviews, a job description, 4 resumes (2 poisoned) | hr |
| `it/` | `payments-gateway.env`, `deploy-config.yaml` with fake live secrets | it |
| `board/` | Q3 board minutes (Project Kestrel acquisition) | board |

Files are stored on the VM under `/var/lib/airange/files`, cataloged in Postgres with the extracted text, and indexed into the Chroma collection `meridian-drive`. Agents reach them only through `list_files`, `read_file` and `search_files`, never through the real filesystem. To add your own scenarios, upload PDFs or Word files in **Admin → MeriDrive**, or edit `scripts/dev/generate_drive.py` and regenerate (`pip install reportlab python-docx`).

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
`scripts/lab_matrix.py` runs on the VM before a class: each lab's attack against the vulnerable and hardened agent, on the real models you name (see the trainer guide).

## Operations
- **Backups:** `scripts/backup.sh` runs nightly. It dumps both databases (range, LiteLLM) and archives Chroma into `/opt/airange/backups`, keeping 14 days. Copy them off the VM, or enable Linode Backups.
- **Cost control:** set LiteLLM virtual-key budgets, `MAX_MESSAGES_PER_USER_PER_DAY`, the per-minute rate limit, per-agent `max_tokens` and `max_steps`, and the kill switch.
- **Reset a cohort:** use Admin → Challenges → Reset, Users → Reset bank, and MCP → Revoke all tokens.
- **Never change `LITELLM_SALT_KEY`** after first start. It encrypts the provider keys stored in the gateway.

## Security notes
- The **bank** is deliberately vulnerable; the **platform** is not. Platform controls:
  - Google OAuth with verified email and an optional domain allowlist; local admin passwords hashed with scrypt and protected by a login lockout; secure, HttpOnly session cookies; CSRF tokens on every form
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
scripts/         install.sh · gen-env.sh · deploy.sh · backup.sh · smoke_test.py · lab_matrix.py
.github/workflows/ci-cd.yml
```
**Add a native tool:** write a function with `@tool(...)` in `app/agents/tools.py`; it appears in the agent form automatically. **Add an external tool:** register its MCP server in the portal and tick it on an agent.
