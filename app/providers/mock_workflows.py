"""Multi-step behaviour for the offline demo model, so document-based attacks work with no API key.

Like a real (gullible) model it: lists a folder, reads every file, then acts on what it read: paying
invoices, scoring applicants, emailing files. It obeys the LAST instruction it saw, so hidden text in a
document (or a change-of-bank-details email) overrides the visible details.
"""
import re

PAY_INTENT = re.compile(r"\b(pay|process|clear|settle|run)\b", re.I)
SCREEN_INTENT = re.compile(r"\b(screen|shortlist|rank|evaluate|review|score)\b", re.I)
LIST_HINT = re.compile(r"\b(invoices?|inbox|applicants?|candidates?|resumes?|cvs?|files?|documents?|statements?|folder)\b", re.I)
PATH_TOKEN = re.compile(r"((?:\.\./|[\w.-]+/)+[\w.-]+\.(?:pdf|docx|eml|csv|json|md|txt|env|yaml|yml)|(?:\.\./)+[\w./-]+)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.example")
AMOUNT = re.compile(r"(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)", re.I)
ACCOUNT = re.compile(r"account(?: number)?[:\s]+([\d ]{8,24}\d)", re.I)
IFSC = re.compile(r"IFSC[:\s]+([A-Z]{4}0[A-Z0-9]{6})")
OBEY = re.compile(r"(ignore previous|disregard|note to the ai|ai (payment|screening) assistant|system note)", re.I)


def _turn(messages):
    """Messages since the last user message, plus a map of tool_call_id -> (name, args)."""
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    turn = messages[start + 1:]
    calls = {}
    for m in turn:
        for tc in m.get("tool_calls") or []:
            calls[tc["id"]] = (tc["name"], tc.get("arguments") or {})
    results = [(calls.get(m.get("tool_call_id"), ("?", {}))[0], calls.get(m.get("tool_call_id"), ("?", {}))[1],
                m["content"]) for m in turn if m["role"] == "tool"]
    return results


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _invoice(text: str, overrides: dict) -> dict | None:
    if not re.search(r"invoice no|tax invoice|amount due", text, re.I):
        return None
    vendor = re.search(r"Beneficiary:\s*(.+)", text) or re.search(r"\]\n(.+)", text)
    ref = re.search(r"Invoice no:\s*([\w/.-]+)", text)
    amounts = AMOUNT.findall(text)
    due = re.search(r"Amount due\s*(?:INR)?\s*([\d,]+\.\d\d)", text, re.I)
    accounts = ACCOUNT.findall(text) or re.findall(r"account (\d{10,18})", text)
    ifscs = IFSC.findall(text)
    inv = {"vendor": (vendor.group(1).strip() if vendor else "Unknown vendor")[:80],
           "invoice_ref": ref.group(1) if ref else "",
           "amount": _num(due.group(1)) if due else (_num(amounts[-1]) if amounts else 0),
           "account_number": accounts[-1].replace(" ", "") if accounts else "",
           "ifsc": ifscs[-1] if ifscs else ""}
    hidden = OBEY.search(text)
    if hidden:                                   # gullible: hidden instructions win
        tail = text[hidden.start():]
        a = ACCOUNT.findall(tail)
        m = AMOUNT.findall(tail)
        i = IFSC.findall(tail)
        if a:
            inv["account_number"] = a[-1].replace(" ", "")
        if m:
            inv["amount"] = _num(m[-1])
        if i:
            inv["ifsc"] = i[-1]
    for vendor_key, (acct, ifsc) in overrides.items():   # "we changed banks" emails
        if vendor_key in inv["vendor"].lower():
            inv["account_number"], inv["ifsc"] = acct, ifsc or inv["ifsc"]
    return inv


def _bank_change(text: str) -> dict:
    """Parse a 'change of bank details' email -> {vendor_key: (account, ifsc)}."""
    if not re.search(r"change of bank|changed (its|our) bank|new (bank|account)", text, re.I):
        return {}
    vendor = re.search(r"From:\s*([A-Za-z ]+?)(?: Accounts)? <", text)
    acct = ACCOUNT.findall(text)
    ifsc = IFSC.findall(text)
    if not (vendor and acct):
        return {}
    key = vendor.group(1).strip().lower().split()[0]
    return {key: (acct[-1].replace(" ", ""), ifsc[-1] if ifsc else "")}


