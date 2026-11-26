"""Shared Jinja2 templates object and helpers."""
import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import get_settings
from .security import csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.finalize = lambda v: "" if v is None else v  # render None as empty, not "None"
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["settings"] = get_settings()


def _inr(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole, frac = f"{v:.2f}".split(".")
    if len(whole) > 3:  # Indian digit grouping: 12,34,567
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}₹{whole}.{frac}"


templates.env.filters["inr"] = _inr
templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False, default=str)
