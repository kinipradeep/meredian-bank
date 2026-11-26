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

    @property
    def access(self) -> list[str]:
        return ROLE_ACCESS.get(self.user.role, ["public"])

    def flag(self, name: str, evidence: str = ""):
        self.flags.append({"flag": name, "evidence": evidence[:300]})

    def event(self, kind: str, **data):
        self.events.append({"kind": kind, **data})
