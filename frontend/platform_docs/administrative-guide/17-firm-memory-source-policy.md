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

## The matter binding is the boundary

LawHand reads a file share through one service account, and firm logins do not
map one-to-one onto Windows accounts, so file permissions cannot decide who may
see a document. What decides it is the matter: who is authorized on it, and
which folder on the share is bound to it.

Two consequences follow, and neither is temporary:

- **Bind every share you want searched.** An unbound share is reported as
  unsupported and is never searched, whatever its source policy says, because
  a service account that can read the whole share gives the search nothing to
  scope itself by.
- **The folder binding is a security control, not a convenience.** Anyone
  authorized on a matter can find anything under that matter's bound folder.
  Bind the narrowest folder that holds the matter's files, and keep material
  that must not follow the matter outside it.

Every result is re-checked against the bound folders of the actor's authorized
matters before it leaves the server, on both the full-text and the fallback
path, so a misconfigured agent cannot widen what a search returns.

## Know what a matterless search covers

Leaving the matter filter empty does not search everything. A matter-bound
source is searched across the matters that user is already authorized on, under
the same policy a chosen matter would go through, and nothing else. A user with
no authorized matter on a share sees that share reported as not covered rather
than silently missing.

On-premises results come from the firm's own search node, so they carry
document text, passages and page numbers. If that node cannot be reached, the
search falls back to the file name and preview index kept in LawHand and says
so; that fallback is never reported as complete coverage.

If your firm has enabled per-user native authorization, a firm-wide search also
needs agent 0.17.0 or newer: an older agent cannot bind a multi-matter request
to its signed authorization, so it is reported as not covered rather than
searched with a weaker binding. Firms on the service-account model are not
affected by that version floor.

## Read coverage as part of the answer

Search coverage is part of the result, not an internal diagnostic. When any
selected authorized source is partial, stale, offline, indexing, or
unsupported, users must be told that the response covers only available
sources. “No matches” is appropriate only for a complete response. Every
incomplete response states in one sentence why it is incomplete.

## Roll out the integration

For schema, rollout, and integration details, see
`docs/firm-memory-source-authorization.md`.
