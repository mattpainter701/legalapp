---
slug: file-share-operations
title: File Share operations
description: Register controlled SMB sources, assign agents, constrain paths, and verify indexing and retrieval.
order: 110
read_time: 8 min
icon: briefcase
---

# File Share operations

[File Shares](/admin?tab=smb) connects approved network file sources through registered agents. The page may expose share configuration, agent status, browsing, indexing, search, and operational health.

## Register an agent and share

Confirm the agent belongs to the intended environment and tenant. Add the narrowest approved share path and a recognizable display name. Avoid drive roots, broad departmental shares, home directories, backup targets, and paths containing unrelated clients.

The service identity used by the agent should have only the read or write permissions required by the supported workflow. Do not place credentials in the display name, notes, or guide.

## Validate the boundary

Browse or inspect the share using redacted or non-sensitive test content. Confirm path normalization, allowed file types, excluded temporary files, symlink or junction behavior, file-size handling, and access errors.

## Index and search

Start indexing during an approved window. Record baseline file counts and monitor progress, failures, and duplicate paths. After indexing, test a known allowed document and a known disallowed location.

File permissions can change after indexing. Establish a process for rescans, deletions, renamed folders, and permission updates so search does not retain content beyond its intended availability.

## Diagnose safely

For an offline agent, check its registered identity, network path reachability, service status, and last health report through the restricted operations procedure. For file errors, preserve the relative path and error category without copying sensitive file contents into support notes.

Disable or remove a share when its business owner, path, tenant, or data classification changes. Verify what indexed data and caches remain under retention policy.
