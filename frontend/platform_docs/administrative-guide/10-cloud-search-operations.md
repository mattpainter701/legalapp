---
slug: cloud-search-operations
title: Cloud Search operations
description: Bind approved sources, test retrieval, monitor metadata, synchronize deliberately, and clear caches safely.
order: 100
read_time: 8 min
icon: network
---

# Cloud Search operations

[Cloud Search](/admin?tab=cloud-search) exposes connection status, test search, synchronization, indexed metadata, and cache controls for approved cloud sources.

## Establish the source boundary

Complete provider authorization and the intended storage binding under [Integrations](/admin?tab=integrations) first. Confirm the site, drive, or source boundary with the information owner. Broad access at the provider should not become broad search scope by accident.

## Test retrieval

Use a distinctive, non-sensitive query with a known document. Check that the result comes from the expected source and that accounts with different permissions see only what they should.

Test both positive and negative cases:

- an authorized user finds an expected document;
- an unauthorized user cannot find it;
- similarly named documents retain correct source metadata; and
- deleted or moved content behaves according to synchronization and retention expectations.

## Synchronization and metadata

Review indexed metadata before forcing a synchronization. A sync can increase provider load and search churn. Capture the reason and baseline counts, then verify completion, errors, and representative results.

Metadata views help diagnose stale paths, unexpected sources, missing titles, and indexing gaps. They are operational signals, not a substitute for source-system permission review.

## Clear cache

Clear search cache only for a defined reason such as verified stale results after a permission or binding change. Expect temporary performance or availability effects and run post-clear tests.

Unexpected cross-tenant, cross-site, or unauthorized results are a stop-work security issue. Preserve the query, user, time, result metadata, and request ID; restrict affected access and follow the incident process.
