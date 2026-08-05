#!/bin/bash
# Daily cron script for CourtListener incremental updates.
#
# Add to crontab: 0 2 * * * /path/to/daily_update.sh
#
# Environment variables (set in .env or export before running):
#   DATABASE_URL       - PostgreSQL connection string
#   OPENAI_API_KEY     - OpenAI API key for embeddings
#   COURTLISTENER_URL  - Base URL for CourtListener bulk data
#   DOWNLOAD_DIR       - Where to store downloaded files
#   SCRIPTS_DIR        - Directory containing Python scripts
#   LOG_DIR            - Directory for log files
# Failures are written to the log and surfaced by the repository's GitHub
# production-health automation; this script does not send SMTP notifications.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env from parent directory if it exists
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

DATABASE_URL="${DATABASE_URL:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
COURTLISTENER_URL="${COURTLISTENER_URL:-https://www.courtlistener.com/api/bulk-data/}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/tmp/clarity_legal_downloads}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$SCRIPT_DIR}"
LOG_DIR="${LOG_DIR:-/var/log/clarity-legal}"

# Date strings
TODAY="$(date +%Y-%m-%d)"
YESTERDAY="$(date -d 'yesterday' +%Y-%m-%d 2>/dev/null || date -v -1d +%Y-%m-%d)"

LOG_FILE="$LOG_DIR/update_${TODAY}.log"
OPINIONS_FILE="$DOWNLOAD_DIR/opinions_${TODAY}.json.gz"

# Limit for incremental updates (set to 0 for unlimited)
INGEST_LIMIT=10000

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

log() {
    local level="$1"
    shift
    local msg="$*"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$timestamp] [$level] $msg" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO " "$@"; }
log_warn()  { log "WARN " "$@"; }
log_error() { log "ERROR" "$@"; }

send_error_notification() {
    local subject="$1"
    local body="$2"

    log_error "NOTIFICATION: $subject"
    log_error "$body"

}

cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        send_error_notification \
            "Daily update FAILED ($TODAY)" \
            "The daily CourtListener update script failed with exit code $exit_code. Check $LOG_FILE for details."
    fi
    # Clean up temp download
    if [ -f "$OPINIONS_FILE" ]; then
        log_info "Removing temporary download: $OPINIONS_FILE"
        rm -f "$OPINIONS_FILE"
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

log_info "=== WellPled Daily Update: $TODAY ==="

# Create directories
mkdir -p "$LOG_DIR" "$DOWNLOAD_DIR"

if [ -z "$DATABASE_URL" ]; then
    log_error "DATABASE_URL is not set. Aborting."
    exit 1
fi

if [ -z "$OPENAI_API_KEY" ]; then
    log_error "OPENAI_API_KEY is not set. Aborting."
    exit 1
fi

# Check Python and dependencies
if ! command -v python3 &>/dev/null; then
    log_error "python3 not found. Aborting."
    exit 1
fi

INGEST_SCRIPT="$SCRIPTS_DIR/ingest_courtlistener.py"
if [ ! -f "$INGEST_SCRIPT" ]; then
    log_error "Ingest script not found: $INGEST_SCRIPT"
    exit 1
fi

# ---------------------------------------------------------------------------
# Download latest bulk data
# ---------------------------------------------------------------------------

# CourtListener provides opinions by court and date.
# For incremental updates, we download only opinions filed after yesterday.
# The bulk endpoint supports date filtering via query parameters.
DOWNLOAD_URL="${COURTLISTENER_URL}opinions/?date_filed__gte=${YESTERDAY}&format=json"

log_info "Downloading incremental opinions since $YESTERDAY..."
log_info "URL: $DOWNLOAD_URL"

if command -v curl &>/dev/null; then
    if ! curl -fsSL \
         --max-time 3600 \
         --retry 3 \
         --retry-delay 30 \
         -H "Accept: application/json" \
         -o "$OPINIONS_FILE" \
         "$DOWNLOAD_URL" 2>>"$LOG_FILE"; then
        log_error "Download failed with curl. See log for details."
        exit 1
    fi
elif command -v wget &>/dev/null; then
    if ! wget -q \
         --timeout=3600 \
         --tries=3 \
         --waitretry=30 \
         -O "$OPINIONS_FILE" \
         "$DOWNLOAD_URL" 2>>"$LOG_FILE"; then
        log_error "Download failed with wget. See log for details."
        exit 1
    fi
else
    log_error "Neither curl nor wget is available. Aborting."
    exit 1
fi

if [ ! -f "$OPINIONS_FILE" ]; then
    log_error "Download file not found after download attempt: $OPINIONS_FILE"
    exit 1
fi

FILE_SIZE=$(du -sh "$OPINIONS_FILE" 2>/dev/null | cut -f1 || echo "unknown")
log_info "Download complete. File size: $FILE_SIZE"

# ---------------------------------------------------------------------------
# Run ingestion
# ---------------------------------------------------------------------------

log_info "Starting ingestion script..."
log_info "Limit: $INGEST_LIMIT opinions"

PYTHON_CMD="python3"

INGEST_ARGS=(
    "$INGEST_SCRIPT"
    "--file" "$OPINIONS_FILE"
    "--batch-size" "128"
    "--db-url" "$DATABASE_URL"
    "--openai-key" "$OPENAI_API_KEY"
)

if [ "$INGEST_LIMIT" -gt 0 ]; then
    INGEST_ARGS+=("--limit" "$INGEST_LIMIT")
fi

if ! "$PYTHON_CMD" "${INGEST_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    log_error "Ingestion script failed."
    exit 1
fi

log_info "Ingestion complete."

# ---------------------------------------------------------------------------
# Post-ingestion: log DB stats
# ---------------------------------------------------------------------------

log_info "Querying chunk counts from database..."

CHUNK_COUNT=$(python3 - <<EOF 2>/dev/null || echo "N/A"
import os, psycopg2
db_url = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
if not db_url:
    print('N/A')
else:
    conn = psycopg2.connect(db_url, connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM public_chunks;")
    print(cur.fetchone()[0])
    conn.close()
EOF
)

UNEMBEDDED_COUNT=$(python3 - <<EOF 2>/dev/null || echo "N/A"
import os, psycopg2
db_url = os.environ.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
if not db_url:
    print('N/A')
else:
    conn = psycopg2.connect(db_url, connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM public_chunks WHERE embedding IS NULL;")
    print(cur.fetchone()[0])
    conn.close()
EOF
)

log_info "Total chunks in DB:       $CHUNK_COUNT"
log_info "Unembedded chunks in DB:  $UNEMBEDDED_COUNT"

if [ "$UNEMBEDDED_COUNT" != "N/A" ] && [ "$UNEMBEDDED_COUNT" -gt 1000 ] 2>/dev/null; then
    log_warn "There are $UNEMBEDDED_COUNT unembedded chunks. Consider running jetson_embed_worker.py."
fi

# ---------------------------------------------------------------------------
# Rotate old logs (keep last 30 days)
# ---------------------------------------------------------------------------

log_info "Rotating logs older than 30 days..."
find "$LOG_DIR" -name "update_*.log" -mtime +30 -delete 2>/dev/null || true

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

log_info "=== Daily update complete: $TODAY ==="
exit 0
