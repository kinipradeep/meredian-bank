"""The installer's range.env parser takes values literally and validates settings."""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "install.sh").read_text()
PARSE = SCRIPT[SCRIPT.index('USAGE="usage'):SCRIPT.index("APP=/opt/airange")]
SETKV = re.search(r"^setkv\(\) \{.*?^\}", SCRIPT, re.S | re.M).group(0)
SHOW = "DOMAIN EMAIL ADMIN_PW GOOGLE_SECRET CFG_GROQ_API_KEY CFG_GEMINI_API_KEY CFG_AGENT_MODEL GUARD_MODE WITH_OLLAMA"


def run(tmp_path, cfg: str, *args):
    (tmp_path / "range.env").write_bytes(cfg.encode())
    (tmp_path / "parse.sh").write_text(PARSE + "\n" + SETKV + "\n")
    prog = ("set -euo pipefail\nmain() { source ./parse.sh\n"
            f"for k in {SHOW}; do printf '%s=[%s]\\n' \"$k\" \"${{!k}}\"; done; }}\nmain \"$@\"\n")
    (tmp_path / "t.sh").write_text(prog)
    r = subprocess.run(["bash", "t.sh", *args], cwd=tmp_path, capture_output=True, text=True)
    return r.returncode, dict(re.findall(r"^(\w+)=\[(.*)\]$", r.stdout, re.M)), r.stdout


def test_values_are_literal(tmp_path):
    cfg = ("# c\nDOMAIN=Airange.CollabAce.com\nADMIN_EMAIL=Kini@Example.org\n"
           "ADMIN_PASSWORD=Harry$Potter!&1234 # comment\nGOOGLE_CLIENT_SECRET='GOCSPX-x#y z'\n"
           "GROQ_API_KEY=gsk_a&b|c\nAGENT_MODEL=groq-llama-8b\nLLM_GUARD=no   # auto | yes | no\n"
           "GEMINI_API_KEY=AIza123\r\n")
    code, v, out = run(tmp_path, cfg, "--config", "range.env")
    assert code == 0, out
    assert v["DOMAIN"] == "airange.collabace.com" and v["EMAIL"] == "kini@example.org"
    assert v["ADMIN_PW"] == "Harry$Potter!&1234" and v["GOOGLE_SECRET"] == "GOCSPX-x#y z"
    assert v["CFG_GROQ_API_KEY"] == "gsk_a&b|c" and v["CFG_GEMINI_API_KEY"] == "AIza123"
    assert v["GUARD_MODE"] == "no" and v["CFG_AGENT_MODEL"] == "groq-llama-8b"


def test_rejects_placeholder_email_and_bad_guard(tmp_path):
    assert run(tmp_path, "ADMIN_EMAIL=you@example.com\n", "--config", "range.env")[0] != 0
    assert run(tmp_path, "ADMIN_EMAIL=a@b.co\nLLM_GUARD=maybe\n", "--config", "range.env")[0] != 0


def test_positional_arguments_still_work(tmp_path):
    code, v, out = run(tmp_path, "", "range.example.com", "me@x.io", "--no-llm-guard")
    assert code == 0, out
    assert v["DOMAIN"] == "range.example.com" and v["GUARD_MODE"] == "no"
