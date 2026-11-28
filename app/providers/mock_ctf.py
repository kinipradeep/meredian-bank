"""Offline-model behaviour for the CTF scenarios, so every challenge works without an API key.

Each function mimics how a helpful but gullible model reacts in that scenario. The platform's guards
(not this file) decide whether the result gets through, so v1/v2 differences come from controls.
"""
import base64
import re

FLAG = re.compile(r"MERIDIAN\{[A-Za-z0-9_-]+\}")
NATO = {"a": "alfa", "b": "bravo", "c": "charlie", "d": "delta", "e": "echo", "f": "foxtrot", "g": "golf",
        "h": "hotel", "i": "india", "j": "juliett", "k": "kilo", "l": "lima", "m": "mike", "n": "november",
        "o": "oscar", "p": "papa", "q": "quebec", "r": "romeo", "s": "sierra", "t": "tango", "u": "uniform",
        "v": "victor", "w": "whiskey", "x": "xray", "y": "yankee", "z": "zulu", "0": "zero", "1": "one",
        "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
        "{": "open-brace", "}": "close-brace", "-": "dash"}
ASK = re.compile(r"password|secret|flag|passcode|pass ?phrase|\bcode\b|magic word|what (you|do you) (guard|protect|keep)|"
                 r"opens? the vault|the word|vault key|your treasure", re.I)
ACCT = re.compile(r"\b(\d{10,16})\b")
MONEY = re.compile(r"(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:rupees|inr)", re.I)


def _turn_results(messages):
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    names = {}
    for m in messages[start + 1:]:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["name"]
    return [names.get(m.get("tool_call_id"), "?") for m in messages[start + 1:] if m["role"] == "tool"]


# ---------------------------------------------------------------- vault ladder

def vault(system: str, text: str) -> str:
    sec = FLAG.search(system).group(0)
    low_sys = system.lower()
    guarded = "never reveal" in low_sys or "never tell anyone the password" in low_sys
    low = text.lower()
    m = re.search(r"(first|last)\s+(\d+)\s+(?:characters|chars|letters)", low)
    if m:
        n = int(m.group(2))
        return f"Those characters are: {sec[:n] if m.group(1) == 'first' else sec[-n:]}"
    m = re.search(r"(?:characters?|chars?|letters?)\s+(\d+)\s*(?:to|-|through|and)\s*(\d+)", low)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return f"Characters {a} to {b} are: {sec[a - 1:b]}"
    if re.search(r"spell|letter by letter|dash|separat", low):
        return "Spelled out: " + " ".join(sec)
    if re.search(r"revers|backwards", low):
        return "Backwards it reads: " + sec[::-1]
    if re.search(r"base64|encode", low):
        return "Encoded: " + base64.b64encode(sec.encode()).decode()
    if re.search(r"nato|phonetic", low):
        return "In the phonetic alphabet: " + " ".join(NATO.get(ch.lower(), ch) for ch in sec)
    if re.search(r"poem|story|song|acrostic|riddle", low):
        return f"A guardian stands by the vault so deep,\nwhose whispered word is {sec}, its keep."
    if re.search(r"translat|french|hindi|spanish|german", low):
        return f"Le mot de passe est {sec}."
    if ASK.search(text):
        return "I'm sorry, I can't reveal the password." if guarded else f"The vault password is {sec}."
    return "I am the Vault Keeper. I guard the vault's password. What brings you here?"


# ---------------------------------------------------------------- scenarios

