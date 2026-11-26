"""Database models. Everything here is training data for a fictional bank."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- platform

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    picture: Mapped[str] = mapped_column(String(500), default="")
    role: Mapped[str] = mapped_column(String(20), default="customer")   # customer | staff | hr | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    customer_id: Mapped[str] = mapped_column(String(20), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mcp_token: Mapped[str] = mapped_column(String(80), default="", index=True)  # for the bank's MCP endpoint
    password_hash: Mapped[str] = mapped_column(String(200), default="")  # only local admin accounts have one
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)  # set for temporary passwords

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Setting(Base):
    """Key/value platform settings editable from the admin panel."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Provider(Base):
    """A model provider. API keys are encrypted at rest."""
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))          # openai_compat | mock
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    default_model: Mapped[str] = mapped_column(String(150), default="")
    models_hint: Mapped[str] = mapped_column(Text, default="")  # comma-separated suggestions for the UI
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Agent(Base):
    """A deployable agent definition (the 'agent framework' unit)."""
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    surface: Mapped[str] = mapped_column(String(20), default="lab")     # support | refunds | lab
    mode: Mapped[str] = mapped_column(String(20), default="vulnerable")  # vulnerable | hardened
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    model: Mapped[str] = mapped_column(String(150), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    collection: Mapped[str] = mapped_column(String(80), default="")     # Chroma collection name, optional
    tools: Mapped[list] = mapped_column(JSON, default=list)
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)       # slugs of McpServer rows
    controls: Mapped[dict] = mapped_column(JSON, default=dict)
    greeting: Mapped[str] = mapped_column(Text, default="")
    xray_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)          # kill switch
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    provider: Mapped[Provider | None] = relationship()


class McpServer(Base):
    """An external MCP tool server agents can use. `approved` pins tool-description fingerprints."""
    __tablename__ = "mcp_servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    url: Mapped[str] = mapped_column(String(400))
    transport: Mapped[str] = mapped_column(String(30), default="streamable_http")   # streamable_http | sse
    headers_enc: Mapped[str] = mapped_column(Text, default="")      # encrypted JSON of HTTP headers
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[dict] = mapped_column(JSON, default=dict)      # {tool_name: fingerprint}
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class KnowledgeDocument(Base):
    """A source document ingested into a vector collection."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    collection: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(200), default="")
    access: Mapped[str] = mapped_column(String(20), default="public")   # public | staff | hr
    content: Mapped[str] = mapped_column(Text)
    chunk_strategy: Mapped[str] = mapped_column(String(20), default="paragraph")
    chunk_size: Mapped[int] = mapped_column(Integer, default=600)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=80)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))   # user | assistant
    content: Mapped[str] = mapped_column(Text)
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    """Telemetry for every agent turn: who, which agent, which model, tools, guards, tokens."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    event: Mapped[str] = mapped_column(String(50))           # chat_turn | tool_call | guard | admin | login
    summary: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(50), default="")
    model: Mapped[str] = mapped_column(String(150), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Challenge(Base):
    __tablename__ = "challenges"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text)
    detector: Mapped[str] = mapped_column(String(40))   # matches a detector in agents/detectors.py
    points: Mapped[int] = mapped_column(Integer, default=100)
    hint: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # CTF metadata
    category: Mapped[str] = mapped_column(String(60), default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="easy")   # easy|medium|hard|insane
    kind: Mapped[str] = mapped_column(String(20), default="detector")     # detector (auto) | flag (submit)
    hints: Mapped[list] = mapped_column(JSON, default=list)               # [{"text": ..., "cost": 25}]
    agent_slug: Mapped[str] = mapped_column(String(80), default="")       # where to launch the attack
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    # guided-lab content (labs mode)
    module: Mapped[str] = mapped_column(String(120), default="")
    module_order: Mapped[int] = mapped_column(Integer, default=100)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)          # free, revealed one at a time
    answer: Mapped[str] = mapped_column(Text, default="")
    defence: Mapped[str] = mapped_column(Text, default="")
    hardened_slug: Mapped[str] = mapped_column(String(80), default="")


