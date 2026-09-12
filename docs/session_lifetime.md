# Session lifetime

How long a signed-in firm session lasts, what ends one early, and which knobs
change that. The implementation is `backend/app/services/session_policy.py`;
this page is the operator's view of it.

## The shape of a session

Authentication is cookie-only: an access token (`ACCESS_TOKEN_EXPIRE_MINUTES`,
30) and an opaque, single-use refresh token, both `httpOnly`. The SPA never
reads either, and rotates silently on a 401. Refresh tokens belong to a
*family* — a rotation chain — tracked in Redis, and replaying a consumed token
revokes the whole chain.

Nothing about authorisation is cached. Every request re-loads the user and
re-reads capabilities, plan and billing tier from the database; the matching
JWT claims are carried but never trusted for an authorisation decision. A
deactivated user, a revoked capability or a suspended tenant takes effect on
the next request. That is why none of the bounds below concern themselves with
entitlement changes — those were never the problem.

## The three bounds

| Bound | Setting | Default | Enforced by |
| --- | --- | --- | --- |
| Idle | `SESSION_IDLE_TIMEOUT_HOURS` | 12 h | The TTL on each rotating refresh token; every rotation restarts it |
| Absolute | `SESSION_ABSOLUTE_TIMEOUT_HOURS` | 720 h (30 d) | `family_issued_at`, carried unchanged through every rotation |
| Session epoch | `users.sessions_valid_after` | unset | An access token's `iat`, and a chain's origin |

They are separate because none implies the others:

- The **idle** bound is a TTL, so every rotation renews it. A session used once
  a day never reaches it.
- The **absolute** bound is therefore impossible to express as a TTL. It rides
  on the chain's origin timestamp, which rotation copies rather than resets.
- Both are satisfied by a chain that is young and in active use — which is
  exactly the chain an attacker holds. Only the **epoch** ends that, because it
  is set by a person deciding to end it.

A chain refused for any of these is revoked, not merely declined, so a live
successor on another device dies with it.

## What sets the epoch

- **A password reset** (`POST /api/auth/reset-password`). This is the action
  people take when they believe someone else is in their account, so it must
  sign that someone out.
- **Signing out everywhere** (`POST /api/auth/sessions/revoke-all`), reachable
  from Profile → "Sign out everywhere else". The caller is re-issued rather
  than signed out with everyone else.

The epoch is stamped to a whole second, which leaves a deliberate sub-second
window: a credential minted earlier in the same wall-clock second survives.
Rounding the other way would void the credential the request itself is about to
issue. See the note in `session_epoch_now()`.

## Choosing values

The defaults assume a product holding privileged client matter data, often on
laptops that are shared, carried, or left open. Twelve hours means a session
left overnight is gone by morning; thirty days means no session outlives a
month however heavily it is used.

Both are validated at startup. An absolute bound below the idle bound is
rejected rather than accepted, because it would silently replace it.

Raising the idle bound is the knob to reach for if sign-ins become a
complaint — it is the one people feel. Raising the absolute bound buys very
little and costs the guarantee that a stolen chain eventually dies on its own.

## Deploying a change to these values

Both are read at process start. `SESSION_IDLE_TIMEOUT_HOURS` applies to tokens
issued after the restart; chains already in Redis keep the TTL they were
written with, so a reduction takes up to one old idle window to take full
effect. `SESSION_ABSOLUTE_TIMEOUT_HOURS` is evaluated at rotation, so it
applies to every existing chain immediately.

The first deploy of this policy signs everyone out once: chains minted before
it carry no origin, cannot be bounded, and are refused rather than
grandfathered.

## Operating notes

- Redis is required in production (`app/main.py` fails startup without it).
  Refresh rotation, replay detection and `jti` revocation all depend on it.
- A refusal is logged with its reason (`session_revoked`,
  `absolute_lifetime_exceeded`, `origin_unknown`); the 401 the person sees says
  only that the session ended, since the distinction changes nothing about what
  they do next.
- This page covers the firm app. The client portal, mediation portal and
  workspace MCP tokens have their own lifetimes and are not governed by these
  settings.
