import {
  CAPABILITY_STATES,
  CORE_CAPABILITIES,
} from '../marketing/capabilities.js'
import {
  PRIMARY_NAVIGATION,
  PUBLIC_ROUTE_META,
  buildStructuredData,
  getRouteMeta,
} from './config.js'

const LEGAL_SHELLS = Object.freeze({
  '/privacy': {
    heading: 'Privacy Policy',
    lead:
      'This Privacy Policy explains how LawHand handles information when firms, legal professionals, and authorized users access the service.',
    otherPath: '/terms',
    otherLabel: 'Terms of Use',
    sections: [
      { id: 'scope', heading: 'Scope and roles', body: 'This policy covers the LawHand website and service. A subscribing organization generally controls the matter, client, and workspace information its users submit; users should also review their organization\u2019s own privacy notices and instructions.' },
      { id: 'information', heading: 'Information we handle', body: 'We may handle account and contact details, authentication and device information, service usage and support communications, billing records, and documents or other workspace content submitted by authorized users or connected services.' },
      { id: 'use', heading: 'How information is used', body: 'Information is used to provide, secure, maintain, troubleshoot, and improve the service; administer accounts and subscriptions; respond to requests; meet legal obligations; and prevent misuse. Workspace content is used to perform the features requested by authorized users.' },
      { id: 'ai-integrations', heading: 'AI features and connected services', body: 'When an organization enables an AI provider or third-party integration, relevant information may be sent to that provider to complete the requested task. Provider handling, retention, and training terms depend on the provider, agreement, and tenant configuration selected by the organization.' },
      { id: 'sharing', heading: 'Sharing and disclosures', body: 'Information may be disclosed to service providers supporting hosting, security, communications, payments, and enabled integrations; to the subscribing organization and its authorized administrators; or when required for legal compliance, safety, or a business transaction. Provider-specific processing is also governed by the provider’s terms and the configuration selected by the organization.' },
      { id: 'retention-security', heading: 'Retention and security', body: 'Retention depends on the type of information, tenant settings, contractual requirements, and legal obligations. LawHand uses administrative, technical, and organizational safeguards, including tenant isolation, but no system can guarantee absolute security.' },
      { id: 'choices', heading: 'Choices and privacy requests', body: 'Users may update certain account information through the service. Requests concerning workspace content should usually be directed to the subscribing organization. Other access, correction, deletion, or objection rights may apply based on location and can be submitted using the contact information below.' },
      { id: 'changes-contact', heading: 'Changes and contact', body: 'We may update this policy as the service or applicable requirements change and will post the revised date here. Questions or privacy requests may be sent to support@getlawhand.com.' },
    ],
  },
  '/terms': {
    heading: 'Terms of Use',
    lead:
      'These Terms of Use govern access to LawHand unless a separate written agreement with the subscribing organization controls.',
    otherPath: '/privacy',
    otherLabel: 'Privacy Policy',
    sections: [
      { id: 'agreement', heading: 'Agreement and eligibility', body: 'By accessing the service, you agree to these terms and confirm that you are authorized by the subscribing organization. If that organization has a subscription agreement with us, that agreement controls in the event of a conflict.' },
      { id: 'service', heading: 'The service and professional responsibility', body: 'LawHand provides law-practice workflow and AI-assisted tools. It is not a law firm and does not provide legal advice. Users remain responsible for professional judgment, source verification, court and client obligations, filings, deadlines, and the accuracy and suitability of all work product.' },
      { id: 'accounts', heading: 'Accounts and administration', body: 'Users must provide accurate account information, protect credentials, and promptly report suspected unauthorized access. Organization administrators control user access, connected services, tenant configuration, and available retention settings.' },
      { id: 'acceptable-use', heading: 'Acceptable use', body: 'Users may not violate law or third-party rights; access another tenant without authorization; upload malicious code; disrupt or probe the service; bypass access controls or usage limits; or use the service to create or distribute unlawful, deceptive, or harmful material.' },
      { id: 'content-integrations', heading: 'Content, AI features, and integrations', body: 'The subscribing organization retains its rights in submitted content and grants the permissions needed to operate the service. Outputs may be incomplete or incorrect and require review. Third-party services and AI providers are governed by their own terms and the organization\u2019s configuration.' },
      { id: 'availability', heading: 'Availability and changes', body: 'Features may evolve, and access may be limited for maintenance, security, legal compliance, nonpayment, or misuse. Subscription fees, support commitments, service levels, and termination rights are governed by the applicable subscription agreement.' },
      { id: 'disclaimers', heading: 'Disclaimers and liability', body: 'Except for express commitments in an applicable organization agreement, the public website and service are provided on an “as available” basis to the extent permitted by law. AI-assisted output, third-party content, citations, integrations, and connected services are not guaranteed to be error-free, complete, current, or continuously available.' },
      { id: 'changes-contact', heading: 'Changes and contact', body: 'We may update these terms and will post the revised date here. Changes to an organization’s controlling subscription or data-processing terms are handled under those agreements. Questions may be sent to support@getlawhand.com.' },
    ],
  },
})