class ChallengeSolve(Base):
    __tablename__ = "challenge_solves"
    __table_args__ = (UniqueConstraint("user_id", "challenge_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    solved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    evidence: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[int] = mapped_column(Integer, default=0)          # awarded, incl. first-blood bonus
    first_blood: Mapped[bool] = mapped_column(Boolean, default=False)


class HintUnlock(Base):
    __tablename__ = "hint_unlocks"
    __table_args__ = (UniqueConstraint("user_id", "challenge_id", "hint_index"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"))
    hint_index: Mapped[int] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FlagSubmission(Base):
    __tablename__ = "flag_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    challenge_id: Mapped[int | None] = mapped_column(ForeignKey("challenges.id"), nullable=True)
    value: Mapped[str] = mapped_column(String(200))
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(200), default="")      # e.g. "another player's flag"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- the fictional bank

class BankCustomer(Base):
    __tablename__ = "bank_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(20), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    segment: Mapped[str] = mapped_column(String(40), default="Retail")
    pan_masked: Mapped[str] = mapped_column(String(20), default="")
    is_synthetic_other: Mapped[bool] = mapped_column(Boolean, default=False)  # other customers in the demo


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_no: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(30))     # Savings | Current | Credit Card
    balance: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(5), default="INR")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    txn_ref: Mapped[str] = mapped_column(String(30), unique=True)
    account_no: Mapped[str] = mapped_column(String(30), index=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)          # negative = debit
    category: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="posted")   # posted | disputed | refunded


class RefundRequest(Base):
    __tablename__ = "refund_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    txn_ref: Mapped[str] = mapped_column(String(30))
    account_no: Mapped[str] = mapped_column(String(30), default="")
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="issued")   # issued | pending_approval | rejected | approved


class OutboxEmail(Base):
    """Simulated email channel (nothing is ever sent). Used to demo exfiltration."""
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    to_addr: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- MeriDrive (the bank's file estate) and its workflows

class DriveFile(Base):
    """A file in the bank's document drive. Bytes live on disk under FILES_ROOT; text is cached here."""
    __tablename__ = "drive_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(400), unique=True, index=True)   # e.g. finance/ap/inbox/INV-1.pdf
    title: Mapped[str] = mapped_column(String(300), default="")
    access: Mapped[str] = mapped_column(String(20), default="staff")  # public|customer|staff|finance|hr|it|board
    owner: Mapped[str] = mapped_column(String(20), default="")        # customer ID for access=customer
    description: Mapped[str] = mapped_column(Text, default="")
    poisoned: Mapped[str] = mapped_column(Text, default="")           # trainer note: how it is poisoned
    tags: Mapped[list] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    text_full: Mapped[str] = mapped_column(Text, default="")
    text_visible: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(320), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class VendorPayment(Base):
    """Payments made (simulated) by the accounts-payable agent."""
    __tablename__ = "vendor_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    vendor: Mapped[str] = mapped_column(String(200))
    invoice_ref: Mapped[str] = mapped_column(String(100), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    account_no: Mapped[str] = mapped_column(String(40), default="")
    ifsc: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="paid")    # paid|pending_approval|blocked|approved|rejected
    verified: Mapped[bool] = mapped_column(Boolean, default=False)     # payee matched the vendor master
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CandidateScreening(Base):
    """Decisions recorded by the HR screening agent."""
    __tablename__ = "candidate_screenings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    candidate: Mapped[str] = mapped_column(String(200))
    score: Mapped[float] = mapped_column(Float, default=0)
    decision: Mapped[str] = mapped_column(String(30), default="")      # shortlist|reject|hold
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------- CTF scenario records

class TreasuryTransfer(Base):
    """Internal transfers executed by the back-office Treasury agent (agent-to-agent scenario)."""
    __tablename__ = "treasury_transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)   # originating customer
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)  # front-desk agent
    from_account: Mapped[str] = mapped_column(String(30))
    to_account: Mapped[str] = mapped_column(String(30))
    amount: Mapped[float] = mapped_column(Float)
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="done")   # done | refused
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoanOffer(Base):
    """'Binding' loan offers issued by the loan advisor agent."""
    __tablename__ = "loan_offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    product: Mapped[str] = mapped_column(String(30))
    amount: Mapped[float] = mapped_column(Float)
    rate: Mapped[float] = mapped_column(Float)
    tenure_months: Mapped[int] = mapped_column(Integer, default=60)
    terms: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="issued")   # issued | blocked | pending_approval
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FaqEntry(Base):
    """Community FAQ: official answers plus learner-submitted ones (the data-poisoning scenario)."""
    __tablename__ = "faq_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)   # None = official
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="published")   # official|published (auto, unreviewed)|pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
