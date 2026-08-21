import { useEffect, useState } from 'react'
import { CalendarDays, GitCommitHorizontal, Megaphone } from 'lucide-react'
import { getAppVersion } from '../api'

function formatReleaseDate(value) {
  if (!value) return 'Date unavailable'
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function formatBuildTime(value) {
  if (!value) return 'Not reported'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

export default function ReleaseInfoPanel({ className = '' }) {
  const [releaseInfo, setReleaseInfo] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let mounted = true
    getAppVersion()
      .then((data) => {
        if (mounted) setReleaseInfo(data)
      })
      .catch(() => {
        if (mounted) setError(true)
      })
    return () => {
      mounted = false
    }
  }, [])

  return (
    <section
      id="release-notes"
      aria-labelledby="release-notes-title"
      className={`overflow-hidden rounded-xl border border-brand-line bg-brand-surface shadow-sm ${className}`}
    >
      <div className="border-b border-brand-line bg-brand-bg-soft/50 px-5 py-5 sm:px-8 sm:py-6">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-ink/10 text-brand-ink">
            <Megaphone size={18} aria-hidden="true" />
          </span>
          <div>
            <h2 id="release-notes-title" className="font-serif text-xl font-bold text-brand-ink">
              Version &amp; release notes
            </h2>
            <p className="mt-1 text-sm text-brand-ink-2">
              Confirm the deployed build and review customer-facing product updates.
            </p>
          </div>
        </div>
      </div>

      {error ? (
        <p role="status" className="px-5 py-6 text-sm text-brand-muted sm:px-8">
          Version information is temporarily unavailable.
        </p>
      ) : !releaseInfo ? (
        <p role="status" className="px-5 py-6 text-sm text-brand-muted sm:px-8">
          Loading version information…
        </p>
      ) : (
        <div>
          <dl className="grid gap-px border-b border-brand-line bg-brand-line sm:grid-cols-3">
            <div className="bg-brand-surface px-5 py-4 sm:px-6">
              <dt className="text-[11px] font-semibold uppercase tracking-wider text-brand-muted">Deployed version</dt>
              <dd className="mt-1 font-mono text-sm font-semibold text-brand-ink">{releaseInfo.version || 'dev'}</dd>
            </div>
            <div className="bg-brand-surface px-5 py-4 sm:px-6">
              <dt className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-brand-muted">
                <GitCommitHorizontal size={13} aria-hidden="true" /> Commit
              </dt>
              <dd className="mt-1 truncate font-mono text-sm font-semibold text-brand-ink" title={releaseInfo.commit || undefined}>
                {releaseInfo.short_commit || releaseInfo.version || 'dev'}
              </dd>
            </div>
            <div className="bg-brand-surface px-5 py-4 sm:px-6">
              <dt className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-brand-muted">
                <CalendarDays size={13} aria-hidden="true" /> Built
              </dt>
              <dd className="mt-1 text-sm font-semibold text-brand-ink">{formatBuildTime(releaseInfo.build_time)}</dd>
            </div>
          </dl>

          <div className="divide-y divide-brand-line">
            {(releaseInfo.release_notes || []).map((release, index) => (
              <article key={release.id} className="px-5 py-6 sm:px-8">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-serif text-lg font-bold text-brand-ink">{release.title}</h3>
                  {index === 0 && (
                    <span className="rounded-full bg-brand-green/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-brand-green">
                      Latest
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs font-semibold uppercase tracking-wider text-brand-muted">
                  Release {release.version} · <time dateTime={release.published_at}>{formatReleaseDate(release.published_at)}</time>
                </p>
                <p className="mt-3 text-sm leading-relaxed text-brand-ink-2">{release.summary}</p>
                <ul className="mt-4 space-y-3">
                  {(release.highlights || []).map((highlight) => (
                    <li key={highlight.title} className="rounded-lg border border-brand-line bg-brand-bg-soft/40 px-4 py-3">
                      <p className="text-sm font-semibold text-brand-ink">{highlight.title}</p>
                      <p className="mt-1 text-sm leading-relaxed text-brand-ink-2">{highlight.description}</p>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
            {!(releaseInfo.release_notes || []).length && (
              <p className="px-5 py-6 text-sm text-brand-muted sm:px-8">No release notes have been published yet.</p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
