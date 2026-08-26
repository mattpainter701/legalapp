---
slug: users-roles-and-licensing
title: Users, roles & licensing
description: Provision people with least privilege and keep access aligned with employment and work.
order: 20
read_time: 7 min
icon: users
---

# Users, roles & licensing

User identity, permissions, and licensing solve different problems. Treat each one explicitly: the account says **who**, roles say **what they may do**, and licensing says **which paid capabilities are available**.

## Invite and maintain users

Open [Users](/admin?tab=users) to invite a colleague. Use their individual business email and correct name. Select the narrowest initial role, then add responsibilities after the account is confirmed.

Deactivate people who should no longer enter the tenant. Reactivation is preferable to creating a second identity for a returning person because it preserves history. Before changing or removing the last administrator, confirm that another tested administrator account remains.

## Assign roles

Open [Roles](/admin?tab=roles) to inspect capability bundles. Prefer reusable job-function roles over one-off permission collections. Avoid granting administrative settings merely to solve a missing matter assignment or module problem.

When changing a role:

1. compare the person's actual duties with the capabilities;
2. check for incompatible financial, approval, or administrative powers;
3. save the assignment; and
4. verify the result with the affected user.

The legacy `admin`, `accountant`, and `user` roles still influence high-level navigation. Custom capabilities should be tested against the specific workflow they are intended to permit.

## Control connected assistants

The **Connected assistants** column in [Users](/admin?tab=users) governs Workspace MCP for each person: whether they may connect an external assistant such as Claude, ChatGPT, or Codex to their own workspace. Access is per user, so grant it to the people whose work needs it rather than tenant-wide by default. The column also shows whether the user has Privacy Mode on, which they must turn off themselves before a connection can be made.

Set the policy applied to people who arrive later under [Settings](/admin?tab=settings): **Enable Workspace MCP for new users** decides the default for each invited or directory-synced account. Choose that default deliberately — it is the setting that decides whether a new hire can connect an outside assistant on their first day.

Turning the toggle off ends the user's connected assistants. A user can also review and revoke their own connections from their profile, but an administrator's decision is the one that holds.

## Allocate licenses

Open [Licensing](/admin?tab=licensing) to see seat availability and activation. A user may exist for record continuity without holding an active standard license. Premium AI is a separate entitlement and should be assigned to people whose work requires it.

Monitor inactive licensed users and avoid using shared accounts to conserve seats. If a plan or seat change has a commercial impact, confirm it under [Subscription](/admin?tab=billing).

## Joiner, mover, leaver checklist

- **Joiner:** confirm identity, role, modules, license, integration prerequisites, and initial training.
- **Mover:** remove old responsibilities before adding new ones; review matter access and approval authority.
- **Leaver:** deactivate promptly, preserve authored records, transfer owned work, revoke connected access — including Workspace MCP assistants — and verify completion.

Never send temporary passwords, tokens, or recovery material through the invitation notes or guide content.
