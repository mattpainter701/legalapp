# Firm Memory source and authorization foundation

## Status

This document describes the additive version 1 backend contract. Generalized
firm-wide search is **off by default** with
`FIRM_MEMORY_GENERAL_SEARCH_ENABLED=false`. The existing matter-scoped SMB
search remains unchanged.

Firm Memory is firm research, not a matter attachment browser. A search may
have no matter filter. A native document may be associated with zero, one, or
many matters and research workspaces. `source_scope: all` means all configured
sources authorized for the current actor; it never means every source row in
the tenant.

## Authorization order

The service evaluates these boundaries in order:

1. The authenticated user must still be active in the request tenant.
2. The user must hold the `search_firm_memory` RBAC capability. The migration
   grants it to existing system Administrators only; firms grant it to other
   roles deliberately.
3. Each optional matter filter must be authorized. A configured matter policy
   may be `firm`, `assigned`, or `restricted`. Restricted matters require an
   explicit user grant. When no new policy exists, a current matter assignment
   is the only accepted legacy authorization signal.
4. Every source must independently allow access through `firm`, `matter`,
   `explicit`, or `native` authorization mode. Explicit denies take precedence
   over allows.
5. Native authorization is supplied by a registered provider. Missing
   providers, timeouts, exceptions, malformed decisions, and other unknown
   states fail closed.

Unauthorized sources are omitted from an `all` response so the source catalog
is not disclosed. If a caller explicitly selects a source it cannot use, the
coverage row is anonymized and reports `unauthorized`.

## Version 1 API

`POST /api/v1/firm-memory/search` accepts:

- a required bounded `query`;
- `source_scope` of `all`, `on_prem`, `cloud`, or `selected`;
- optional `matter_ids`, `source_ids`, and `collection_ids`;
- bounded extension, MIME, and modified-date filters;
- a result limit and optional audit correlation ID.

The response includes an opaque HMAC-derived document identity, source and
index provenance, optional matter/workspace associations, server-issued open
actions, per-source coverage, partial/complete state, and the audit correlation
ID. Query text is not written to the application log; correlation, actor,
source count, result count, and partial state are logged for operations.

Clients must use `complete`, `partial`, and every coverage row when explaining
results. “No matches” is truthful only when `complete=true`. Otherwise the UI
should say that no matches were found in the available sources.

`GET /api/v1/firm-memory/capabilities` separates RBAC entitlement from rollout.
Clients should render the unified research experience only when
`unified_research_available=true`; `search_entitled` alone must not enable it.

`GET /api/v1/firm-memory/sources` returns only sources authorized for the actor
and optional repeated `matter_ids` query parameters. It includes safe source,
provider, legacy-share, collection, and coverage identifiers for UI filters;
it is not an administrative catalog endpoint.

## Current safe adapter

The first adapter searches the existing PostgreSQL `smb_file_index` only when
all of the following are true:

- the configured source is `smb` and points to one existing SMB share;
- its authorization mode is `matter`;
- the request supplies authorized matter IDs;
- existing share/folder bindings constrain every indexed row.

It returns a relative location, never a `file://`, `smb://`, or raw UNC open
URL. Generalized SMB search is not inferred from the indexer's service-account
visibility. Source-level authorization is not represented as native NTFS ACL
trimming.

With the rollout flag off, a matterless query returns authorized sources with
`unsupported/generalized_search_rollout_disabled` coverage. Even with the flag
on, a source without a document-authorized adapter returns `unsupported`
rather than untrimmed metadata.

## Data model

- `firm_memory_sources` configures a source, authorization mode, coverage, and
  optional legacy SMB share adapter.
- `firm_memory_collections` and `firm_memory_collection_sources` provide named
  source groups.
- `firm_memory_source_grants` stores explicit user/role allow or deny policy.
- `firm_memory_matter_policies` and `firm_memory_matter_grants` represent
  firm, assigned, and ethical-wall/restricted matter rules.
- `firm_memory_document_matters` and `firm_memory_document_workspaces` attach
  native document keys to any number of contexts without moving the document.

Every table is tenant-scoped with forced PostgreSQL RLS. Composite foreign keys
prevent cross-tenant source, matter, user, and workspace associations.

## Native provider integration

Implement `NativeSourceAuthorizer.authorize_search`, register it under the
configured `native_authorizer_key`, and return an explicit
`AuthorizationDecision`. The provider must validate the current native
identity and source for the query. This hook is a policy boundary, not yet a
document-level result adapter; an allow does not authorize the SaaS service to
search or return untrimmed native documents.

The OpenSearch node, OCR/extraction workers, Windows SID/ACL evaluator, and
workstation opener are separate integrations and are intentionally absent.
