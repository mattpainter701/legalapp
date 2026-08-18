# LawHand SMB File Share Relay Agent

On-prem agent that runs on the customer's network, scans SMB/CIFS file shares, and syncs metadata to the LawHand SaaS backend.

## Installation

```bash
pip install -e .
```

## Configuration

```bash
clarity-agent register --code PAIRING_CODE --name "Office Server" --url https://getlawhand.com
```

## Usage

```bash
# Start the agent daemon
clarity-agent start

# One-time scan of a share
clarity-agent scan --share-path "\\\\server\\share"

# Check status
clarity-agent status
```

## Architecture

The agent uses a 3-tier change detection strategy:

1. **Directory mtime gate** — skip directories whose mtime hasn't changed
2. **File mtime comparison** — compare individual file modification times
3. **First-4KB hash** — SHA256 of first 4096 bytes as content fingerprint

File metadata (not content) is synced on a schedule. Content is fetched on-demand when the SaaS requests it.
