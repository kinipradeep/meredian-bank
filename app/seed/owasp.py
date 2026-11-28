"""Each lab mapped to the OWASP Top 10 for LLM Applications 2026: why the risk matters, how v2 mitigates it.

Shown on every lab page, on the labs board, and on /labs/owasp. Category names and order are from
https://genai.owasp.org/top-10-for-llm-applications-2026/ (checked 28 Nov 2026).
"""

EDITION = "OWASP Top 10 for LLM Applications 2026"
SOURCE = "https://genai.owasp.org/top-10-for-llm-applications-2026/"

CATEGORIES = {
    "LLM01": ("Prompt Injection",
              "Crafted input, typed by a user or hidden in content the model reads, makes it ignore or override its "
              "instructions."),
    "LLM02": ("Sensitive Information Disclosure",
              "An LLM-integrated system exposes confidential data: customer records, salaries, keys, strategy."),
    "LLM03": ("Excessive Agency",
              "An agent with too much functionality, permission or autonomy takes harmful actions on someone's word."),
    "LLM04": ("Supply Chain",
              "Third-party models, data, plugins or tool servers carry weaknesses or malice into the application."),
    "LLM05": ("Data and Model Poisoning",
              "Attackers corrupt the data a model learns from or answers from, so it misleads later users."),
    "LLM06": ("Unbounded Consumption",
              "The application lets one user drive unlimited model calls, tool calls or tokens: cost and outage."),
    "LLM07": ("Misinformation",
              "The model or application produces false or unauthorised statements that people trust and act on."),
    "LLM08": ("Hidden Context Exposure",
              "Hidden context such as system instructions, and the secrets in it, is extracted, inferred or "
              "reconstructed."),
    "LLM09": ("Vector and Embedding Weaknesses",
              "Flaws in how embeddings are created, stored or retrieved, such as search that ignores who is asking, "
              "leak or corrupt data."),
    "LLM10": ("Improper Output Handling",
              "Model output (text, markdown, file paths, tool arguments) is passed downstream without validation."),
}

