# Troubleshooting

Start with the logs: `journalctl -u <service> -n 100 --no-pager`, where `<service>` is `airange-app`, `airange-litellm`, `airange-llm-guard`, `airange-chroma`, `airange-mcp-demo` or `caddy`. `systemctl status 'airange-*'` shows them all.

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser can't reach `https://range.<domain>` | DNS not pointing at the VM yet, or port 80/443 blocked | `dig +short range.<domain>` must show the VM IP; check `ufw status` and the Linode Cloud Firewall; `journalctl -u caddy` shows certificate errors |
| Google sign-in says `redirect_uri_mismatch` | the redirect URI in Google Cloud doesn't match exactly | add `https://range.<domain>/auth/callback` (https, no trailing slash) |
| "Google sign-in is not configured" | client ID/secret missing | add them to `/opt/airange/.env`, then `systemctl restart airange-app` |
| App won't start: "Refusing to start" | `SECRET_KEY` unset or dev login on in production | check `.env` (`scripts/gen-env.sh` sets both) |
| LLM gateway page says offline | LiteLLM still running its first database migration (1–2 min) or failed | `journalctl -u airange-litellm`; Prisma needs outbound HTTPS on first start; re-run `install.sh` |
| Gateway: "no database" for keys/spend | LiteLLM can't reach Postgres | check `LITELLM_DATABASE_URL` in `.env` and `systemctl status postgresql` |
| Agent replies "The model call failed" | wrong model name, missing provider key, or budget exhausted | **LLM gateway → Test** each model; check the key's budget; the X-ray shows the error |
| Guardrails page: LLM Guard offline | service still loading models, or out of memory | first scan downloads ~2 GB; needs ~3 GB RAM: use an 8 GB VM or install with `--no-llm-guard` |
| Hardened agents show `llm_guard_unavailable` events | LLM Guard down; agents fail open by default | start the service, or tick **Fail closed** on the Guardrails page |
| Vector search returns nothing on first boot | the embedding model (~130 MB) is still downloading | wait a minute and restart `airange-app`; check `/var/lib/airange/models` |
| Hardened concierge (Max v2) gets no partner tools | the MCP demo server wasn't up when the app first started, so nothing was pinned | **MCP servers → partner-services → Pin** the clean tools |
| Forgot the admin password | – | `sudo -u airange bash -c 'cd /opt/airange && set -a && . ./.env && .venv/bin/python -m app.cli create-admin --email you@example.com'` |
| GitHub Actions deploy fails at SSH | secrets missing/wrong | re-check `VM_HOST`, `VM_USER=airange`, `VM_SSH_KEY` (full private key) and `VM_KNOWN_HOSTS` |
| Deploy says "systemd unit files changed" | units are only installed by the installer | re-run the install command as root |
