"""Chunking strategies, written to be easy to show in the admin RAG lab."""
import re

STRATEGIES = {
    "fixed": "Fixed-size character windows with overlap",
    "paragraph": "Pack whole paragraphs up to the size limit",
    "sentence": "Pack whole sentences up to the size limit",
    "markdown": "Split on Markdown headings, then pack paragraphs",
}


def _pack(units: list[str], size: int, overlap: int) -> list[str]:
    chunks, cur = [], ""
    for u in units:
        u = u.strip()
        if not u:
            continue
        if cur and len(cur) + len(u) + 2 > size:
            chunks.append(cur.strip())
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + "\n\n" + u) if tail else u
        else:
            cur = (cur + "\n\n" + u) if cur else u
        while len(cur) > size * 1.5:              # a single unit longer than the window
            chunks.append(cur[:size].strip())
            cur = cur[size - overlap:] if overlap else cur[size:]
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def chunk_text(text: str, strategy: str = "paragraph", size: int = 600, overlap: int = 80) -> list[dict]:
    text = text.replace("\r\n", "\n")
    size = max(100, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    if strategy == "fixed":
        step = size - overlap
        spans = [(i, text[i:i + size]) for i in range(0, max(len(text), 1), step)]
        return [{"index": n, "text": t, "start": i, "end": i + len(t), "chars": len(t),
                 "approx_tokens": max(1, len(t) // 4)} for n, (i, t) in enumerate([x for x in spans if x[1].strip()])]
    elif strategy == "sentence":
        raw = _pack(re.split(r"(?<=[.!?])\s+", text), size, overlap)
    elif strategy == "markdown":
        sections = re.split(r"\n(?=#{1,4} )", text)
        raw = []
        for sec in sections:
            raw.extend(_pack(sec.split("\n\n"), size, overlap))
    else:  # paragraph
        raw = _pack(text.split("\n\n"), size, overlap)

    out, cursor = [], 0
    for i, c in enumerate(raw):
        probe = c[:60]
        start = text.find(probe, max(0, cursor - 5))
        if start < 0:
            start = cursor
        out.append({"index": i, "text": c, "start": start, "end": start + len(c),
                    "chars": len(c), "approx_tokens": max(1, len(c) // 4)})
        cursor = start + len(c) - overlap
    return out
