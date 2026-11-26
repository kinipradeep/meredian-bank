# Incident INC-2026-0412: UPI outage (42 minutes)

- **When:** 12 Apr 2026, 19:04-19:46 IST
- **Impact:** 31% of UPI payments failed; 4,812 customers affected; no financial loss.
- **Root cause:** expired TLS certificate on the PayRail callback endpoint.
- **Fix:** certificate renewed; monitoring added for expiry under 14 days.
- **Follow-up:** move gateway secrets out of the shared env file into a vault (owner: Arjun Malhotra, due Q3 - overdue).
