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

Stable latest-release assets are:

- `https://github.com/mattpainter701/legalapp/releases/latest/download/lawhand-agent-x64.msi`
- `https://github.com/mattpainter701/legalapp/releases/latest/download/lawhand-agent-linux-x86_64.tar.gz`
- `https://github.com/mattpainter701/legalapp/releases/latest/download/SHA256SUMS.txt`

The release workflow publishes these aliases only after Windows and Linux
builds/tests both pass, verifies their checksums exist, and then probes the
public URLs. The aliases resolve to the most recently verified `agent-v*`
release.

### Windows (primary target)

```powershell
msiexec /i lawhand-agent-<version>-x64.msi /qn `
        /norestart

# Run separately in an elevated PowerShell window after the MSI completes:
& 'C:\Program Files\LawHand\Agent\lawhand-agent.exe' register --code '<pairing code>'
```

The MSI installs `lawhand-agent.exe` into `C:\Program Files\LawHand\Agent`,
registers the **LawHand File Share Agent** service (auto-start, restart on
failure), and creates `C:\ProgramData\LawHand\Agent` for config/key/ledger.
Pairing is a separate command so the one-time code is never exposed to Windows
Installer command lines or MSI event logs. Installing a newer MSI directly over
the existing product preserves the enrollment and service configuration.

Optional properties:

| Property | Default | Purpose |
| --- | --- | --- |
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
enables the `lawhand-agent.service` plus the root-owned
`lawhand-agent-update.path` systemd unit.

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
lawhand-agent update --check                   # check fixed official release
lawhand-agent update --apply                   # verify and launch update
lawhand-agent service install|start|stop|restart|status|remove
```

## Logs

Windows services write bounded UTF-8 rotating logs (5 MiB, three backups) under
the same protected ProgramData directory as their enrollment. From an elevated
PowerShell window:

```powershell
Get-Content 'C:\ProgramData\LawHand\Agent\logs\agent.log' -Tail 200 -Wait
```

MSI diagnostics are separate; the portal install block writes those to
`$env:TEMP\lawhand-agent-install.log`. On Linux, application output remains in
journald:

```bash
sudo journalctl -u lawhand-agent.service -n 200 -f
```

Updates use only the fixed official `agent-update.json` release manifest from
the LawHand GitHub repository. The platform asset name and SHA-256 are checked
before staging. Portal-triggered `agent_update` tasks contain only the target
version and manifest identity; they cannot supply a URL, checksum, executable
path, or dry-run flag.
Windows portal updates are automatic only for LocalSystem services. A custom
service account uses the manual overtop MSI path so the existing service
credential remains under Windows Service Control Manager ownership. Linux updates
use the installed root-owned systemd helper. That helper re-verifies the fixed
manifest and immutable versioned asset itself, refuses downgrades and unsafe
archive members, and rolls back if the replacement service is not healthy.

## Upgrade an installed agent

Agents at 0.15.0 or later can be upgraded from **Administration → File
Shares → Agents → Update**. The portal polls queued/in-progress status and the
next heartbeat confirms the running version. Agents older than 0.15.0 need one
manual overtop upgrade before they understand portal update tasks.

On Windows, use the **Copy command** block in **Administration → File Shares →
Agents** from an elevated PowerShell prompt. It restricts downloads to the
official GitHub release path/CDN hosts, validates the manifest, downloads a
version-pinned MSI, verifies SHA-256, and installs the new MSI directly over the
existing product. Do not uninstall first: the block preserves
`%ProgramData%\LawHand\Agent` and skips pairing when an enrollment already
exists.

For a Windows service using a custom account, use that overtop install block.
The late-upgrade schedule discovers and preserves the existing service identity
without reading or resupplying its password. Provide `SERVICE_ACCOUNT` and
`SERVICE_PASSWORD` only for a clean install or an intentional identity change. See
[`packaging/windows/UPGRADE.md`](packaging/windows/UPGRADE.md).

On a packaged Linux host, this version-only command hands the request to the
privileged systemd updater and returns immediately:

```bash
lawhand-agent update --check
sudo lawhand-agent update --apply
sudo systemctl status lawhand-agent-update.service
```

For the one-time pre-0.15 Linux bootstrap, download and verify the latest
tarball, extract it, then run `sudo ./install.sh` without a pairing code. The
existing `/etc/lawhand-agent` enrollment, key, and ledger remain in place.

