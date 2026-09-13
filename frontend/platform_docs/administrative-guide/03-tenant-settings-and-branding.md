---
slug: tenant-settings-and-branding
title: Tenant settings & branding
description: Configure firm identity, defaults, alerts, and feature behavior without surprising users.
order: 30
read_time: 6 min
icon: settings
---

# Tenant settings & branding

Tenant-wide settings shape the experience for every user. Make changes during a communicated window when they alter navigation, billing behavior, generated documents, notifications, or AI routing.

## Firm identity

[Firm Profile](/admin?tab=firm) holds the firm's name, contact details, and branding. It is the first thing to set on a new tenant.

**Account name** is the firm's name of record. Sign-up derives it from the email domain of the first account — a firm that signed up from `painterlaw.com` starts out named "Painterlaw" — so correcting it here is usually the first change a new tenant makes. The name flows to client portal invitations, engagement and intake email, invoices, trust statements, and any template mapped to a Firm profile field.

**Display name** is optional and overrides the account name wherever clients see it. Set it only when the letterhead name differs from the name of record; leave it blank and the account name is used.

Renaming does not rewrite documents already generated or matter numbers already issued — both keep the values they were created with.

[Tenant](/admin?tab=tenant) remains the read-only view of the organization record and plan context. Confirm the tenant there before any bulk or integration action.

## General settings

[Settings](/admin?tab=settings) contains defaults, alert configuration, feature flags, and other controls. Read the description and current value before changing a field. If a control is unfamiliar, test it in a non-production tenant or obtain product guidance first.

Branding may flow into generated documents and customer-facing experiences. Use approved firm assets, accessible contrast, and current contact information. Preview a representative template after a branding change.

Invoice and trust-statement PDFs use the Firm Profile name, logo URL,
address, phone, email, website, and optional PDF footer. Amounts are
rendered in USD. Client portal invoice
downloads use the same settings. A logo that cannot be fetched or decoded does
not block the PDF; the remaining firm identity is still rendered. The invoice
download audit retains the branding values and content hash used for that
response, not a stored PDF copy.

## Feature flags and plan scope

Feature controls are rollout tools, not substitutes for permissions. Enabling a module can expose new navigation and workflows to eligible users; it does not automatically establish the firm's process or train staff.

Before enablement:

- identify the owner and intended audience;
- confirm data and integration prerequisites;
- define the expected workflow and rollback condition;
- update the relevant user-guide chapter; and
- test with representative roles.

## Alerts

Set alert destinations that are actively monitored. Use a firm-controlled address or group rather than one person's mailbox for important operational notices. After saving, perform a supported test or verify the next expected delivery.

## Onboarding

[Onboarding](/onboarding) guides initial tenant setup. Returning to onboarding can help identify incomplete prerequisites, but do not repeat connection or completion steps without understanding their effect on current configuration.
