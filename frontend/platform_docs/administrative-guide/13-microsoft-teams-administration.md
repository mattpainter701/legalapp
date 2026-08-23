---
slug: microsoft-teams-administration
title: Microsoft Teams administration
description: Link matters to the correct Teams channels, verify membership and permissions, and maintain mappings through change.
order: 130
read_time: 7 min
icon: users
---

# Microsoft Teams administration

[Teams](/admin?tab=teams) manages matter-to-channel links and shows existing mappings. The Teams configuration surface may also be opened inside Microsoft Teams after administrator consent.

## Prerequisites

Complete Microsoft authorization under [Integrations](/admin?tab=integrations) with the approved organization account and required scopes. Confirm that the team, channel, matter, and intended users already exist.

Teams is an explicit addition to the Microsoft grant. Its requested permissions can read basic team and channel information, send channel messages, read and write chats, and send Teams activity notifications. Channel creation is added only when the organization opts into the matter-channel creation workflow. Review the full Microsoft disclosure in [Integration permissions and data visibility](/guide/integration-data-visibility).

## Current data flow

LawHand resolves the configured team and channel, posts approved workflow messages or cards, and records binding and delivery state needed to avoid duplicate or misdirected delivery. When matter-channel creation is enabled, LawHand can create the requested channel and store its provider identifiers.

Microsoft's permission boundary and LawHand's matter authorization are independent. A Teams member may lack LawHand matter access, and a LawHand matter member may lack Teams membership. Test both systems before sending real matter content.

## Link a channel

Select the matter and exact team/channel combination. Check for similarly named teams, archived channels, private or shared channel behavior, and membership. Save one authoritative mapping for the intended collaboration space.

Test with:

1. an authorized matter user who is a channel member;
2. an authorized matter user who is not a channel member;
3. a channel member without matter access; and
4. an administrator reviewing the mapping.

The desired result depends on product policy, but no test identity should gain unintended matter content.

## Maintain links

Review mappings after a matter closes, a team is renamed or archived, channel membership changes, or the responsible group changes. Remove stale mappings through the supported control and verify the Teams experience no longer presents the matter.

Do not solve a membership problem by linking the matter to a broader channel. Treat an incorrect channel mapping as a potential disclosure: stop use, correct or remove the link, determine what was visible, and follow the incident process when necessary.

## User notice and verification

Tell affected users what classes of matter updates may be sent to Teams, whether messages are authoritative records or notifications, and where the official client file remains. Do not include more matter detail in a card or notification than the channel audience needs.

After authorization or a mapping change, verify the grant owner, displayed scopes, team/channel identifiers, membership, a non-sensitive test delivery, deduplication behavior, and the removal path. Revoking Microsoft access stops future delivery but does not remove messages already posted to Teams or records already retained in LawHand.

## Notification routing

Beyond per-matter channel links, [Teams](/admin?tab=teams) carries firm-wide routing: each notification event LawHand can raise may be pointed at one team and channel. A matter linked to its own channel always posts there instead, so a matter-specific link overrides the firm-wide default for that matter.

Only events LawHand actually raises can be routed. A route saved against an unrecognized event is rejected rather than stored, because a stored route that can never fire is indistinguishable to an administrator from a broken integration.

## Teams voice (Teams Phone) call capture

Firms whose telephony runs on Teams Phone can have inbound calls captured into the intake dashboard alongside Zoom Phone calls. The two providers share one feed, one set of follow-up tasks, and one export. Outbound and internal Teams calls are not captured.

### How it differs from Teams chat

Teams chat features use the delegated Microsoft grant an administrator authorized under [Integrations](/admin?tab=integrations). Microsoft exposes call records only through an **application** permission, `CallRecords.Read.All`, which has no delegated equivalent. Voice capture therefore runs on a separate application-only credential and requires its own administrator consent. Enabling voice does not widen the chat grant, and disabling it does not affect chat.

Call records cover call metadata — the numbers, the participants, the timing, the outcome. They are not recordings or transcripts.

### Setup

Setup is three ordered steps on the Voice tab:

1. **Name the Microsoft Entra directory.** Supply the directory (tenant) ID from Entra admin center → Overview. The shared `common` endpoint cannot issue an application-only token, so it is rejected rather than saved.
2. **Grant the application permission.** A Microsoft 365 global administrator consents once, through the link the panel builds for your directory. `CallRecords.Read.All` is the only permission voice capture uses.
3. **Enable capture and start live notifications.** Microsoft validates LawHand's notification URL before it begins sending. The panel shows that URL for firms that need it recorded in a change ticket.

Firms that prefer to own the application registration can register a single-tenant Entra app holding only `CallRecords.Read.All` and supply its credentials; otherwise the LawHand application is used.

### Two feeds, deliberately

Captured calls arrive two ways. Change notifications from Microsoft deliver a call within moments of it ending. A separate hourly pass over the Teams PSTN usage report re-reads the same window and fills anything the notification path dropped. Microsoft publishes that usage report with a lag, which is exactly why it is the backstop and not the primary feed.

Both feeds converge on the same record: a call captured twice is stored once. If live notifications lapse, capture keeps working through the hourly pass — slower, but uninterrupted. The Voice tab distinguishes these two states rather than reporting both as "on".

### Verification and maintenance

Use the connection test to prove the credential and permission before waiting on a real call; it reads the last 24 hours of usage without changing anything. The manual import re-runs the reconciliation pass over the last seven days.

Microsoft expires a call-record subscription after roughly three days. LawHand renews it well before that on its own schedule; a renewal that fails is reported on the Voice tab, and capture continues through the hourly pass in the meantime. Re-pointing the integration at a different Entra directory invalidates the existing subscription, so LawHand clears it rather than renewing a subscription in a directory the firm no longer uses.

Disabling voice capture removes the subscription at Microsoft, not just LawHand's willingness to store what arrives. Calls already captured remain in the intake dashboard and follow that data's normal retention.