```bash
set -euo pipefail
LAWHAND_UPDATE_DIR="$(mktemp -d)"
LAWHAND_RELEASE_BASE="https://github.com/mattpainter701/legalapp/releases/latest/download"
curl --fail --location "$LAWHAND_RELEASE_BASE/lawhand-agent-linux-x86_64.tar.gz" \
  --output "$LAWHAND_UPDATE_DIR/lawhand-agent-linux-x86_64.tar.gz"
curl --fail --location "$LAWHAND_RELEASE_BASE/SHA256SUMS.txt" \
  --output "$LAWHAND_UPDATE_DIR/SHA256SUMS.txt"
(cd "$LAWHAND_UPDATE_DIR" && sha256sum --ignore-missing --check SHA256SUMS.txt)
tar -xzf "$LAWHAND_UPDATE_DIR/lawhand-agent-linux-x86_64.tar.gz" \
  -C "$LAWHAND_UPDATE_DIR"
LAWHAND_INSTALLER="$(find "$LAWHAND_UPDATE_DIR" -mindepth 2 -maxdepth 2 \
  -type f -name install.sh -print -quit)"
test -n "$LAWHAND_INSTALLER"
sudo "$LAWHAND_INSTALLER"
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

Three loops run concurrently: a scan loop, a task poll loop, and a heartbeat.

The connector initiates every cloud connection over HTTPS. The SaaS has no
inbound route to the customer LAN: work is delivered through a bounded
20-second long poll, and the agent only opens SMB paths assigned to it. Each
agent credential belongs to one tenant; every queued read is bound again to the
tenant, agent, share, and indexed file before its result is accepted.

The scan loop wakes once a minute and scans the shares that are due according
to their own cron schedule from the admin console (`0 */6 * * *` by default), so
a share set to run nightly is not walked every few hours. A share with no usable
schedule falls back to the agent-wide `scan_interval_minutes`. Scan times are
held in memory, so a restarted agent scans once and then resumes the schedule.

The task loop handles five kinds of work queued by the SaaS — `content_fetch`
(read one document), `verify_share` (the console's **Test connection**), and
`scan_now`, `local_search` (a bounded query against the local control index),
plus `agent_update` for the fixed official updater — and every scan
reports its outcome back, which is what fills in the last-scan column and error
text in the console.

## Security boundary

This is a narrow application relay rather than a general VPN or Tailscale node.
It provides comparable outbound-only reachability for the approved file-share
workflow without exposing arbitrary tenant hosts or ports. Metadata and short
snippets are cached in the tenant database; requested document text uses a
short-lived Redis handoff and bounded LLM context and is not stored in the SMB
index. Each fetch is recorded in the tenant access log.

`local_search` is matter-scoped by the SaaS task and share/folder-scoped by the
agent. It returns only relative paths, bounded snippets, optional page hints,
opaque correlation data, and aggregate index state. Query text is processed
in memory and is not written to the agent log or returned as telemetry.
Correlation IDs, counts, duration, and partial/degraded state are safe to use
when correlating a validation run. The agent has no inbound `/metrics`
endpoint; Tailscale is optional private administrator reachability, not the
product data path or a metrics exporter.

Tenant-vault SMB passwords are encrypted in the SaaS, delivered only to the
assigned agent over TLS, and held in memory. Optional local fallback credentials
are encrypted on disk. For deployments that require cryptographic device
identity comparable to a private mesh, add mTLS/rotating device certificates
and require code-signed installers as release gates; the current agent uses a
high-entropy tenant-bound API key and normal public-PKI HTTPS.

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

To publish, merge the tested change to `main`, then tag the exact version from
`clarity_agent/__init__.py` (currently `agent-v0.15.3`) and push that tag. The
workflow rejects a mismatched tag and does not publish either platform unless
both builds finish successfully.

Tagged releases use an Azure Artifact Signing **Public Trust** certificate
profile with GitHub OIDC. The `agent-release` environment must provide the
Azure client/tenant/subscription identifiers and the non-secret account,
endpoint, and certificate-profile variables beginning with `WINDOWS_SIGNING_`,
including `WINDOWS_SIGNING_EXPECTED_SUBJECT`; the federated identity needs the
Artifact Signing Certificate Profile Signer role. CI builds
the EXE, signs and timestamps it through the service, verifies it, builds the
MSI around that signed EXE, signs and timestamps the final
MSI, and verifies both Authenticode signatures before upload. Pull-request and
ordinary `main` validation builds remain possible without release signing
configuration. Local developer builds may continue using
`-SignToolCertThumbprint`.

The signing job is the only job granted `id-token: write`. It uses the protected
`agent-release` environment with deployment creation disabled; restrict that
environment to tags matching `agent-v*`. Its Azure federated credential must
match this exact OIDC subject (with no wildcard):
`repo:mattpainter701/legalapp:environment:agent-release`.

## Tests

```bash
pip install -e .[dev]
pytest tests -q
```
