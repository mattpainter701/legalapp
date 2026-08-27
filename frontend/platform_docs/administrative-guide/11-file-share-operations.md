---
slug: file-share-operations
title: File Share operations
description: Register controlled SMB sources, store share credentials securely, constrain paths, and verify indexing and retrieval.
order: 110
read_time: 8 min
icon: briefcase
---

# File Share operations

[Integrations → File shares](/admin?tab=integrations&integration=file-shares) connects approved network file sources through registered agents. It is built for shares that stay on your network — large, long-lived document stores that are not moving to the cloud — so their contents can be searched and pulled into matter context and research without being copied wholesale. Only metadata and a short snippet per file are synced on a schedule; full document text is fetched on request and recorded in the access log.

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

Use **Edit** to correct the UNC path or move the share to another registered agent. A path or agent change clears the old scan/verification result and removes the prior file metadata from active search until the new location is scanned, preventing stale matter context from surviving a move.

## Index and search

Start indexing during an approved window, using **Scan now** rather than waiting for the schedule. Record baseline file counts and monitor progress and failures — the share row shows the last scan time, its status, the file count, and the error text when a scan fails. After indexing, test a known allowed document and a known disallowed location.

File permissions can change after indexing. Establish a process for rescans, deletions, renamed folders, and permission updates so search does not retain content beyond its intended availability.

## Diagnose safely

Start with **Status**. Its counts describe the tenant's registered agents,
configured shares, stored credentials, and indexed files even if retrieval has
been disabled by server configuration. The retrieval badge and warning are a
separate signal: disabled retrieval prevents indexed file-share results from
being used in search and matter context, but it does not erase the operational
inventory.

Use **Activity** as the tenant-scoped operational timeline. It combines agent
registration and heartbeats, agent updates, share configuration, scans and
connection tests, credential creation, delivery and verification, and audited
full-document access. Passwords, API keys, and document contents are never
included. A successful connection test followed by a failed scan usually
means the server accepted the SMB identity but rejected or could not process
the later metadata sync.

For an offline agent, check its registered identity, network path reachability, service status, and last heartbeat through the restricted operations procedure. For a share that stopped indexing, read the scan error on the share row and re-run **Test connection** to separate a credential problem from a path or permission problem. If the connection test succeeds but sync reports HTTP 422, the share credentials are working and the file-metadata payload needs a compatible server or agent update. For file errors, preserve the relative path and error category without copying sensitive file contents into support notes.

Disable or remove a share when its business owner, path, tenant, or data classification changes. Verify what indexed data and caches remain under retention policy.
