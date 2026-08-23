# `word-addin/` — removed

The repository carried two Word add-ins. This records why one is gone, so it is
not reconstructed from an old checkout.

## What was removed

`word-addin/` — a vanilla-JS prototype (`taskpane.js`, `manifest.xml`, assets).

## Why

It could not ship, and it contradicted the authentication posture the main
application deliberately moved to:

- `API_BASE` was hardcoded to `http://localhost:8000/api` — plain HTTP, loopback.
  It could not reach a deployed backend.
- The manifest pointed at `https://localhost:3001`.
- It stored a long-lived bearer token in `localStorage`, where any script on the
  page can read it. The main app's `AuthProvider` documents the opposite rule:
  the access token is never written to browser-accessible storage, so an XSS
  payload cannot exfiltrate a live session.
- It received that token through a **URL query string**
  (`href.match(/[?&]token=([^&]+)/)`), which leaks into browser history,
  `Referer` headers, and server access logs.

`frontend/src/api.js` still clears legacy `localStorage` `token` and `user` keys
on sign-out — the footprint of the migration this add-in never followed.

## What to use instead

`office-addin/` is the supported implementation: TypeScript, Vite, MSAL with
Nested App Authentication, `sessionStorage`, generated manifests, a Dockerfile,
and tests. It exchanges an Entra token at `POST /auth/office/exchange` for the
same httpOnly cookie session the web app uses, and is gated behind
`OFFICE_ASSISTANT_ENABLED` plus an explicit pilot-tenant allowlist that denies
everyone when empty.

Removed in the platform hardening change; see
`docs/reviews/PLATFORM_HARDENING_REVIEW.md` §4.1.
