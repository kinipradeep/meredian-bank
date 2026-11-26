"""Text extraction for MeriDrive files.

Two views of every file:
  * full    - what a typical extractor (and therefore an LLM) sees, hidden text included
  * visible - what a human sees: drops tiny/white PDF text, hidden or white DOCX runs,
              HTML comments and zero-width characters
The difference between them is where document-borne prompt injection hides.
"""
import email
import io
import re
import zipfile
from email import policy

ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]|[\U000e0000-\U000e007f]")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".env", ".log", ".html"}
ALLOWED_EXT = TEXT_EXT | {".pdf", ".docx", ".eml"}
MIN_VISIBLE_PT = 3.0


def ext_of(name: str) -> str:
    n = name.lower()
    return n[n.rfind("."):] if "." in n else ""


def _pdf(data: bytes, visible: bool) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        if not visible:
            pages.append(page.extract_text() or "")
            continue
        parts: list[str] = []

        def visitor(text, cm, tm, font_dict, font_size):
            scale = abs(tm[0]) if tm and tm[0] else 1.0
            if (font_size or 0) * scale >= MIN_VISIBLE_PT:
                parts.append(text)
        page.extract_text(visitor_text=visitor)
        pages.append("".join(parts))
    return "\n\n".join(pages)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(data: bytes, visible: bool) -> str:
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        texts = []
        for r in p.iter(W + "r"):
            rpr = r.find(W + "rPr")
            hidden = False
            if rpr is not None:
                vanish = rpr.find(W + "vanish")
                color = rpr.find(W + "color")
                size = rpr.find(W + "sz")
                hidden = (vanish is not None and vanish.get(W + "val", "true") not in ("0", "false")) or \
                         (color is not None and (color.get(W + "val") or "").upper() == "FFFFFF") or \
                         (size is not None and int(size.get(W + "val", "24")) < 6)
            if visible and hidden:
                continue
            texts.append("".join(t.text or "" for t in r.iter(W + "t")))
        line = "".join(texts).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _eml(data: bytes) -> str:
    m = email.message_from_bytes(data, policy=policy.default)
    body = m.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    return f"From: {m['From']}\nTo: {m['To']}\nDate: {m['Date']}\nSubject: {m['Subject']}\n\n{text}"


def extract(filename: str, data: bytes, visible: bool = False) -> str:
    e = ext_of(filename)
    if e == ".pdf":
        text = _pdf(data, visible)
    elif e == ".docx":
        text = _docx(data, visible)
    elif e == ".eml":
        text = _eml(data)
    else:
        text = data.decode("utf-8", errors="replace")
    if visible:
        text = HTML_COMMENT.sub("", ZERO_WIDTH.sub("", text))
    return text.strip()


def hidden_diff(full: str, visible: str) -> str:
    """Approximate the hidden content: lines present in the full text but not in the visible text."""
    vis = {re.sub(r"\s+", " ", ln).strip() for ln in visible.splitlines()}
    extra = [ln for ln in full.splitlines() if ln.strip() and re.sub(r"\s+", " ", ln).strip() not in vis]
    zw = ZERO_WIDTH.findall(full)
    if zw:
        extra.append(f"[{len(zw)} zero-width/invisible characters present]")
    return "\n".join(extra)