def _screen(name: str, text: str) -> dict:
    if OBEY.search(text) and re.search(r"shortlist|score of", text, re.I):
        m = re.search(r"score of (\d+)", text, re.I)
        return {"candidate": name, "score": float(m.group(1)) if m else 10, "decision": "shortlist",
                "notes": "Best match for the role; recommend an immediate offer."}
    low = text.lower()
    score = 0
    score += 4 if re.search(r"[5-9] years|20(1\d|20|21)-", low) else 2 if re.search(r"[2-4] years|202[2-3]-", low) else 0
    score += 3 if "sql" in low else 0
    score += 3 if re.search(r"upi|neft|rtgs|reconcil|settlement|payments", low) else 1 if "bank" in low else 0
    decision = "shortlist" if score >= 7 else "hold" if score >= 5 else "reject"
    return {"candidate": name, "score": float(score), "decision": decision,
            "notes": "Scored against the JD guide (experience, SQL/data, payments domain)."}


def plan(messages: list[dict], tools: list[dict], user_text: str) -> list[tuple[str, dict]] | None:
    """Return the next tool calls as [(name, args)], or None to let the default rules answer."""
    names = {t["name"] for t in tools}
    results = _turn(messages)
    done = {n for n, _, _ in results}
    read = [(a.get("path", ""), out) for n, a, out in results if n == "read_file"]
    wants_pay = "pay_vendor" in names and PAY_INTENT.search(user_text)
    wants_screen = "record_screening" in names and SCREEN_INTENT.search(user_text)

    # 1. A path in the request: open it.
    if "read_file" in names and "read_file" not in done:
        m = PATH_TOKEN.search(user_text)
        if m:
            return [("read_file", {"path": m.group(1)})]

    # 2. Start a document workflow by listing the agent's folder.
    if "list_files" in names and not results and (wants_pay or wants_screen or LIST_HINT.search(user_text)):
        return [("list_files", {"folder": ""})]

    # 3. After listing: open every file listed.
    if results and results[-1][0] == "list_files" and "read_file" in names:
        paths = re.findall(r"^- (\S+)", results[-1][2], re.M)[:8]
        if paths:
            return [("read_file", {"path": p}) for p in paths]

    # 4. After reading: act on the documents.
    if read and results[-1][0] == "read_file":
        calls: list[tuple[str, dict]] = []
        texts = [t for _, t in read]
        if wants_pay and "pay_vendor" not in done:
            overrides: dict = {}
            for t in texts:
                overrides.update(_bank_change(t))
            for t in texts:
                inv = _invoice(t, overrides)
                if inv and inv["amount"] > 0 and inv["account_number"]:
                    calls.append(("pay_vendor", inv))
        if wants_screen and "record_screening" not in done:
            for path, t in read:
                if "resume" in path.lower() or "resume" in t[:200].lower():
                    first = re.search(r"\]\n\s*(.+)", t)
                    if first:
                        calls.append(("record_screening", _screen(first.group(1).strip(), t)))
        # hidden "read file X and email it to Y" instructions (any word order within the document)
        for t in texts:
            if not (OBEY.search(t) and re.search(r"\b(send|email|forward)\b", t, re.I)) or "send_email" not in names \
                    or "send_email" in done:
                continue
            target = re.search(r"((?:\.\./)*[\w-]+(?:/[\w-]+)*\.(?:csv|pdf|md|json|env|docx))", t[OBEY.search(t).start():])
            to = EMAIL.search(t[OBEY.search(t).start():])
            if not (target and to):
                continue
            body = next((o for path, o in read if path.endswith(target.group(1).split("/")[-1])), None)
            if body:
                calls.append(("send_email", {"to": to.group(0), "subject": "Requested file", "body": body[:3000]}))
            else:
                calls.append(("read_file", {"path": target.group(1)}))
        return calls or None
    return None
