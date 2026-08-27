---
slug: zoom-phone-administration
title: Zoom Phone administration
description: Connect the approved Zoom account, verify scopes and webhooks, and validate call-feed visibility and routing.
order: 140
read_time: 8 min
icon: plug
---

# Zoom Phone administration

[Integrations → Zoom](/admin?tab=integrations&integration=zoom) manages Zoom service authorization and Zoom Phone readiness for the call-intake workflow.

Zoom Phone and Zoom Meetings are separate grants. Phone reads completed call history and detail for intake. Meetings uses user-profile and meeting read/write permission to create and manage meeting links. Enabling one does not automatically enable the other.

## What Zoom Phone can expose

The current Phone workflow can receive provider call ID, caller and recipient names and numbers, direction, result/status, duration, timestamp, and the provider's call record. If Zoom supplies them for the connected account and call, LawHand may also receive summary text, transcript text, a recording URL, and a transcript URL.

LawHand normalizes this into a tenant communication record that can be matched to a contact and linked to a matter. The ingestion path can retain transcript text and provider URLs; it does not itself initiate or record the phone call. See [Integration permissions and data visibility](/guide/integration-data-visibility) for the complete cross-provider disclosure.

## Confirm the account and app

Use the firm's approved Zoom administrator and account. Verify the account identifier, application type, redirect configuration, required administrative scopes, active status, and intended call population.

Webhook secret tokens and client credentials are secrets. Enter them only in the designated protected fields and store recovery material in the approved secret manager.

## Authorize and test

Complete the provider grant, then review displayed status for authorization, scopes, webhook configuration, credential health, and account alignment. Make an approved demo call and verify:

- the call appears in [Call Intake](/intake/dashboard);
- caller and direction metadata are correct;
- only the intended account's calls are visible;
- matching does not attach an unrelated contact or matter; and
- follow-up routing behaves as configured.

## Webhooks and refresh health

Webhook delivery and OAuth refresh health are distinct. A connected account can still have stale calls if event delivery is broken; successful events do not guarantee future API refresh.

After changing the Zoom app, secret token, grant, or scopes, perform both a webhook test and an API-backed status check.

Completed-call webhooks are signature-checked and matched to the connected Zoom account and LawHand tenant. LawHand then retrieves the exact call through the authorized API instead of trusting the event body as the final record.

## Consent and retention

Before enabling production intake, document the firm's lawful basis and notice/consent procedure for call recording and transcription in every applicable jurisdiction. Define who may view raw call details, transcripts, and recording links, and how long imported communications and provider payloads are retained.

Use a demo call that contains no client confidential information to verify scope. Confirm the imported participants, timestamps, result, duration, transcript/recording behavior, contact matching, matter linking, and role-based visibility.

## Disconnect or contain

Communicate the impact before disconnecting. Stop dependent call workflows, disconnect in LawHand, revoke provider access when required, and verify no new calls arrive.

Unexpected unrelated call history, cross-tenant data, or unexplained webhook traffic is a security issue. Preserve identifiers and timestamps without copying call content into an unrestricted ticket.

Disconnecting and provider revocation stop future imports after they take effect. They do not automatically delete communication records, transcripts, URLs, or audit history already stored in LawHand; apply the tenant's retention and supported deletion process separately.
