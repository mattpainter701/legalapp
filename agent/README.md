# LawHand File Share Agent

The agent runs inside the customer's network, indexes approved SMB/CIFS file
shares, and relays document text to LawHand on demand. It exists so a firm can
put a large on-premise share — the one that is too big, too old, or too
sensitive to migrate — behind matter context, search, and research prompts
without copying it to the cloud.

What leaves the network on a schedule is metadata plus a short snippet per file
(path, name, size, timestamps, first ~500 characters). Full document text is
read only when someone in the product asks for that specific document, and each
of those reads is written to the tenant's access log.

## Install

Installers are published on every `agent-v*` tag by
[`.github/workflows/agent-release.yml`](../.github/workflows/agent-release.yml)
and can be built locally (see **Building installers** below).

### Windows (primary target)

```powershell
msiexec /i lawhand-agent-<version>-x64.msi /qn `
        PAIRING_CODE=<code from Administration → File Shares> `
        SAAS_URL=https://getlawhand.com
```

The MSI installs `lawhand-agent.exe` into `C:\Program Files\LawHand\Agent`,
registers the **LawHand File Share Agent** service (auto-start, restart on
failure), creates `C:\ProgramData\LawHand\Agent` for config/key/ledger, and
pairs the agent during install when `PAIRING_CODE` is supplied.

Optional properties:

| Property | Default | Purpose |
| --- | --- | --- |
| `PAIRING_CODE` | *(none)* | Pair during install. Omit to pair later with `lawhand-agent register`. |
| `SAAS_URL` | `https://getlawhand.com` | API endpoint. |
| `SERVICE_ACCOUNT` | `LocalSystem` | Run the service as a domain account instead. |
| `SERVICE_PASSWORD` | *(none)* | Password for `SERVICE_ACCOUNT`. |

A domain service account is only needed when shares are mounted with the agent
host's own identity. When each share carries a credential from the console's
credential vault, LocalSystem is enough.

### Linux

```bash
tar xzf lawhand-agent-<version>-linux-x86_64.tar.gz
cd lawhand-agent-<version>
sudo ./install.sh --code <pairing code> --url https://getlawhand.com
```

`install.sh` creates the `lawhand-agent` system user, installs the binary in
`/opt/lawhand-agent`, keeps config in `/etc/lawhand-agent` (mode 0700), and
enables the `lawhand-agent.service` systemd unit.

### From source

```bash
pip install -e .[dev]
lawhand-agent register --code <pairing code> --url https://getlawhand.com
lawhand-agent start
```

## Commands

```bash
lawhand-agent register --code CODE [--url URL]  # pair with a tenant
lawhand-agent start                             # run in the foreground
lawhand-agent scan                              # scan every assigned share now
lawhand-agent scan --share-path "\\FS01\Legal"  # one-off scan of a path
lawhand-agent status                            # config + assigned shares
lawhand-agent service install|start|stop|restart|status|remove
```

`service` manages the Windows service or the systemd unit depending on the
platform; `service run` is what the service manager itself invokes.

## Authentication

Share credentials are configured in **Administration → File Shares** and stored
per tenant, encrypted with the tenant token keyring. The agent fetches the
credential for each share it is assigned, over its API-key-authenticated HTTPS
connection, and keeps it in memory only — nothing is written to disk, so
revoking or rotating a credential in the console takes effect on the next poll
without touching the agent.

Supported methods:

| Method | Use |
| --- | --- |
| `ntlm` | Username/password (optionally with a domain). The usual service account. |
| `kerberos` | The agent host's ticket cache; no secret is stored. |
| `guest` | Anonymous access to an already-open share. |
| *(no credential)* | The agent connects as the account its service runs as. |

`clarity-agent register --smb-username/--smb-password` still exists as a local
fallback for shares with no credential attached; it is encrypted at rest with a
key in the agent's config directory.

## Configuration

Config lives in `config.toml` in the agent data directory, which is
`%ProgramData%\LawHand\Agent` on Windows, `/etc/lawhand-agent` on Linux (or
`~/.clarity-agent` for an existing per-user install). Override the location with
`CLARITY_CONFIG_DIR`. Every field also has an environment variable
(`CLARITY_SAAS_URL`, `CLARITY_API_KEY`, `CLARITY_SCAN_INTERVAL`, …).

## Architecture

The agent uses a 3-tier change detection strategy:

1. **Directory mtime gate** — skip directories whose mtime hasn't changed
2. **File mtime comparison** — compare individual file modification times
3. **First-4KB hash** — SHA256 of first 4096 bytes as content fingerprint

Three loops run concurrently: a scan loop on the configured interval, a task
poll loop, and a heartbeat. The task loop handles three kinds of work queued by
the SaaS — `content_fetch` (read one document), `verify_share` (the console's
**Test connection**), and `scan_now` — and every scan reports its outcome back,
which is what fills in the last-scan column and error text in the console.

## Building installers

```powershell
# Windows: exe + MSI into agent\dist (WiX v5 is installed automatically)
.\packaging\windows\build.ps1
.\packaging\windows\build.ps1 -SkipMsi                       # exe only
.\packaging\windows\build.ps1 -SignToolCertThumbprint <hash> # sign both
```

```bash
# Linux: binary + install tarball into agent/dist
./packaging/linux/build.sh
```

Both drive the shared PyInstaller spec at `packaging/lawhand-agent.spec`, so the
two platforms ship the same code with the same entry point.

## Tests

```bash
pip install -e .[dev]
pytest tests -q
```
