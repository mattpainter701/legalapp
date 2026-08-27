import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import LawHandLogo from './LawHandLogo'
import { PRIMARY_NAVIGATION } from '../seo/config'

// Derived from the one list that also drives the SiteNavigationElement
// structured data and the no-JavaScript shells, so the internal links Google
// weighs for sitelinks stay identical everywhere they are rendered.
const NAV_ITEMS = [
  ...PRIMARY_NAVIGATION.map(({ path, shortLabel }) => ({ label: shortLabel, to: path })),
  // Rendered as a router link so a visitor arriving from another marketing
  // page still lands on the home section; HomePage honours the hash on mount.
  { label: 'Security', to: '/#security', section: 'security' },
]

export function MarketingHeader({ onSectionClick }) {

  return (
    <header className="sticky top-0 z-40 border-b border-brand-line bg-brand-bg/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link to="/" aria-label="LawHand home" className="rounded-lg">
          <LawHandLogo compact />
        </Link>
        <nav aria-label="Marketing" className="hidden items-center gap-5 text-[13.5px] font-medium text-brand-ink-2 lg:flex">
          {NAV_ITEMS.map((item) => (
            // On the home page itself an in-page anchor scrolls without a
            // route change; everywhere else the same item routes home first.
            onSectionClick && item.section ? (
              <a
                key={item.label}
                href={`#${item.section}`}
                onClick={onSectionClick(item.section)}
                className="inline-flex min-h-11 items-center transition-colors hover:text-brand-ink"
              >
                {item.label}
              </a>
            ) : (
              <Link key={item.label} to={item.to} className="inline-flex min-h-11 items-center transition-colors hover:text-brand-ink">
                {item.label}
              </Link>
            )
          ))}
        </nav>
        <div className="flex items-center gap-2 sm:gap-3">
          <Link to="/login" className="inline-flex min-h-11 items-center px-2 text-[14px] font-semibold text-brand-ink transition-colors hover:text-brand-accent-2">
            Sign in
          </Link>
          <Link to="/request-demo" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-ink px-3.5 text-[13px] font-semibold text-white shadow-sm transition-all hover:-translate-y-px hover:bg-brand-ink-2 sm:px-4 sm:text-[14px]">
            Book demo <ArrowRight size={15} className="hidden sm:block" aria-hidden="true" />
          </Link>
        </div>
      </div>
    </header>
  )
}

export function MarketingFooter() {
  return (
    <footer className="border-t border-brand-line">
      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 sm:grid-cols-[1fr_auto] sm:items-end">
        <div>
          <LawHandLogo compact />
          <p className="mt-3 font-sans text-[13px] text-brand-muted">The whole matter, in hand.</p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap items-center gap-x-5 gap-y-2 font-sans text-[12.5px] text-brand-muted sm:justify-end">
          <Link to="/product" className="inline-flex min-h-11 items-center hover:text-brand-ink">Platform</Link>
          <Link to="/product/chat" className="inline-flex min-h-11 items-center hover:text-brand-ink">AI Chat</Link>
          <Link to="/product/mcp" className="inline-flex min-h-11 items-center hover:text-brand-ink">Legal Research MCP</Link>
          <Link to="/pricing" className="inline-flex min-h-11 items-center hover:text-brand-ink">Pricing</Link>
          <Link to="/privacy" className="inline-flex min-h-11 items-center hover:text-brand-ink">Privacy</Link>
          <Link to="/terms" className="inline-flex min-h-11 items-center hover:text-brand-ink">Terms</Link>
          <span>© {new Date().getFullYear()} LawHand</span>
        </nav>
      </div>
    </footer>
  )
}

export default function MarketingPageLayout({ children }) {
  return (
    <div className="min-h-screen bg-brand-bg text-brand-ink">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-brand-ink focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
      >
        Skip to main content
      </a>
      <MarketingHeader />
      <main id="main-content" tabIndex="-1">{children}</main>
      <MarketingFooter />
    </div>
  )
}
