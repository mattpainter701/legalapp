# Google Search, Analytics, and Business Profile setup

**Scope:** the public LawHand marketing site at `https://getlawhand.com`.
**Cost:** none. Every step below uses a free Google product. This runbook does
not cover Google Ads, paid placement, or any paid traffic tooling.

Organic search is the whole channel here, so the work splits three ways:

| Layer | What it does | Where it lives |
|---|---|---|
| On-page and structured data | Tells Google what LawHand is, so the result reads clearly | Shipped in code. See [what the site already publishes](#what-the-site-already-publishes) |
| Search Console | Proves ownership, submits the sitemap, reports what queries actually land | Console task, one-time + weekly review |
| Business Profile | Puts LawHand in the local/knowledge panel and Maps for brand searches | Console task, one-time + occasional posts |
| Analytics (GA4) | Measures which pages and channels produce demo requests | Env var + console task |

---

## What the site already publishes

Do not duplicate any of this by hand in Tag Manager or a CMS. It is generated
at build time from `frontend/src/seo/config.js`.

- **Title and meta description** per public route, kept inside the length
  Google renders. Enforced by `frontend/src/seo/config.test.js`.
- **Canonical URL, Open Graph, and Twitter card** per route, absolute against
  `VITE_PUBLIC_SITE_URL`.
- **`robots.txt`** allowing the public pages and disallowing every sign-in
  walled route as a prefix rule.
- **`sitemap.xml`** listing only indexable public routes, with `lastmod` from
  `PUBLIC_CONTENT_LASTMOD`.
- **`X-Robots-Tag: noindex`** from nginx on every non-public path, so a
  workspace URL cannot be indexed even before JavaScript runs.
- **No-JavaScript HTML shells** for `/product`, `/product/chat`,
  `/product/mcp`, `/pricing`, `/request-demo`, `/privacy`, and `/terms`, so a
  crawler sees real content without executing the bundle.
- **Structured data** (`schema.org`): `Organization`, `WebSite`,
  `SoftwareApplication` with the capability `featureList`, `BreadcrumbList`,
  `FAQPage` on the home and pricing pages, and `SiteNavigationElement` for
  sitelink candidates.

### The rule that governs marketing copy

Structured data must never claim a capability the served page does not show.
`CORE_CAPABILITIES` in `frontend/src/marketing/capabilities.js` is the single
source for the home-page capability grid, the no-JavaScript shell in
`frontend/index.html`, and the published `featureList`. A test fails if they
drift apart. Release-gated surfaces — MCP today — must state the gate in the
copy rather than being described as generally available.

---

## 1. Google Search Console

Search Console is the only place that reports what Google actually does with
the site. Set it up before anything else.

1. Go to <https://search.google.com/search-console> and add a property.
2. Prefer the **Domain** property (`getlawhand.com`) and verify by DNS TXT
   record. It covers `www`, the apex, `http`, and `https` in one property, and
   it survives a redeploy.
   - If DNS is not available to you, add a **URL prefix** property for
     `https://getlawhand.com` and use the **HTML tag** method instead. Copy
     only the `content` value out of the tag Google shows — not the whole
     `<meta>` element — into `VITE_GOOGLE_SITE_VERIFICATION` in `.env`, then
     rebuild and redeploy the frontend before clicking Verify. A malformed
     value fails the build rather than silently failing verification.
3. Once verified, open **Sitemaps** and submit `sitemap.xml`.
4. Open **URL Inspection** for `https://getlawhand.com/` and request indexing.
   Repeat for `/product`, `/product/mcp`, and `/pricing`.
5. Confirm **Page indexing** shows no unexpected exclusions after a few days.
   Workspace routes reported as "Excluded by robots.txt" or "noindex" are
   correct and intended.

### Weekly review, 10 minutes

- **Performance → Queries**: the queries LawHand actually appears for. Queries
  with impressions but a low click rate usually mean the title or description
  does not match the intent; fix the copy in `PUBLIC_ROUTE_META` rather than
  adding pages.
- **Performance → Pages**: which pages earn impressions.
- **Page indexing**: anything newly excluded.

---

## 2. Google Analytics 4

The GA4 property and the `getlawhand.com` web data stream already exist. The
measurement id is `G-XRFT19WYPH`.

### How it is wired

Google's console hands you an inline `<script>` snippet. **Do not paste it into
`index.html`.** Two reasons:

1. The site's Content-Security-Policy is `script-src 'self'`. An inline tag is
   blocked by the browser and would collect nothing.
2. A signed-in LawHand URL can contain a matter, client, invoice, or portal
   identifier, and a `page_view` sends the full path. Pasting the raw snippet
   would send firm data to Google on every workspace navigation.

Instead, `frontend/src/analytics/googleAnalytics.js` loads the same tag from
`googletagmanager.com`, and:

- fires **only** on public marketing routes — `isMeasurablePath()` gates every
  page view on the route being indexable, so no workspace URL is ever sent;
- disables automatic `page_view` and sends one explicitly per client-side
  navigation, which a single-page app otherwise misses entirely;
- sets `allow_google_signals: false` and
  `allow_ad_personalization_signals: false`, so firms evaluating legal software
  are not swept into advertising audiences;
- forwards the existing `demo_cta_clicked`, `demo_form_started`, and
  `demo_form_submitted` conversions to GA4 alongside the first-party
  `/api/marketing/events` endpoint, which remains the system of record.

nginx allows the Google hosts in the CSP **only** on public marketing
responses; workspace responses keep `script-src 'self'`.

### Enabling it

1. Set in production `.env`:
   ```
   VITE_GA_MEASUREMENT_ID=G-XRFT19WYPH
   ```
   Leaving it empty ships no analytics request at all.
2. Rebuild and redeploy the frontend. The value is baked in at build time.
3. Verify in GA4 **Admin → Data display → Realtime**: load
   `https://getlawhand.com/` and confirm the page view arrives, then sign in and
   confirm that workspace navigation produces **no** further page views.

### Recommended GA4 console settings

- **Admin → Data collection → Google signals**: leave disabled. The tag already
  refuses it; disabling it in the property too avoids surprises.
- **Admin → Data retention**: set event data retention to 14 months.
- **Admin → Data streams → getlawhand.com → Configure tag settings → Define
  internal traffic**: add your own office IP so internal visits are filtered.
- **Admin → Key events**: mark `demo_form_submitted` as a key event. That is
  the number that matters; sessions are not.
- **Admin → Product links → Search Console**: link the verified Search Console
  property. This is what lets you see landing-page performance and organic
  queries next to conversions in one report.

---

## 3. Google Business Profile

A Business Profile is what produces the branded knowledge panel and puts
LawHand in Maps. It is free and it is the single highest-leverage step for
brand-name searches.

1. Go to <https://business.google.com> and create a profile.
2. **Business name**: `LawHand`. Use the product name exactly as the site uses
   it. Do not append keywords — "LawHand Legal Automation Software" violates
   Google's naming guidelines and risks suspension.
3. **Category**: primary `Software company`. Add `Computer software store` or
   `Business to business service` as secondary only if genuinely accurate.
4. **Service-area business**: LawHand is sold remotely, so hide the street
   address and set the service area (state, region, or nationwide). A hidden
   address still needs a real one during verification.
5. **Website**: `https://getlawhand.com`.
6. **Contact**: use the same email and phone the site publishes. Google
   reconciles a business across the web by exact name, address, and phone
   match, so an inconsistency here weakens the profile.
7. **Description**: reuse the site's own category sentence so the two agree:
   > LawHand is a legal automation platform for law firms. It combines client
   > and matter CRM, caller intake, tasks and deadlines, document preparation,
   > time tracking and invoicing, practice-area workflows, and source-linked AI
   > legal research in one tenant-isolated workspace.
8. **Verification**: usually by postcard, phone, or video call. Expect several
   days.
9. **Photos**: upload the brand assets in `frontend/public/brand/lawhand/`
   (logo, social card, homepage preview).

### After verification

Publish the profile URL back into the site so Google can link the two as one
entity. Set in `.env`:

```
VITE_ORG_SAME_AS=https://maps.google.com/?cid=YOUR_CID,https://www.linkedin.com/company/lawhand
VITE_ORG_TELEPHONE=+15555550100
```

Then rebuild. These feed `sameAs` and `contactPoint` in the `Organization`
structured data. Invalid URLs are dropped rather than published, and omitting
both simply publishes no claim — an empty or wrong profile link is worse than
none.

> **Do not** publish `LocalBusiness` or `LegalService` structured data.
> LawHand is a software company, not a law firm, and marking it up as a legal
> service would misrepresent the business to Google and to a reader.

### Maintenance

- Post an update every few weeks (a release note works). Profiles that go
  stale lose panel prominence.
- Respond to every review.
- Never ask for reviews with an incentive; Google removes them and it puts the
  profile at risk.

---

## 4. Growing organic traffic without paying for it

The technical layer above makes the site *eligible* to rank. Rankings then come
from content that answers real queries. In priority order:

1. **Keep the capability copy honest and specific.** "Legal automation platform
   with CRM, invoicing, and document preparation" matches how a firm actually
   searches. Vague brand poetry does not, and it is what Google rewrites
   snippets away from.
2. **Earn sitelinks with a stable, short menu.** `PRIMARY_NAVIGATION` is
   rendered identically in the app header, the no-JavaScript shells, and the
   `SiteNavigationElement` markup. Consistency over months is what produces
   sitelinks; the markup alone does not.
3. **Add pages that answer one question each.** The highest-value gaps today,
   given what LawHand already does:
   - "legal research MCP server" / "MCP for legal AI" — LawHand is early to a
     term with almost no competition. `/product/mcp` should own it.
   - "law firm intake software", "legal document automation", "LEDES billing
     software", "trust accounting for law firms" — each maps to a shipped
     capability and deserves its own page rather than a section.
   - Practice-area pages from `frontend/src/marketing/catalog.js`.
4. **Bump `PUBLIC_CONTENT_LASTMOD`** in `frontend/src/seo/config.js` whenever
   public copy changes, so the sitemap tells Google to re-crawl.
5. **Be patient with a new domain.** Expect eight to twelve weeks before
   Search Console impressions mean anything. Do not react to week-two data.

---

## 5. Domain authority and legitimacy, without paying for it

"Domain Rating" (Ahrefs) and "Domain Authority" (Moz) are third-party estimates
of one thing: how many independent, credible sites link to yours. Neither is a
Google ranking factor, and neither can be bought honestly — anything sold as a
"DR boost" is a link scheme that risks a manual penalty. What actually moves
both numbers, and what Google actually rewards, is the same list.

A new domain starts near zero. That is normal and not a defect.

### Free listings that produce a real, followed link

Do these in order; each is a legitimate citation for a real software company.

1. **Software review directories.** G2, Capterra, GetApp, Software Advice, and
   TrustRadius all accept free vendor profiles. These rank for
   "legal automation software" style queries in their own right, so they bring
   qualified traffic as well as a link. Claim the profile, complete every
   field, and use the same category sentence as the site.
2. **MCP server directories.** This is the genuine asymmetry: LawHand ships a
   legal research MCP server, and the MCP ecosystem is young, topical, and
   actively indexed. Submit to the community server lists (for example
   `mcp.so`, PulseMCP, Smithery, and the `awesome-mcp-servers` lists on
   GitHub). Almost no legal vendor is there yet.
3. **Company profiles.** LinkedIn company page, Crunchbase, and the Google
   Business Profile from section 3. Keep the name, description, and contact
   details byte-identical across all of them — inconsistency is what makes an
   entity look unverified.
4. **Legal-technology directories.** The ABA Legal Technology Resource Center
   listings, state bar practice-management vendor pages, and legal-tech news
   directories. State bar listings are especially strong: they are `.org`
   domains with real editorial standards, and they reach the exact audience.
5. **Attribution to upstream sources.** LawHand builds on CourtListener and the
   Free Law Project. Publishing a page that credits those sources properly is
   both correct and the kind of thing that earns a reciprocal mention.

### On-site signals that make the domain look legitimate

Trust signals are assessed by Google's quality systems and by every human who
lands on the site. The site already has HTTPS, a privacy policy, terms, and
real structured data. The remaining gaps, in priority order:

- **An About page with real people.** A software company with no named humans,
  no location, and no history reads as a shell. Name the team, say where the
  company is, say when it started.
- **A real contact route beyond `mailto:`.** A `mailto:` link to a domain that
  does not match the site (`cybersafeadvisor.com` on `getlawhand.com`) actively
  undercuts legitimacy. Move public contact to a `@getlawhand.com` address.
- **A security and data-handling page.** The buyer is a law firm with
  confidentiality obligations. Tenant isolation, encryption at rest, subprocessor
  list, and incident contact are what a firm's IT reviewer looks for, and it is
  a page competitors link to.
- **A changelog or release notes page.** Public, dated evidence that the
  product is actively maintained.
- **Original writing on what LawHand actually knows.** One genuinely useful
  technical post — how a legal research MCP server works, how citation review
  states are computed — will earn more links than fifty generic
  "top 10 legal software" posts, because the people who cover this topic are
  looking for exactly that and there is almost nothing to cite yet.

### What to refuse

- Paid link placements, "guest post packages", PBNs, and directory-submission
  services. These are link schemes under Google's spam policies.
- Reciprocal-link swaps at scale.
- AI-generated content published at volume to "build topical authority".
- Incentivized reviews on G2, Capterra, or the Business Profile.

### Realistic expectation

With the listings above, a new domain reaches a Domain Rating in the teens to
low twenties within a few months, which is enough to rank for brand and
long-tail terms. Competing for "legal practice management software" against
established vendors takes years and is not the goal; owning
"legal research MCP" and the specific capability queries is.

## Checklist

- [ ] Search Console property verified (DNS preferred)
- [ ] `sitemap.xml` submitted, indexing requested for the top four pages
- [ ] `VITE_GA_MEASUREMENT_ID=G-XRFT19WYPH` set in production `.env`, frontend rebuilt
- [ ] Realtime confirms marketing page views and **no** workspace page views
- [ ] `demo_form_submitted` marked as a GA4 key event
- [ ] Search Console linked to the GA4 property
- [ ] Business Profile created, categorized as a software company, verified
- [ ] `VITE_ORG_SAME_AS` set to the verified profile URL, frontend rebuilt
- [ ] Rich Results Test passes for `/` and `/pricing`:
      <https://search.google.com/test/rich-results>
- [ ] Free vendor profiles claimed: G2, Capterra, GetApp, TrustRadius
- [ ] Submitted to MCP server directories
- [ ] LinkedIn company page and Crunchbase profile, matching the site's copy
- [ ] Public contact moved to an `@getlawhand.com` address
- [ ] About page naming real people and a real location
