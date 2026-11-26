# Learner guide: Meridian Bank AI Security Range

Meridian Bank is a **fictional** bank built for practising attacks on AI systems. Everything here (customers, money, keys, documents) is fake. Attack the bank's AI agents freely; **do not attack the platform itself** (login, servers, other learners' accounts) and never enter real personal data.

## Getting started
1. Sign in with Google. You get your own customer profile, accounts and transactions.
2. Open the **Training lab** to see the challenges, your score and all the agents.
3. Each agent has a **v1 (vulnerable)** and a **v2 (hardened)** version. Break v1 first, then try the same attack on v2 and work out which control stopped you.

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

## Write up each finding
For every successful attack, note:
1. the vulnerability (OWASP LLM Top 10 category);
2. the business impact for a real bank;
3. the control that stopped it in v2, and one more control you would add;
4. what the exam domain says about it.
