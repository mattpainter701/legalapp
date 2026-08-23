# LawHand platform guides

This directory is the source of truth for the authenticated, in-product guides.

- `user-guide/` documents day-to-day operational workflows for every authenticated user.
- `administrative-guide/` documents tenant configuration, access, integrations, billing, and governance for administrators.

Each chapter is Markdown with a small front matter block. `slug`, `title`, `description`, `order`, `read_time`, and `icon` are required. The frontend discovers chapters automatically, so adding a valid file is enough to place it in the guide navigation.

`coverage.json` maps every authenticated product route and Administration tab to its owning chapter. The documentation check compares that map with `App.jsx` and `AdminPage.jsx`, verifies the target chapter and deep link, and fails when a product surface is added or removed without updating the guide.

## Linking into LawHand

Use root-relative links to take a reader to the corresponding screen:

```md
[Open your matters](/matters)
[Manage users](/admin?tab=users)
```

The in-product renderer turns these into client-side links. User-guide chapters must not link to administrative routes. The documentation check enforces that boundary and validates supported application routes and admin tabs.

## Screenshots

Place future screenshots in `frontend/public/guide-assets/` and reference them with an absolute path such as:

```md
![The matter overview with the Documents tab highlighted](/guide-assets/matter-overview.webp)
```

Use redacted demo data, meaningful alternative text, and a narrow crop that emphasizes one task. Do not put credentials, customer data, security procedures, or internal incident runbooks in these client-delivered guides.

Run `npm run docs:check` from `frontend/` before committing guide changes.
