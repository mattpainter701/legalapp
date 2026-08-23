---
slug: prompt-management
title: Prompt management
description: Review tenant prompt overrides, preserve required variables and gates, test changes, and recover safely.
order: 90
read_time: 8 min
icon: sparkles
---

# Prompt management

[Prompts](/admin?tab=prompts) controls supported tenant-level prompt overrides by plugin and skill. A prompt change can alter outputs across many matters, so treat it as a production configuration change.

## Select the right scope

Confirm the plugin, skill, current source, and whether an override already exists. Prefer the platform default when it meets the firm's need; unnecessary overrides make future improvements harder to adopt.

Required variables such as practice profile, matter context, sources, or user input must remain intact. Do not rename or remove a variable unless the product explicitly supports that change.

## Write a safe override

A useful override defines desired structure, firm terminology, jurisdictional constraints, and review expectations. It must not instruct the model to fabricate citations, conceal uncertainty, bypass permissions, send external messages automatically, or ignore product gates.

Do not embed client facts, credentials, provider tokens, private infrastructure, or one matter's strategy in a tenant-wide prompt.

## Test before saving

Use representative, redacted test input. Check normal, missing-data, ambiguous, and adverse cases. Review:

- required sections and formatting;
- use of source material and citations;
- uncertainty and escalation behavior;
- protection against unsupported conclusions;
- token or latency impact; and
- compatibility with downstream review and export.

Record the reason, owner, test cases, approval, and rollback plan. After saving, run a limited production smoke test with non-sensitive content.

## Remove or recover

Deleting an override should return the skill to the platform default. Confirm the displayed source and re-test. If a change produces unsafe or unusable output, restore the last approved text or remove the override, then preserve examples and request identifiers for investigation.
