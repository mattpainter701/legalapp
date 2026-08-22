---
slug: billing-usage-and-governance
title: Billing, usage & governance
description: Monitor commercial health, investigate anomalies, and keep administrative evidence.
order: 60
read_time: 6 min
icon: chart
---

# Billing, usage & governance

Commercial controls and operational governance meet in the admin portal. Review them together: a cost anomaly may indicate a workflow change, configuration error, compromised credential, or simply legitimate growth.

## Subscription and billing

Use [Subscription](/admin?tab=billing) to review plan and billing status. Confirm authorization before changing paid capacity or commercial configuration. Avoid making seat and billing changes under a shared administrator identity.

## Usage

[Usage](/admin?tab=usage) provides tenant and per-user consumption views. Compare like-for-like date ranges and consider matter volume, document size, integration retries, and premium model access.

When usage is unexpected:

1. identify the affected user, tool, and period;
2. check for retries, automation loops, or recently enabled integrations;
3. preserve relevant request and error identifiers;
4. contain exposed keys or access if compromise is plausible; and
5. escalate through the approved operational or security process.

Do not deactivate a user or delete evidence solely because a chart looks unusual.

## Administrative evidence

Keep a lightweight record for consequential changes: reason, approver, operator, previous value, new value, timestamp, validation, and rollback result when applicable. Use the audit facilities available in the product and retain complementary evidence in your restricted operations system.

## Periodic review

A practical review covers:

- active and dormant users;
- administrators and powerful custom roles;
- standard and premium licenses;
- connected providers and granted scopes;
- MCP keys and tool allowlists;
- search and file-share boundaries;
- billing status and usage anomalies; and
- alert delivery and ownership.

Update the corresponding Markdown chapter whenever a product workflow or setting changes. Documentation is part of the feature's acceptance criteria, not a cleanup task after release.
