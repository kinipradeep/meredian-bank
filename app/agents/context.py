from dataclasses import dataclass, field

ROLE_ACCESS = {
    "customer": ["public"],
    "staff": ["public", "staff"],
    "hr": ["public", "staff", "hr"],
    "admin": ["public", "staff", "hr"],
}

DEFAULT_CONTROLS = {
    "auto_retrieve": True,               # retrieve before answering (classic RAG)
    "top_k": 4,
    "permission_aware_retrieval": False,  # filter chunks by the user's role
    "input_guard": False,                 # block injection-looking user input; quarantine poisoned chunks
    "context_isolation": False,           # wrap retrieved text as untrusted data
    "output_filter": False,               # redact secrets, card numbers, system-prompt leakage
    "on_behalf_of": False,                # tools act only with the signed-in customer's rights
    "refund_cap": None,                   # hard maximum per refund (INR)
    "require_approval_over": None,        # refunds above this go to a human approval queue
    "email_allowlist": False,             # send_email only to the customer or the bank's domain
    # LLM Guard (open-source scanners, run as a separate service)
    "llm_guard_input": False,             # scan the user's message
    "llm_guard_context": False,           # scan retrieved chunks and MCP tool results for injection
    "llm_guard_output": False,            # scan/redact the final answer
    # MeriDrive files and payments
    "file_scope": None,                   # folder the agent may use, e.g. finance/ap/ or customers/{customer_id}/
    "safe_paths": False,                  # normalise paths BEFORE the folder check (blocks ../ traversal)
    "file_user_rights": False,            # act with the signed-in user's file rights, not a service account
    "strip_hidden_content": False,        # give the model only the text a human can see (no white/hidden text)
    "payee_verification": False,          # pay only to the account in the vendor master; no duplicate payments
    "payment_approval_over": None,        # payments above this go to a human approval queue
    # CTF scenarios
    "input_keyword_block": None,          # comma-separated words that block the message (vault levels)
    "block_secret_literal": False,        # block replies containing the protected secret verbatim
    "secret_fuzzy_block": False,          # ...or spelled out, reversed or base64-encoded
    "secret_decode_check": False,         # ...or any 4+ character piece of it, in any of those encodings or NATO
    "sanitize_markdown": False,           # strip images/links to non-bank domains from replies
    "turn_token_budget": None,            # stop calling the model after this many tokens in one turn
    "a2a_structured": False,              # delegate to other agents with structured fields, never raw user text
    "a2a_identity": False,                # downstream agents act with the original customer's identity
    "offer_policy_check": False,          # loan offers must respect rate floors, caps and wording rules
    "faq_moderation": False,              # learner FAQ submissions wait for human review
    # MCP
    "mcp_scan_descriptions": False,       # drop MCP tools whose descriptions contain hidden instructions
    "mcp_pin_tools": False,               # only allow MCP tools whose description matches the approved fingerprint
    "max_steps": 4,
    "temperature": 0.3,
    "max_tokens": 800,
}


@dataclass
class AgentContext:
    db: object
    user: object
    agent: object
    controls: dict
    events: list = field(default_factory=list)      # tool + guard events for the X-ray view
    flags: list = field(default_factory=list)       # detector signals, e.g. "cross_account"
    retrieved: list = field(default_factory=list)
    poisoned_tools: list = field(default_factory=list)  # MCP tools loaded with hidden instructions
    mcp_loaded: list = field(default_factory=list)      # MCP tools offered to the model this turn
    files_read: list = field(default_factory=list)      # drive paths opened this turn
    secrets: list = field(default_factory=list)         # player flags rendered into this agent's prompt
    tool_calls: int = 0
    usage_total: int = 0
    faq_hits: list = field(default_factory=list)        # learner-written FAQ entries retrieved this turn

    @property
    def access(self) -> list[str]:
        return ROLE_ACCESS.get(self.user.role, ["public"])

    def flag(self, name: str, evidence: str = ""):
        self.flags.append({"flag": name, "evidence": evidence[:300]})

    def event(self, kind: str, **data):
        self.events.append({"kind": kind, **data})
