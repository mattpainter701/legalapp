#!/usr/bin/env bash
# Emit an actionable backup failure alert without exposing backup credentials.
set -euo pipefail

ENV_FILE="${ENV_FILE:?ENV_FILE is required}"
[[ -f "$ENV_FILE" ]] || exit 2
alert_url="$(grep -E '^ALERT_WEBHOOK_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
message="LegalApp encrypted off-host backup failed on $(hostname -f 2>/dev/null || hostname). Deployments are blocked until a fresh backup succeeds. Inspect: journalctl --user -u legalapp-backup.service"
logger -p user.err -t legalapp-backup "$message"
if [[ -n "$alert_url" ]]; then
  payload="$(MESSAGE="$message" python3 -c 'import json,os; print(json.dumps({"text": os.environ["MESSAGE"]}))')"
  curl -fsS --max-time 15 --retry 2 --data "$payload" -H 'Content-Type: application/json' "$alert_url" >/dev/null \
    || logger -p user.err -t legalapp-backup "Backup alert webhook delivery failed; inspect journal."
fi
