import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  BriefcaseBusiness,
  CheckSquare2,
  Clock3,
  Compass,
  LayoutDashboard,
  Network,
  PlugZap,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from 'lucide-react'
import { slugifyHeading } from '../platformDocs'

const ICONS = {
  briefcase: BriefcaseBusiness,
  chart: BarChart3,
  checklist: CheckSquare2,
  clock: Clock3,
  compass: Compass,
  layout: LayoutDashboard,
  network: Network,
  plug: PlugZap,
  settings: Settings2,
  shield: ShieldCheck,
  sparkles: Sparkles,
  users: UsersRound,
}

function textFromChildren(children) {
  if (Array.isArray(children)) return children.map(textFromChildren).join(' ')
  return typeof children === 'string' || typeof children === 'number' ? String(children) : ''
}

function MarkdownLink({ href = '', children, node: _node, ...props }) {
  if (href.startsWith('/') && !href.startsWith('//')) {
    return <Link to={href} {...props}>{children}</Link>
  }
  if (href.startsWith('#')) return <a href={href} {...props}>{children}</a>
  return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
}

function Heading({ level: Level, children }) {
  const id = slugifyHeading(textFromChildren(children))
  return <Level id={id} className="scroll-mt-24">{children}</Level>
}

const MARKDOWN_COMPONENTS = {
  h1: () => null,
  h2: ({ children }) => <Heading level="h2">{children}</Heading>,
  h3: ({ children }) => <Heading level="h3">{children}</Heading>,
  a: MarkdownLink,
  table: ({ children }) => (
    <div className="my-6 overflow-x-auto rounded-xl border border-brand-line">
      <table>{children}</table>
    </div>
  ),
  img: ({ alt, node: _node, ...props }) => (
    <figure className="my-8 overflow-hidden rounded-2xl border border-brand-line bg-brand-surface-2 p-2 shadow-sm">
      <img alt={alt || ''} className="w-full rounded-xl" loading="lazy" {...props} />
      {alt && <figcaption className="px-2 pb-1 pt-3 text-center text-xs text-brand-muted">{alt}</figcaption>}
    </figure>
  ),
}

function ChapterIcon({ name, className = 'h-4 w-4' }) {
  const Icon = ICONS[name] || BookOpen
  return <Icon aria-hidden="true" className={className} />
}

