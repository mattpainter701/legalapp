---
slug: support-and-escalation
title: Support and escalation
description: File a severity-classified support request, understand acknowledgement objectives, and keep secrets and privileged content out of the record.
order: 190
read_time: 5 min
icon: clock
---

# Support and escalation

[Support](/admin?tab=support) files a support request that is classified, recorded, and bound to a published acknowledgement objective. Email reaches the same queue, but a request filed here returns the objective and the policy version in writing, so there is no ambiguity later about when the request was picked up or which policy applied.

The same policy is published publicly at `/support`, and both surfaces read it from one source. If the figures on this page and the public page ever disagree, the published policy is authoritative.

## Choose the severity honestly

Severity drives the acknowledgement objective and the first owner. The tab shows the current definition for each level as you select it.

- **S1** — confirmed or credibly suspected confidentiality breach, destructive data-integrity event, or production-wide unavailability with no safe workaround.
- **S2** — material production degradation or a blocked critical workflow affecting multiple authorized users with no reasonable workaround.
- **S3** — non-critical defect with a workaround, an isolated integration problem, or a question requiring investigation.
- **S4** — how-to request, cosmetic issue, or non-urgent enhancement feedback.

Overstating severity does not make a request move faster; it moves the wrong people onto it and delays the requests that genuinely need them. Understating a confidentiality or data-integrity concern is the more serious error — if you are unsure whether something is S1, file it as S1 and say why you are unsure.

## What an acknowledgement objective is

An acknowledgement objective is the time within which LawHand aims to pick the request up and own it. It is **not** a resolution time, and it is not a service-level agreement, warranty, or service-credit commitment unless signed customer terms expressly incorporate it.

Objectives run inside standard coverage hours. A request filed outside those hours enters the next covered period, except for S1, which uses the emergency channel identified in your order form.

## Keep secrets and client content out of the request

Support records are stored without secrets or unnecessary customer content, and submissions are checked before they are accepted. A request containing something that looks like a credential is rejected with an explanation rather than stored.

Do not paste:

- passwords, API keys, tokens, or authorization codes;
- privileged or confidential client material;
- full document contents.

Instead, describe what you expected, what happened, roughly when it happened with your time zone, how many people are affected, and whether a workaround exists. An identifier and a description are enough to start work.

## After filing

The request appears in the list on the same tab with its severity, status, acknowledgement due time, and the time it was filed. Status moves through acknowledged, mitigated, and resolved as the request is worked; those transitions are recorded rather than edited in place.

Where an issue affects shared service rather than only your firm, it may also be published as a sanitized public incident. Public incident updates never carry tenant-specific detail, so continue to track your own request here.
