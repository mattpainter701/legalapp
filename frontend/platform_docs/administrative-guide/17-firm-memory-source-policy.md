---
slug: firm-memory-source-policy
title: Firm Memory Source Policy
description: Configure authorization, rollout, and truthful coverage for Firm Memory sources.
icon: shield
order: 170
read_time: 6 min
---

# Firm Memory Source Policy

## Configure the authorization boundary

Firm Memory is designed to search authorized firm knowledge across configured
sources. Selecting **All** never bypasses permissions: it means all sources the
signed-in user is allowed to search. A matter is an optional research filter.

The generalized feature is currently an administrator/developer foundation
and is off by default. Existing matter-scoped file-share search continues to
work as before.

Before enabling a source, an administrator must decide:

- who receives the `search_firm_memory` role capability;
- whether the source is firm-wide, matter-bound, explicitly granted, or uses a
  native authorization provider;
- which source collections should contain it;
- whether any associated matters use assigned-only or restricted/ethical-wall
  policy;
- whether its coverage is actually ready, partial, indexing, stale, offline,
  or unsupported.

Unknown authorization is always denied. A missing native provider is not a
temporary firm-wide allow. Restricted matters require explicit user grants,
and explicit source denies override allows.

Do not enable generalized SMB results merely because the LawHand file-share
service account can read a file. The current source foundation does not perform
per-user NTFS ACL trimming. Until the native ACL integration is installed and
validated, keep generalized SMB sources off or matter-bound.

Search coverage is part of the result, not an internal diagnostic. When any
selected authorized source is partial, stale, offline, indexing, or
unsupported, users must be told that the response covers only available
sources. “No matches” is appropriate only for a complete response.

## Roll out the integration

For schema, rollout, and integration details, see
`docs/firm-memory-source-authorization.md`.