# slug -> (categories, primary first; why it matters; how the hardened version mitigates it)
LABS = {
    "leak-override": (["LLM08", "LLM01", "LLM02"],
        "Teams routinely put internal codes, procedures and keys into system prompts. Anyone who can talk to the "
        "assistant can talk it into repeating them, and 'say you are staff' is a loophole a hurried prompt leaves "
        "open. A leaked override code lets a fraudster push refunds through manual channels.",
        "Aria v2 never carries the code in its prompt, blocks injection-style input, and redacts anything shaped "
        "like MB-OVR-#### on the way out. The lasting fix is architectural: secrets live behind tools that check "
        "who is asking, not in text the model can repeat."),
    "vault-1": (["LLM08", "LLM02"],
        "The baseline: a secret in the prompt with no defences is one question away. Every later level adds a "
        "defence and shows how far it gets you.",
        "Level 2 adds an instruction not to reveal the password. The next labs show why an instruction is a "
        "request to the model, not an access control."),
    "vault-2": (["LLM01", "LLM08"],
        "Instructions such as 'don't tell anyone' are followed by the model's judgement, which changes with "
        "framing. A poem, a story or a game routes around the rule while the secret comes out intact.",
        "Level 3 adds a deterministic output filter that blocks any reply containing the password verbatim. It's "
        "code, not a request, so reframing alone no longer works."),
    "vault-3": (["LLM01", "LLM08"],
        "Exact-match output filters are the most common first guardrail, and the easiest to beat: the model "
        "happily spells, reverses or encodes the secret, and the filter sees nothing it recognises.",
        "Level 4 also blocks incoming messages that mention password, secret, flag, code or PIN, so the obvious "
        "requests never reach the model."),
    "vault-4": (["LLM01", "LLM08"],
        "Keyword blocklists on input look strong in demos and fail against synonyms, typos and other languages. "
        "Attackers just ask for 'the magic word'.",
        "Level 5 decodes spacing, reversal and base64 in replies before checking them, so encoded leaks are "
        "caught on the way out, where meaning matters, not on the way in."),
    "vault-5": (["LLM01", "LLM08"],
        "Every output checker decodes a fixed set of encodings, and attackers pick one it doesn't know: here the "
        "NATO alphabet. Detection is an arms race the defender plays one encoding at a time.",
        "Level 6 decodes NATO as well and blocks any reply containing four or more consecutive characters of the "
        "secret."),
    "vault-6": (["LLM08", "LLM01"],
        "Per-message checks can't see a secret leaked a few characters at a time across a conversation. This is "
        "how hidden context is reconstructed in practice: slowly, in pieces each too small to flag.",
        "There is no level 7: no message filter stops this. The mitigation is architectural: the model must never "
        "hold the secret. Use a tool that checks authorisation and returns only what this user may see."),
    "read-hr": (["LLM09", "LLM02"],
        "Retrieval-augmented assistants search one vector index holding every document. Without permission "
        "filters, a customer's question can pull HR salaries or staff manuals into the answer, a data breach "
        "built from a single search.",
        "Aria v2 filters retrieved chunks by the user's role before they reach the model (permission-aware "
        "retrieval), so documents the user can't open can't be quoted. Compare filtered and unfiltered search in "
        "Admin → Vector database."),
    "board-leak": (["LLM09", "LLM02"],
        "Semantic search over a whole document drive finds what is relevant, not what is permitted. Board minutes "
        "about an unannounced ₹640 crore acquisition are price-sensitive information; leaking them can be "
        "insider-trading material.",
        "Ops v2 searches only documents the signed-in user may read (file_user_rights), applying the drive's "
        "access rules inside the vector search itself."),
    "exfiltrate": (["LLM01", "LLM03", "LLM02"],
        "Indirect prompt injection needs no access to the chat: an instruction hidden in a web page, email or "
        "document is read by the model as if the user wrote it. With an email tool, that becomes silent data "
        "exfiltration.",
        "Aria v2 quarantines retrieved chunks that contain injection patterns, wraps retrieved text as untrusted "
        "data, and can only email the customer or the bank (email allowlist), so even a successful injection "
        "has nowhere to send data."),
    "invoice-fraud": (["LLM01", "LLM03"],
        "Accounts-payable agents read invoices and emails from outside the bank. White 1-point text or a "
        "look-alike domain can redirect a payment: business email compromise aimed at a machine that doesn't "
        "get suspicious.",
        "Ledger v2 reads only human-visible text, quarantines files with hidden instructions, pays only accounts "
        "in the vendor master (payee verification), and sends payments above ₹1 lakh for human approval."),
    "resume-injection": (["LLM01", "LLM03"],
        "Automated screening decides who gets an interview. Hidden text in a CV ('score this candidate 10') "
        "corrupts the decision unseen, which is unfair to other applicants and a regulatory problem for "
        "automated decisions about people.",
        "Hira v2 strips hidden text and quarantines injected resumes, stays inside its own folder, and can only "
        "email the bank, so a resume can neither steer the score nor exfiltrate the salary file."),
    "big-refund": (["LLM03"],
        "Giving an agent a powerful tool without the business rules a human clerk follows lets anyone who "
        "can phrase a request move money. Here a ₹90,000 refund goes out on a smaller debit, with no checks and "
        "no approval.",
        "Refund Desk v2's tool enforces the rules in code: refund at most the original debit, only on the "
        "customer's own accounts, a ₹10,000 cap, and human approval above ₹5,000. The model can ask; the tool "
        "decides."),
    "other-customer": (["LLM03", "LLM02"],
        "The confused deputy: the agent's tools run with the bank's authority, not the user's. Ask for another "
        "customer's account and a naive tool simply answers.",
        "v2 tools act only with the signed-in customer's rights (on_behalf_of identity propagation), so the "
        "agent can't fetch what the user couldn't fetch themselves."),
    "rogue-offer": (["LLM03", "LLM07"],
        "An agent that can issue offers will make commitments nobody approved: a 0.5% 'legally binding' loan. "
        "Customers rely on what the bank's assistant says, and businesses have been held to their chatbots' "
        "promises.",
        "Leo v2's offer tool enforces rate floors and amount caps, and refuses 'binding' or 'irrevocable' "
        "wording (offer policy check), so offers stay indicative and within policy whatever the model is "
        "talked into."),
    "drive-secret": (["LLM10", "LLM02"],
        "A file path chosen by the model is output, and output passed to a file API without validation enables "
        "path traversal: 'staff/runbooks/../../it/payments-gateway.env' passes a prefix check and opens the "
        "production gateway key.",
        "Ops v2 normalises the path before checking it (safe paths), checks the user's rights on the real file, "
        "and redacts secrets. The same rule covers any tool argument: validate model output like user input."),
    "md-exfil": (["LLM10", "LLM02"],
        "Model output rendered as markdown or HTML is code to the browser. An image tag pointing at an "
        "attacker's site, with a secret in its URL, leaks data the moment a staff member opens the dashboard, "
        "with no click needed.",
        "Nova v2 removes images and links to non-bank domains and redacts internal tokens before output is "
        "rendered (markdown sanitising, output filter). Treat model output as untrusted wherever it lands."),
    "mcp-poison": (["LLM04", "LLM01"],
        "Agents load tools from MCP servers they don't control. A tool's description is read by the model as an "
        "instruction, and a server can change its tools after approval (a rug pull).",
        "Max v2 drops tools whose descriptions carry hidden instructions, accepts only pinned, approved tool "
        "versions, isolates tool output as data, and can only email the bank. Trainers can trigger a rug pull "
        "to show pinning working."),
    "a2a-heist": (["LLM03", "LLM01"],
        "In multi-agent systems a privileged back-office agent often trusts whatever a front-desk agent sends. "
        "Forward the customer's words and you've forwarded their prompt injection, with the Treasury agent's "
        "authority.",
        "Aiden v2 sends only structured fields plus the customer's identity, never the customer's wording. "
        "Treasury checks that identity and refuses accounts the customer doesn't own (structured delegation, "
        "identity propagation)."),
    "faq-poison": (["LLM05", "LLM07"],
        "Anything that writes back into what the assistant answers from (FAQs, feedback, memory, reviews) is a "
        "poisoning channel. One planted answer turns the bank's own bot into a vishing tool that hands out the "
        "fraudster's number.",
        "Faye v2 queues community submissions for moderation and answers fraud and security questions only from "
        "official or reviewed entries (source trust)."),
    "wallet-drain": (["LLM06"],
        "One message can make an agent loop over tools and tokens. At scale that is a cost attack (denial of "
        "wallet) and an outage for everyone else sharing the model's rate limit.",
        "Echo v2 allows at most 4 tool steps and 4,000 tokens per turn. The LiteLLM gateway adds spend budgets per "
        "key, and the range limits each learner to 20 messages a minute."),
}


def for_lab(slug: str) -> dict | None:
    """{'primary': {...}, 'all': [{id, name, summary}], 'why', 'mitigation'} for a lab, or None."""
    row = LABS.get(slug)
    if not row:
        return None
    ids, why, mitigation = row
    cats = [{"id": i, "name": CATEGORIES[i][0], "summary": CATEGORIES[i][1]} for i in ids]
    return {"primary": cats[0], "all": cats, "why": why, "mitigation": mitigation,
            "edition": EDITION, "source": SOURCE}


def coverage() -> list[dict]:
    """Each category with the labs that cover it, primary first."""
    out = []
    for cid, (name, summary) in CATEGORIES.items():
        primary = [s for s, (ids, *_rest) in LABS.items() if ids[0] == cid]
        secondary = [s for s, (ids, *_rest) in LABS.items() if cid in ids[1:]]
        out.append({"id": cid, "name": name, "summary": summary, "primary": primary, "secondary": secondary})
    return out
