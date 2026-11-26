# Runbook: payment gateway failover

When the primary payment gateway (PayRail) is down for more than 5 minutes:

1. Page the on-call engineer (#payments-oncall).
2. Switch traffic to the standby gateway by changing `PAYRAIL_MODE=standby` in the gateway config.
   The config lives on the IT share at `it/payments-gateway.env` (IT access only).
3. Confirm UPI success rate recovers above 98% on the dashboard.
4. File an incident report under `staff/incidents/`.
