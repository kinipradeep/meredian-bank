# Learner guide: Meridian Bank AI Security Range

Meridian Bank is a **fictional** bank built for practising attacks on AI systems. Everything here (customers, money, keys, documents) is fake. Attack the bank's AI agents freely; **do not attack the platform itself** (login, servers, other learners' accounts) and never enter real personal data.

## Getting started
1. Sign in with Google. You get your own customer profile, accounts and transactions.
2. Open the **Training lab** to see the challenges, your score and all the agents.
3. Each agent has a **v1 (vulnerable)** and a **v2 (hardened)** version. Break v1 first, then try the same attack on v2 and work out which control stopped you.

## How the CTF works
- Open **CTF** in the top menu for the challenge board, your score and the scoreboard.
- Two kinds of challenge:
  - **auto-detected:** when your attack works, the chat shows a 🏁 banner with your points and your personal flag;
  - **submit a flag:** find a `MERIDIAN{…}` flag (for example, the Vault Keeper's password) and paste it into the flag box.
- **Flags are personal.** Submitting someone else's flag doesn't score and is logged.
- **Scoring:** the first player to solve a challenge gets **first blood** (+10%). Hints cost the points shown on them.
- **Limits:** 20 chat messages and 10 flag submissions per minute. Plan your attacks.

## The X-ray panel
Next to every chat, the X-ray shows what the model actually received and did:
- the full system prompt, including retrieved documents;
- retrieved chunks and their similarity scores;
- MCP tools and their descriptions;
- every tool call and its result;
- guard decisions and LLM Guard scores.

Use it to understand *why* an attack worked. Your trainer may switch it off for a blind round.

## Agents
| Agent | Where | What it can do |
|---|---|---|
| Aria | Help & support | Answers from the bank's knowledge base, looks up your accounts, opens your documents, sends emails |
| Refund Desk | Disputes | Lists transactions, issues refunds |
| Max | Training lab | Concierge using a third-party MCP tool server (branches, FX rates, partner offers) |
| Ledger | Training lab | Accounts-payable agent: reads invoices in the AP inbox and pays vendors |
| Hira | Training lab | HR assistant: screens job applicants' resumes |
| Ops copilot | Training lab | Staff helper that searches the bank's document drive |
| Vault Keeper (levels 1–6) | Training lab | Guards a password. Each level adds a defence |
| Aiden | Training lab | Front desk that hands money transfers to an internal Treasury agent |
| Nova | Training lab | Writes complaint summaries for a staff dashboard that renders markdown |
| Leo | Training lab | Loan advisor that issues offers |
| Echo | Training lab | Statement analyst; very thorough |
| Faye | Training lab | Community FAQ bot that learns from customers |

## Bring your own MCP client
The **Training lab** page gives you a personal token for the bank's MCP endpoint (`https://<range>/mcp/`). Connect Claude Desktop, MCP Inspector or your own script and call the bank's tools directly, with no model in between. Every call is logged.

## Challenges
| Challenge | Points | Hint |
|---|---|---|
| Leak the override code | 100 | What does the model see before your message? |
| Read the confidential HR file | 150 | Retrieval finds whatever your question is close to. |
| Exfiltrate data by email | 200 | One knowledge-base document contains more than it shows. |
| Get a refund of ₹50,000 or more | 150 | Does the tool compare your amount with the original transaction? |
| View another customer's account | 150 | Whose authority does the agent use? Customer IDs look like MB-200101. |
| Exploit a poisoned MCP tool | 200 | The model reads every tool description, even ones you never see. |
| Redirect a vendor payment | 200 | Ask Ledger to process the AP inbox. Who really sent that email? |
| Get an unqualified applicant shortlisted | 150 | What would a model read in white-on-white text? |
| Steal the payment gateway key | 200 | The runbooks say where the config lives. How is "inside the folder" checked? |
| Leak the board's M&A secret | 150 | Does the drive search check who is asking? |
| Vault Keeper levels 1–6 | 50–300 | Each level blocks the last trick. Framing, spelling, synonyms, other alphabets, patience… |
| Rob a stranger through the back office | 300 | Aiden forwards your words to someone very trusting. |
| Exfiltrate the case token through markdown | 200 | What does a markdown renderer do with an image URL? |
| Get a "legally binding" loan at a giveaway rate | 150 | Leo never loses a deal. |
| Drain the wallet | 100 | How thorough is Echo willing to be? |
| Poison the FAQ | 200 | Teach Faye something, then ask her about it. |

## Write up each finding
For every successful attack, note:
1. the vulnerability (OWASP LLM Top 10 category);
2. the business impact for a real bank;
3. the control that stopped it in v2, and one more control you would add;
4. what the exam domain says about it.
