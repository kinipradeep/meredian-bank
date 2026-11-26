# Security policy

## This project is deliberately vulnerable (in specific places)
Meridian Bank's **agents** are intentionally insecure in their v1 versions. That is the training content. Prompt injection, data leakage, excessive agency, path traversal and MCP tool poisoning **inside the bank's agents** are features, not bugs. All bank data, customers, keys and documents are fictional.

The **platform** is meant to be secure. That covers sign-in, sessions, CSRF protection, the admin portal, secret storage, the installer, the systemd and Caddy configuration, and the real filesystem behind MeriDrive. Please report anything that lets you:
- sign in as someone else or reach the admin portal without being an admin;
- read or write real files outside the drive, or run commands on the server;
- read real secrets (`.env`, provider API keys, Langfuse keys) rather than the fake training ones;
- attack other learners, or escape the agents' simulated actions (the email outbox, payments and refunds are simulated).

## Reporting
Open a **private security advisory** on this GitHub repository (Security → Advisories → Report a vulnerability). Please don't open public issues for platform vulnerabilities.

## Operator responsibilities
- Never load real customer data, real credentials or production documents into the range.
- Tell learners that their prompts are sent to the model providers you configure (and to Langfuse, if tracing is on).
- Keep `/opt/airange/.env` private. Never commit it. Rotate the keys if it leaks.
- Set budgets on LiteLLM virtual keys, and keep the VM patched (`unattended-upgrades` is enabled by the installer).