const MARKETING_SHELLS = Object.freeze({
  '/product': {
    heading: 'The legal automation platform for law firms.',
    lead: 'LawHand holds client and matter CRM, caller intake, tasks and deadlines, document preparation, time and invoicing, and source-linked legal research in a single tenant-isolated workspace.',
    sections: [
      { heading: 'Core capabilities', body: CORE_CAPABILITIES.map((capability) => `${capability.name} (${CAPABILITY_STATES[capability.availability]})`).join(' \u00b7 ') },
      { heading: 'The core workspace', body: 'Intake and tasks, matters and contacts, calendar and deadlines, documents and automation, time, billing, trust accounting, reporting, client portal, and signature routing.' },
      { heading: 'Practice-area library', body: 'Skill libraries add the document patterns, checks, and terminology of a practice area to the shared matter record. Trust and estate, family and domestic relations, and mediation add dedicated workspaces with their own records and roles.' },
      { heading: 'Connected sources', body: 'Supported Microsoft 365, Google Workspace, Microsoft Teams, Zoom Phone, QuickBooks Online, and enterprise file-share connections are enabled by a firm administrator and can be disconnected at any time.' },
      { heading: 'Controls', body: 'Firm workspaces are tenant-isolated, module roles decide what each participant can see or approve, and AI-assisted work carries source links for attorney review before reliance.' },
    ],
  },
  '/product/chat': {
    heading: 'Ask with the whole matter in hand.',
    lead: 'LawHand gives legal teams a matter-aware AI workspace for research, review, summaries, and drafting with authorized sources close at hand.',
    sections: [
      { heading: 'Starts with the matter', body: 'Open chat from a matter and keep the conversation tied to the work instead of rebuilding context in a blank chatbot.' },
      { heading: 'Shows its source trail', body: 'When connected sources are enabled, answers can include citations, confidence cues, and links for attorney verification.' },
      { heading: 'Moves into work product', body: 'Use LawHand to summarize, compare, review, and prepare a first draft while keeping professional judgment in the loop.' },
    ],
  },
  '/product/mcp': {
    heading: 'Bring approved public legal authority into the tools you already use.',
    lead: 'LawHand Research MCP is a controlled pilot connecting approved ChatGPT, Claude, and API clients through OAuth or a scoped API token for research-only retrieval, PAYG metering, and administrative visibility.',
    sections: [
      { heading: 'Research-only boundary', body: 'Search approved public legal authority without exposing workspace matters, documents, tasks, or client files.' },
      { heading: 'OAuth or API token', body: 'Hosted ChatGPT and Claude clients use OAuth 2.1. Header-capable API clients use a scoped LawHand Research token.' },
      { heading: 'Controlled pilot', body: 'Approved pilot customers can use OAuth or managed Research API keys at a pilot price of $0.45 per successful tool call. Coverage depends on the configured public-authority corpus.' },
    ],
  },
  '/request-demo': {
    heading: 'Book a LawHand demo.',
    lead: 'See the legal automation platform against your firm\u2019s own intake, matters, documents, billing, and review controls rather than a generic script.',
    sections: [
      { heading: 'What the demo covers', body: 'A walkthrough of client and matter CRM, caller intake, document preparation, time and invoicing, and source-linked legal research, using workflows that match how your firm already works.' },
      { heading: 'Bring your questions', body: 'Tenant isolation, integrations with Microsoft 365, Google Workspace, Zoom Phone, and QuickBooks Online, attorney review controls, and rollout scope are all fair game.' },
      { heading: 'No obligation', body: 'Pricing, onboarding scope, and any specialized service commitments are confirmed in writing before a firm commits.' },
    ],
  },
  '/pricing': {
    heading: 'One clear platform price. Controlled expansion.',
    lead: 'LawHand is $89 per user per month, billed annually. Research MCP is a controlled pilot for approved customers at a pilot price of $0.45 per successful tool call.',
    sections: [
      { heading: 'LawHand platform', body: 'The core seat includes the firm workspace, matter-aware AI chat, firm operations, source-aware workflows, and role-aware access within the licensed scope.' },
      { heading: 'LawHand Research MCP', body: 'Approved pilot customers pay $0.45 per successful tool call, with administrator-managed keys, budgets, expiration, and usage visibility.' },
      { heading: 'Call Intake', body: 'Firms may begin with a focused caller-intake and task workflow, with optional verified Zoom Phone integration.' },
    ],
  },
})

