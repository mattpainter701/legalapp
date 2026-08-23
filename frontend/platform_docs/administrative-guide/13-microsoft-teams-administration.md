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
