---
slug: file-share-operations
title: File Share operations
description: Register controlled SMB sources, store share credentials securely, constrain paths, and verify indexing and retrieval.
order: 110
read_time: 8 min
icon: briefcase
---

# File Share operations

[File Shares](/admin?tab=smb) connects approved network file sources through registered agents. It is built for shares that stay on your network — large, long-lived document stores that are not moving to the cloud — so their contents can be searched and pulled into matter context and research without being copied wholesale. Only metadata and a short snippet per file are synced on a schedule; full document text is fetched on request and recorded in the access log.

## Install and register an agent

Generate a pairing code on the **Agents** tab. The tab also shows copy-ready installer commands for the machine that will run the agent: on Windows, one verified PowerShell block installs or upgrades the auto-starting service and then registers it as a separate step; on Linux, the tarball installs a systemd service. The agent must run on a host that already has network access to the share; it needs no inbound firewall rule, only outbound HTTPS.

Confirm the agent belongs to the intended environment and tenant before assigning shares to it. Pause or revoke an agent from the same tab when its host is decommissioned.

## Store share credentials

The **Credentials** tab holds the identities agents use to mount shares. Each credential is scoped to your tenant, encrypted at rest with the tenant key, and delivered only to your paired agents over their authenticated connection — the password is never displayed again and is never returned by the API. Choose the method that matches your environment:

- **Username and password (NTLM)** for a dedicated service account, with an optional domain.
- **Kerberos** where the agent host holds a ticket; no secret is stored.
- **Guest** only for shares that are already open.
- **No credential**, which mounts the share as the account the agent service runs as.

Restrict a credential to a single agent when the secret should only ever reach one office or one file server. Give the service identity the minimum read permissions the workflow needs, and never place credentials in a share display name, credential name, or support note.

Rotating a password is done here: edit the credential, enter the new password, and the agent picks it up on its next poll. Deleting a credential leaves the shares that used it running under the agent's own identity, so re-check those shares afterwards.

## Add a share

Add the narrowest approved path and a recognizable display name. Avoid drive roots, broad departmental shares, home directories, backup targets, and paths containing unrelated clients. In the same form, choose the credential, the file types to index, exclusion globs (temporary files, archives, backup folders), the maximum folder depth, and the scan schedule.

Use **Test connection** immediately after adding the share. The agent mounts the path with the configured credential and reports back what identity it used and whether it could list the folder, so a wrong password or a missing permission surfaces at once instead of as an empty index hours later.

## Index and search

Start indexing during an approved window, using **Scan now** rather than waiting for the schedule. Record baseline file counts and monitor progress and failures — the share row shows the last scan time, its status, the file count, and the error text when a scan fails. After indexing, test a known allowed document and a known disallowed location.

File permissions can change after indexing. Establish a process for rescans, deletions, renamed folders, and permission updates so search does not retain content beyond its intended availability.

## Diagnose safely

For an offline agent, check its registered identity, network path reachability, service status, and last heartbeat through the restricted operations procedure. For a share that stopped indexing, read the scan error on the share row and re-run **Test connection** to separate a credential problem from a path or permission problem. For file errors, preserve the relative path and error category without copying sensitive file contents into support notes.

Disable or remove a share when its business owner, path, tenant, or data classification changes. Verify what indexed data and caches remain under retention policy.
