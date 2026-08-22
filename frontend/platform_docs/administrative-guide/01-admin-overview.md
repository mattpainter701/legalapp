---
slug: admin-overview
title: Administrator overview
description: Operate the tenant deliberately, separate duties, and know where configuration lives.
order: 10
read_time: 5 min
icon: layout
---

# Administrator overview

The Administration portal controls tenant-wide access, commercial settings, integrations, and AI infrastructure. Changes here can affect every user, so use named administrator accounts and document consequential decisions.

## Administrative map

- [Admin Guide](/admin?tab=guide) — search operating guidance and jump directly to each control.
- [Users](/admin?tab=users) — invite, activate, deactivate, and update people.
- [Roles](/admin?tab=roles) — manage permission bundles and assignments.
- [Licensing](/admin?tab=licensing) — allocate seats and premium AI access.
- [Subscription](/admin?tab=billing) and [Usage](/admin?tab=usage) — review commercial status and consumption.
- [Tenant](/admin?tab=tenant) and [Settings](/admin?tab=settings) — maintain firm identity, defaults, branding, alerts, and feature controls.
- [Integrations](/admin?tab=integrations) — authorize approved cloud services and inspect readiness.
- [MCP Servers](/admin?tab=mcp), [Prompts](/admin?tab=prompts), [Cloud Search](/admin?tab=cloud-search), and [File Shares](/admin?tab=smb) — govern AI tools and sources.

Your plan may intentionally hide features that do not apply to the tenant. Accountant access is limited to the finance-oriented administrative tabs.

## A safe change pattern

1. Define the operational reason and affected users.
2. Confirm that you are in the correct tenant.
3. Record the current setting when rollback may be necessary.
4. Make the smallest change that achieves the goal.
5. Test with a non-administrator account when user visibility is involved.
6. Record who approved the change and when it was verified.

## Keep sensitive operations elsewhere

This guide is delivered with the web application. It must not contain secrets, private keys, recovery codes, customer-specific configuration, infrastructure addresses, exploit details, or incident response procedures. Store privileged operational runbooks in your approved restricted system.

## Review cadence

At least periodically, review active users, administrator assignments, licensed seats, connected services, usage anomalies, feature settings, and failed integration health checks. Also review immediately after staff departures, vendor changes, suspected compromise, or major plan changes.
