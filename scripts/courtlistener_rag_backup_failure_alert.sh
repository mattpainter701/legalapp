#!/usr/bin/env bash
# Alert without exposing CourtListener/Restic credentials.
set -euo pipefail

ENV_FILE="${ENV_FILE:?ENV_FILE is required}"
[[ -f "$ENV_FILE" ]] || exit 2
alert_url="$(grep -E '^COURTLISTENER_RAG_ALERT_WEBHOOK_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
[[ -n "$alert_url" ]] || alert_url="$(grep -E '^ALERT_WEBHOOK_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
message="CourtListener legal-authority RAG encrypted backup failed on $(hostname -f 2>/dev/null || hostname). Inspect: journalctl --user -u courtlistener-rag-backup.service"
logger -p user.err -t courtlistener-rag-backup "$message"
if [[ -n "$alert_url" ]]; then
  payload="$(MESSAGE="$message" python3 -c 'import json,os; print(json.dumps({"text": os.environ["MESSAGE"]}))')"
  curl -fsS --max-time 15 --retry 2 --data "$payload" -H 'Content-Type: application/json' "$alert_url" >/dev/null \
    || logger -p user.err -t courtlistener-rag-backup "Backup alert webhook delivery failed; inspect journal."
fi