const PUBLIC_SHELLS = Object.freeze({ ...LEGAL_SHELLS, ...MARKETING_SHELLS })

const LAST_UPDATED = 'July 27, 2026'

const FALLBACK_CONTACT_URL = 'mailto:support@getlawhand.com'

/** Render the address a mailto: contact URL points at, for link text. */
function contactLabel(contactUrl) {
  return contactUrl.startsWith('mailto:')
    ? contactUrl.slice('mailto:'.length).split('?')[0]
    : 'our team'
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function replaceMeta(html, attribute, key, content) {
  const escaped = escapeHtml(content)
  const pattern = new RegExp(
    `<meta\\s+([^>]*${attribute}=["']${key}["'][^>]*)>`,
    'i',
  )
  return html.replace(pattern, (tag) => {
    if (/content=["'][^"']*["']/i.test(tag)) {
      return tag.replace(/content=["'][^"']*["']/i, `content="${escaped}"`)
    }
    return tag.replace(/\s*\/?\s*>$/, ` content="${escaped}" />`)
  })
}

function replaceRootContents(html, contents) {
  const marker = '<div id="root">'
  const rootStart = html.indexOf(marker)
  const bodyEnd = html.indexOf('</body>', rootStart)
  const rootEnd = html.lastIndexOf('</div>', bodyEnd)
  if (rootStart < 0 || bodyEnd < 0 || rootEnd < rootStart) {
    throw new Error('Built frontend HTML does not contain the expected React root shell.')
  }
  return `${html.slice(0, rootStart + marker.length)}\n${contents}\n    ${html.slice(rootEnd)}`
}

function legalShellMarkup(pathname, contactUrl) {
  const route = LEGAL_SHELLS[pathname]
  const contents = route.sections
    .map((section) => `            <li><a href="#${escapeHtml(section.id)}">${escapeHtml(section.heading)}</a></li>`)
    .join('\n')
  const sections = route.sections
    .map((section) => `          <section id="${escapeHtml(section.id)}">
            <h2>${escapeHtml(section.heading)}</h2>
            <p>${escapeHtml(section.body)}</p>
          </section>`)
    .join('\n')
  return `      <main class="server-legal">
        <article class="server-legal__article">
          <header class="server-legal__header">
            <a class="server-legal__brand" href="/">LawHand</a>
            <h1>${escapeHtml(route.heading)}</h1>
            <p class="server-legal__lead">${escapeHtml(route.lead)}</p>
            <p class="server-legal__updated">Last updated: <time datetime="2026-07-27">${LAST_UPDATED}</time></p>
          </header>
          <nav class="server-legal__contents" aria-label="On this page">
            <h2>On this page</h2>
            <ol>
${contents}
            </ol>
          </nav>
${sections}
          <footer class="server-legal__footer">
            <p>The controlling subscription agreement and, where applicable, data-processing agreement are available from your organization. Contact your firm administrator for workspace-specific terms.</p>
            <p>Read the <a href="${route.otherPath}">${escapeHtml(route.otherLabel)}</a> or contact <a href="${escapeHtml(contactUrl)}">${escapeHtml(contactLabel(contactUrl))}</a>.</p>
          </footer>
        </article>
      </main>`
}

/**
 * The same short menu the app header renders. Repeating one consistent set of
 * internal links across every public page is what actually makes a page a
 * sitelink candidate; the structured data only describes the intent.
 */
function navigationLinks(currentPath) {
  return PRIMARY_NAVIGATION
    .filter(({ path }) => path !== currentPath)
    .map(({ path, label }) => `              <li><a href="${escapeHtml(PUBLIC_ROUTE_META[path]?.canonicalPath || path)}">${escapeHtml(label)}</a></li>`)
    .join('\n')
}

function marketingShellMarkup(pathname, contactUrl) {
  const route = MARKETING_SHELLS[pathname]
  const sections = route.sections
    .map((section) => `          <section>
            <h2>${escapeHtml(section.heading)}</h2>
            <p>${escapeHtml(section.body)}</p>
          </section>`)
    .join('\n')
  return `      <main class="server-legal">
        <article class="server-legal__article">
          <header class="server-legal__header">
            <a class="server-legal__brand" href="/">LawHand</a>
            <h1>${escapeHtml(route.heading)}</h1>
            <p class="server-legal__lead">${escapeHtml(route.lead)}</p>
          </header>
          <nav class="server-legal__contents" aria-label="LawHand product pages">
            <h2>Explore LawHand</h2>
            <ol>
${navigationLinks(pathname)}
            </ol>
          </nav>
${sections}
          <footer class="server-legal__footer">
            <p><a href="${escapeHtml(contactUrl)}">Book a LawHand demo</a> or <a href="/login">sign in</a>.</p>
          </footer>
        </article>
      </main>`
}

/**
 * Replace the home-page JSON-LD baked into index.html with this route's own.
 * Leaving the home graph in place would tell a crawler that /pricing is the
 * home page; removing it outright would leave the crawler-visible copy of the
 * page with no structured data at all.
 */
function replaceStructuredData(html, siteOrigin, pathname, organizationProfile) {
  const pattern = /<script[^>]*data-seo-structured-data[^>]*>[\s\S]*?<\/script>\s*/gi
  const data = buildStructuredData(siteOrigin, pathname, organizationProfile)
  if (!data) return html.replace(pattern, '')

  const payload = JSON.stringify(data).replace(/</g, '\\u003c')
  const script = `<script type="application/ld+json" data-seo-structured-data>${payload}</script>`
  if (pattern.test(html)) {
    pattern.lastIndex = 0
    return html.replace(pattern, `${script}\n    `)
  }
  return html.replace('</head>', `  ${script}\n  </head>`)
}

/** Derive a crawl-correct, no-JavaScript shell from Vite's final SPA index. */
export function buildPublicRouteHtml(
  baseHtml,
  pathname,
  siteOrigin = '',
  contactUrl = FALLBACK_CONTACT_URL,
  organizationProfile = {},
) {
  if (!Object.hasOwn(PUBLIC_SHELLS, pathname)) {
    throw new Error(`No public server shell is defined for ${pathname}`)
  }
  const meta = getRouteMeta(pathname)
  const canonical = siteOrigin ? `${siteOrigin}${pathname}` : pathname
  let html = baseHtml
    .replace(/<title>[\s\S]*?<\/title>/i, `<title>${escapeHtml(meta.title)}</title>`)
    .replace(
      /<link\s+rel=["']canonical["'][^>]*>/i,
      `<link rel="canonical" href="${escapeHtml(canonical)}" />`,
    )

  html = replaceStructuredData(html, siteOrigin, pathname, organizationProfile)

  html = replaceMeta(html, 'name', 'description', meta.description)
  html = replaceMeta(html, 'name', 'robots', 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1')
  html = replaceMeta(html, 'name', 'googlebot', 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1')
  html = replaceMeta(html, 'property', 'og:title', meta.title)
  html = replaceMeta(html, 'property', 'og:description', meta.description)
  html = replaceMeta(html, 'property', 'og:url', canonical)
  html = replaceMeta(html, 'name', 'twitter:title', meta.title)
  html = replaceMeta(html, 'name', 'twitter:description', meta.description)

  return replaceRootContents(
    html,
    Object.hasOwn(LEGAL_SHELLS, pathname)
      ? legalShellMarkup(pathname, contactUrl)
      : marketingShellMarkup(pathname, contactUrl),
  )
}

export const PUBLIC_SERVER_SHELL_PATHS = Object.freeze(Object.keys(PUBLIC_SHELLS))