export default function GuideViewer({
  documents,
  audience = 'user',
  activeSlug,
  onSelect,
  embedded = false,
}) {
  const [internalSlug, setInternalSlug] = useState(documents[0]?.slug)
  const [query, setQuery] = useState('')
  const selectedSlug = activeSlug || internalSlug
  const selectedIndex = documents.findIndex((document) => document.slug === selectedSlug)
  const selected = documents[selectedIndex] || documents[0]

  useEffect(() => {
    if (activeSlug && selectedIndex === -1 && documents[0]) onSelect?.(documents[0].slug, { replace: true })
  }, [activeSlug, documents, onSelect, selectedIndex])

  const visibleDocuments = useMemo(() => {
    const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
    if (!terms.length) return documents
    return documents.filter((document) => terms.every((term) => document.searchText.includes(term)))
  }, [documents, query])

  if (!selected) return null

  const chooseChapter = (slug, options) => {
    setInternalSlug(slug)
    onSelect?.(slug, options)
  }

  const previous = documents[selectedIndex - 1]
  const next = documents[selectedIndex + 1]
  const isAdmin = audience === 'admin'

  return (
    <section className={embedded ? '' : 'mx-auto max-w-7xl px-4 py-8 md:px-8 md:py-12'} aria-label={isAdmin ? 'Administrative guide' : 'User guide'}>
      <div className="relative mb-7 overflow-hidden rounded-3xl border border-brand-line bg-brand-ink px-6 py-7 text-white shadow-sm md:px-9 md:py-9">
        <div className="pointer-events-none absolute -right-20 -top-28 h-72 w-72 rounded-full bg-brand-accent/35 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 left-1/3 h-64 w-64 rounded-full bg-brand-green/30 blur-3xl" />
        <div className="relative max-w-3xl">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-white/80">
            {isAdmin ? <ShieldCheck className="h-3.5 w-3.5" /> : <BookOpen className="h-3.5 w-3.5" />}
            {isAdmin ? 'Administrator handbook' : 'Workspace handbook'}
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-white md:text-4xl">
            {isAdmin ? 'Run your LawHand tenant with confidence.' : 'Get useful work done, one clear step at a time.'}
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-white/70 md:text-base">
            {isAdmin
              ? 'Configuration guidance, governance checks, and direct paths to every administrative control.'
              : 'Practical guidance for matters, documents, deadlines, billing, and safe AI-assisted work.'}
          </p>
          <div className="mt-5 flex flex-wrap gap-2 text-xs text-white/70">
            <span className="rounded-full border border-white/10 bg-black/10 px-3 py-1.5">{documents.length} chapters</span>
            <span className="rounded-full border border-white/10 bg-black/10 px-3 py-1.5">Searchable</span>
            <span className="rounded-full border border-white/10 bg-black/10 px-3 py-1.5">Linked to settings</span>
          </div>
        </div>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="rounded-2xl border border-brand-line bg-brand-surface p-3 shadow-sm lg:sticky lg:top-5" aria-label="Guide chapters">
          <label className="relative block">
            <span className="sr-only">Search this guide</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-brand-muted" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search this guide"
              className="w-full rounded-xl border border-brand-line bg-brand-surface-2 py-2.5 pl-9 pr-3 text-sm placeholder:text-brand-muted focus:border-brand-accent"
            />
          </label>

          <div className="mt-3 max-h-64 space-y-1 overflow-y-auto pr-1 lg:max-h-none lg:overflow-visible lg:pr-0">
            {visibleDocuments.map((document) => {
              const active = document.slug === selected.slug
              return (
                <button
                  type="button"
                  key={document.slug}
                  onClick={() => chooseChapter(document.slug)}
                  aria-current={active ? 'page' : undefined}
                  className={`group flex w-full items-start gap-3 rounded-xl px-3 py-3 text-left ${active ? 'bg-brand-bg-soft text-brand-ink' : 'text-brand-ink-2 hover:bg-brand-surface-2'}`}
                >
                  <span className={`mt-0.5 rounded-lg p-1.5 ${active ? 'bg-brand-accent text-white' : 'bg-brand-bg-soft text-brand-muted group-hover:text-brand-ink'}`}>
                    <ChapterIcon name={document.icon} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold leading-5">{document.title}</span>
                    <span className="mt-0.5 block text-[11px] text-brand-muted">{document.read_time}</span>
                  </span>
                </button>
              )
            })}
            {visibleDocuments.length === 0 && (
              <div className="rounded-xl bg-brand-surface-2 px-4 py-5 text-center">
                <p className="text-sm font-semibold text-brand-ink">No matching chapter</p>
                <button type="button" onClick={() => setQuery('')} className="mt-2 text-xs font-semibold text-brand-accent hover:underline">Clear search</button>
              </div>
            )}
          </div>
        </aside>

        <article className="min-w-0 overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
          <header className="border-b border-brand-line bg-brand-surface-2 px-6 py-7 md:px-10 md:py-9">
            <div className="flex items-start gap-4">
              <span className="rounded-2xl bg-brand-accent p-3 text-white shadow-sm">
                <ChapterIcon name={selected.icon} className="h-5 w-5" />
              </span>
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-muted">Chapter {selectedIndex + 1} · {selected.read_time}</p>
                <h2 className="mt-1 text-2xl font-bold text-brand-ink md:text-3xl">{selected.title}</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-brand-ink-2 md:text-base">{selected.description}</p>
              </div>
            </div>
            {selected.headings.length > 0 && (
              <nav className="mt-6 flex flex-wrap gap-2" aria-label="On this page">
                {selected.headings.map((heading) => (
                  <a key={heading.id} href={`#${heading.id}`} className="rounded-full border border-brand-line bg-white px-3 py-1.5 text-xs font-medium text-brand-ink-2 hover:border-brand-accent hover:text-brand-accent">
                    {heading.title}
                  </a>
                ))}
              </nav>
            )}
          </header>

          <div className="guide-prose px-6 py-8 md:px-10 md:py-10">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
              {selected.content}
            </ReactMarkdown>
          </div>

          <footer className="grid gap-3 border-t border-brand-line bg-brand-surface-2 px-6 py-5 sm:grid-cols-2 md:px-10">
            {previous ? (
              <button type="button" onClick={() => chooseChapter(previous.slug)} className="group flex items-center gap-3 rounded-xl border border-brand-line bg-white px-4 py-3 text-left hover:border-brand-accent">
                <ArrowLeft className="h-4 w-4 text-brand-muted group-hover:text-brand-accent" />
                <span><span className="block text-[10px] font-semibold uppercase tracking-wider text-brand-muted">Previous</span><span className="text-sm font-semibold text-brand-ink">{previous.title}</span></span>
              </button>
            ) : <span />}
            {next && (
              <button type="button" onClick={() => chooseChapter(next.slug)} className="group flex items-center justify-end gap-3 rounded-xl border border-brand-line bg-white px-4 py-3 text-right hover:border-brand-accent">
                <span><span className="block text-[10px] font-semibold uppercase tracking-wider text-brand-muted">Next</span><span className="text-sm font-semibold text-brand-ink">{next.title}</span></span>
                <ArrowRight className="h-4 w-4 text-brand-muted group-hover:text-brand-accent" />
              </button>
            )}
          </footer>
        </article>
      </div>
    </section>
  )
}
