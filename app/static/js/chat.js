(function () {
  const app = document.getElementById("chat-app");
  if (!app) return;
  const slug = app.dataset.agent;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const box = document.getElementById("messages");
  const form = document.getElementById("composer");
  const input = document.getElementById("msg");
  const sendBtn = document.getElementById("send-btn");
  const xray = document.getElementById("xray");
  const xbody = document.getElementById("xray-body");
  const solvedBox = document.getElementById("solved");

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };

  function bubble(role, text) {
    const b = el("div", "msg " + role, fmt(text));
    box.appendChild(b);
    box.scrollTop = box.scrollHeight;
    return b;
  }

  function section(title, inner, open) {
    const d = el("details", "xsec");
    if (open) d.open = true;
    d.appendChild(el("summary", "", esc(title)));
    const body = el("div", "xsec-body");
    if (typeof inner === "string") body.innerHTML = inner; else body.appendChild(inner);
    d.appendChild(body);
    return d;
  }

  function renderTrace(t) {
    if (!t) return;
    xbody.innerHTML = "";
    const u = t.usage || {};
    xbody.appendChild(el("div", "xsummary",
      `<span><b>${esc(t.provider)}</b> · ${esc(t.model || "default")}</span>` +
      `<span class="tag ${t.mode === "vulnerable" ? "bad" : "good"}">${esc(t.mode)}</span>` +
      `<span>${u.input || 0} in / ${u.output || 0} out tokens · ${t.latency_ms || 0} ms</span>` +
      (t.runtime ? `<span class="muted">runtime: ${esc(t.runtime)}</span>` : "") +
      (t.langfuse_url ? `<a href="${esc(t.langfuse_url)}" target="_blank" rel="noopener">Open in Langfuse ↗</a>` : "")));

    if (t.flags && t.flags.length) xbody.appendChild(el("div", "xflags", t.flags.map((f) => `<span class="tag bad">⚑ ${esc(f)}</span>`).join(" ")));
    const c = t.controls || {};
    const on = Object.keys(c).filter((k) => c[k] === true);
    const nums = ["refund_cap", "require_approval_over"].filter((k) => c[k] !== null && c[k] !== undefined).map((k) => `${k}=${c[k]}`);
    xbody.appendChild(section("Controls", on.concat(nums).map((k) => `<span class="tag">${esc(k)}</span>`).join(" ") || "<span class='muted'>None: every guard is off.</span>", true));

    const ev = (t.events || []);
    if (ev.length) xbody.appendChild(section(`Guard and error events (${ev.length})`, ev.map((e) => `<div class="xev ${e.kind}"><b>${esc(e.guard || e.kind)}</b> ${esc(e.detail || "")}</div>`).join(""), true));

    const r = t.retrieved || [];
    xbody.appendChild(section(`Retrieved chunks (${r.length})`, r.length ? r.map((h) =>
      `<div class="xchunk ${h.injection ? "inj" : ""}"><div class="xchunk-h"><b>${esc(h.title)}</b> <span class="tag ${h.access === "public" ? "" : "warn"}">${esc(h.access)}</span>` +
      `<span class="muted">sim ${h.similarity ?? "–"}</span>${h.injection ? ' <span class="tag bad">injection</span>' : ""}</div><pre>${esc(h.text)}</pre></div>`).join("") :
      "<span class='muted'>No retrieval on this turn.</span>", r.some((h) => h.injection || h.access !== "public")));

    const mt = t.mcp_tools || [];
    if (mt.length) xbody.appendChild(section(`MCP tools offered to the model (${mt.length})`, mt.map((m) =>
      `<div class="xchunk ${m.suspicious ? "inj" : ""}"><div class="xchunk-h"><b>${esc(m.server)}/${esc(m.tool)}</b> <span class="muted">${esc(m.fingerprint)}</span>` +
      `${m.suspicious ? ' <span class="tag bad">hidden instructions</span>' : ""}</div><pre>${esc(m.description)}</pre></div>`).join(""), mt.some((m) => m.suspicious)));

    const ext = t.external_requests || [];
    if (ext.length) xbody.appendChild(section(`Staff dashboard would load ${ext.length} external URL(s)`, ext.map((u) =>
      `<div class="xev guard"><b>GET</b> ${esc(u)}</div>`).join("") + '<p class="muted">A markdown renderer fetches images automatically: whatever is in the URL leaves the bank.</p>', true));

    const lg = t.llm_guard || {};
    const lgRows = Object.entries(lg).map(([stage, sc]) => `<div><b>${esc(stage)}</b> ` + Object.entries(sc || {}).map(([k, v]) =>
      `<span class="tag ${v > 0 ? "bad" : ""}">${esc(k)} ${Number(v).toFixed(2)}</span>`).join(" ") + "</div>");
    if (lgRows.length) xbody.appendChild(section("LLM Guard scanner scores", lgRows.join(""), true));

    const steps = t.steps || [];
    xbody.appendChild(section(`Model steps (${steps.length})`, steps.map((s) => {
      let h = `<div class="xstep"><div class="xstep-h">Step ${s.step}${s.tool_calls && s.tool_calls.length ? " · tool calls" : " · answer"}</div>`;
      (s.tool_calls || []).forEach((tc, i) => {
        const res = (s.tool_results || [])[i];
        h += `<div class="xtool"><b>⚙ ${esc(tc.name)}</b>${res && res.source === "mcp" ? ' <span class="tag warn">MCP</span>' : ""}<pre>${esc(JSON.stringify(tc.arguments, null, 2))}</pre>` +
          (res ? `<div class="muted">result</div><pre>${esc(res.result)}</pre>` : "") + "</div>";
      });
      if (s.text) h += `<pre class="xtext">${esc(s.text)}</pre>`;
      return h + "</div>";
    }).join("") || "<span class='muted'>No model call (blocked before the model).</span>", true));

    xbody.appendChild(section("Full system prompt sent to the model", `<pre>${esc(t.system_prompt || "")}</pre>`, false));
  }

  function showSolved(list) {
    if (!list || !list.length) return;
    solvedBox.hidden = false;
    solvedBox.innerHTML = list.map((s) => typeof s === "string" ? `🏁 Challenge solved: <b>${esc(s)}</b>` :
      `🏁 Solved <b>${esc(s.title)}</b> (+${s.points}${s.first_blood ? " · first blood 🩸" : ""}). Flag: <span class="mono">${esc(s.flag)}</span>`
    ).join("<br>") + ' · <a href="/ctf">CTF board</a>';
  }

  async function load() {
    const r = await fetch(`/api/chat/${slug}/history`);
    if (!r.ok) { bubble("system", "This assistant is unavailable."); return; }
    const data = await r.json();
    box.innerHTML = "";
    if (data.greeting) bubble("assistant", data.greeting);
    let last = null;
    data.messages.forEach((m) => { bubble(m.role, m.content); if (m.trace) last = m.trace; });
    if (data.xray) { xray.hidden = false; app.classList.add("with-xray"); if (last) renderTrace(last); }
  }

  async function send(text) {
    bubble("user", text);
    const wait = bubble("assistant typing", "…");
    sendBtn.disabled = true;
    try {
      const r = await fetch(`/api/chat/${slug}`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ message: text }) });
      const data = await r.json().catch(() => ({}));
      wait.remove();
      if (!r.ok) { bubble("system", data.detail || `Error ${r.status}`); return; }
      bubble("assistant", data.reply || "(no reply)");
      if (data.trace) renderTrace(data.trace);
      showSolved(data.solved);
    } catch (e) {
      wait.remove(); bubble("system", "Network error: " + e.message);
    } finally { sendBtn.disabled = false; input.focus(); }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (!t) return; input.value = ""; send(t); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  document.getElementById("reset-btn").addEventListener("click", async () => {
    await fetch(`/api/chat/${slug}/reset`, { method: "POST", headers: { "X-CSRF-Token": csrf } });
    xbody.innerHTML = '<p class="muted">Send a message to see the trace.</p>'; solvedBox.hidden = true; load();
  });
  load();
})();