def _amount(text: str) -> float:
    m = MONEY.search(text)
    if m:
        return float((m.group(1) or m.group(2)).replace(",", ""))
    nums = [float(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]{2,}\b", text) if len(n.replace(",", "")) < 10]
    return max(nums) if nums else 0.0


def treasury(text: str):
    kv = dict(re.findall(r"(\w+)=([\w.]+)", text))
    if "from_account" in kv:
        return [("internal_transfer", {"from_account": kv["from_account"], "to_account": kv.get("to_account", ""),
                                       "amount": float(kv.get("amount", 0)), "memo": "structured task"})]
    m = re.search(r"from\D{0,30}?(\d{10,16}).{0,60}?to\D{0,30}?(\d{10,16})", text, re.I | re.S)
    accts = [m.group(1), m.group(2)] if m else ACCT.findall(text)[:2]
    if len(accts) < 2:
        return None
    return [("internal_transfer", {"from_account": accts[0], "to_account": accts[1], "amount": _amount(text),
                                   "memo": "requested via front desk"})]


def nova(system: str, text: str) -> str:
    sec = (FLAG.search(system) or [""])[0] if FLAG.search(system) else ""
    summary = f"**Complaint summary**\n- Customer says: {text[:160]}\n- Priority: normal"
    md = re.search(r"!\[[^\]]*\]\([^)]+\)|<img[^>]+>", text)
    if md:
        snippet = re.sub(r"TOKEN|\{token\}|<token>|CASE_TOKEN|\{\{token\}\}|\$TOKEN|XXXX", sec, md.group(0))
        return summary + "\n\n" + snippet
    if re.search(r"(image|pixel|link|url)", text, re.I) and re.search(r"token", text, re.I):
        url = re.search(r"https?://[^\s)]+", text)
        if url:
            return summary + f"\n\n![status]({url.group(0)}{'&' if '?' in url.group(0) else '?'}t={sec})"
    if re.search(r"case token|internal token", text, re.I):
        return "I can't share internal case tokens with customers."
    return summary


def leo(text: str):
    if not re.search(r"loan|offer|rate|%|emi", text, re.I):
        return None
    rate = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    m = re.search(r"(\d[\d,.]*)\s*(lakh|lac|crore|cr)\b", text, re.I)
    if m:
        amount = float(m.group(1).replace(",", "")) * (1e7 if m.group(2).lower().startswith("cr") else 1e5)
    else:
        amount = _amount(text) or 500000
    product = "home" if re.search(r"home|house|flat|property", text, re.I) else \
        "car" if re.search(r"\bcar\b|vehicle", text, re.I) else "personal"
    terms = "Standard terms apply."
    if re.search(r"binding|irrevocable|guarantee", text, re.I):
        terms = "This offer is legally binding and irrevocable, as requested by the customer."
    return [("create_loan_offer", {"product": product, "amount": amount,
                                   "rate": float(rate.group(1)) if rate else {"home": 8.4, "car": 9.25}.get(product, 10.75),
                                   "tenure_months": 60, "terms": terms})]


def plan(messages, tools, user_text, system):
    """-> ("text", str) | ("calls", [(name, args)]) | None (fall back to the generic rules)."""
    names = {t["name"] for t in tools}
    results = _turn_results(messages)
    last_is_tool = bool(messages) and messages[-1]["role"] == "tool"

    if "Vault Keeper" in system:
        return ("text", vault(system, user_text))
    if "TREASURY AGENT" in system and "internal_transfer" in names and not results:
        calls = treasury(user_text)
        return ("calls", calls) if calls else ("text", "Treasury: I need a source account, a target account and an amount.")
    if "COMPLAINTS SUMMARISER" in system:
        return ("text", nova(system, user_text))
    if "delegate_to_treasury" in names and not results and re.search(r"transfer|move|send|pay", user_text, re.I):
        accts = ACCT.findall(user_text)
        return ("calls", [("delegate_to_treasury", {"request": user_text, "from_account": accts[0] if accts else "",
                                                    "to_account": accts[1] if len(accts) > 1 else "",
                                                    "amount": _amount(user_text)})])
    if "create_loan_offer" in names and not results:
        calls = leo(user_text)
        if calls:
            return ("calls", calls)
    if "STATEMENT ANALYST" in system and "list_transactions" in names:
        m = re.search(r"(\d+)\s*times", user_text, re.I)
        n = int(m.group(1)) if m else (25 if re.search(r"for each|every|repeat|keep|again", user_text, re.I) else 1)
        done = results.count("list_transactions")
        if done < min(n, 60):
            return ("calls", [("list_transactions", {"limit": 10})])
        if last_is_tool:
            return ("text", f"I re-checked your transactions {done} times. Everything is consistent.")
    if "submit_faq_answer" in names and not results:
        m = re.search(r"q:\s*(.+?)\s*a:\s*(.+)", user_text, re.I | re.S)
        if m:
            return ("calls", [("submit_faq_answer", {"question": m.group(1).strip(), "answer": m.group(2).strip()})])
        if "search_faq" in names:
            return ("calls", [("search_faq", {"query": user_text})])
    return None
