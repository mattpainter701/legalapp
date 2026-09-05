// The Studio Versions and Activity tabs. Both read the same append-only
// history: every number identifies the exact immutable state shown in its row.

import { useCallback, useEffect, useState } from 'react'
import { History, Loader2, RotateCcw } from 'lucide-react'

import { listTemplateVersions, restoreTemplateVersion } from '../../api'

const formatWhen = (value) => {
  const when = new Date(value)
  return Number.isNaN(when.getTime()) ? String(value || '') : when.toLocaleString()
}

/** Describe a version by what changed against the one before it, so the list
 *  reads as a history rather than a column of identical-looking rows. */
export const describeChange = (version, previous) => {
  if (version.change_summary) return version.change_summary
  if (!previous) return 'First recorded version'
  const changes = []
  if (version.title !== previous.title) changes.push('renamed')
  if (version.body_sha256 !== previous.body_sha256) changes.push('content edited')
  if (version.field_count !== previous.field_count) {
    changes.push(`fields ${previous.field_count} → ${version.field_count}`)
  }
  if (version.source_sha256 !== previous.source_sha256) changes.push('source replaced')
  if (version.is_active !== previous.is_active) {
    changes.push(version.is_active ? 'activated' : 'deactivated')
  }
  return changes.length ? changes.join(', ') : 'No tracked change'
}

export default function TemplateVersionHistory({ templateId, mode = 'versions', onRestored }) {
  const [state, setState] = useState({ status: 'loading', versions: [], total: 0, current: 0, tested: null, published: null })
  const [busyVersion, setBusyVersion] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    let cancelled = false
    setState((previous) => ({ ...previous, status: 'loading' }))
    listTemplateVersions(templateId)
      .then((data) => {
        if (cancelled) return
        setState({
          status: 'ready',
          versions: data?.versions || [],
          total: data?.total || 0,
          current: data?.current_version_no || 0,
          tested: data?.tested_version_no || null,
          published: data?.published_version_no || null,
        })
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error', versions: [], total: 0, current: 0, tested: null, published: null })
      })
    return () => { cancelled = true }
  }, [templateId])

  useEffect(() => load(), [load])

  const restore = async (versionNo) => {
    setBusyVersion(versionNo)
    setError('')
    try {
      const template = await restoreTemplateVersion(templateId, versionNo)
      onRestored?.(template)
      load()
    } catch (caught) {
      setError(
        caught?.response?.data?.detail
        || 'That version could not be restored.',
      )
    } finally {
      setBusyVersion(null)
    }
  }

  if (state.status === 'loading') {
    return (
      <div role="status" aria-label="History loading status" className="flex items-center gap-2 rounded-xl border border-brand-line bg-brand-surface-2 px-5 py-10 text-sm text-brand-muted">
        <Loader2 size={16} className="animate-spin" aria-hidden="true" />
        Loading history…
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <p role="alert" className="rounded-xl border border-brand-amber/40 bg-brand-amber/10 px-5 py-4 text-sm text-brand-ink">
        This template&rsquo;s history could not be loaded. Reopen the tab to try again.
      </p>
    )
  }

  if (!state.versions.length) {
    return (
      <div className="rounded-xl border border-dashed border-brand-line bg-brand-surface-2 px-6 py-12 text-center">
        <History size={20} className="mx-auto text-brand-muted" aria-hidden="true" />
        <h2 className="mt-3 text-lg font-semibold text-brand-ink">No history yet</h2>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-brand-muted">
          Save or test this draft to create its first immutable version. Nothing has been
          recorded so far, so there is nothing to compare or restore.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {error && (
        <p role="alert" className="rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
          {error}
        </p>
      )}
      <p className="text-sm text-brand-muted">
        {state.total} recorded {state.total === 1 ? 'version' : 'versions'}. The template is on
        version {state.current}.
      </p>
      <ol className="space-y-2">
        {state.versions.map((version, index) => (
          <li
            key={version.version_no}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-line bg-brand-surface-2 px-4 py-3"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-brand-ink">
                Version {version.version_no} · {version.title}
              </p>
              <p className="mt-0.5 text-xs text-brand-muted">
                {formatWhen(version.created_at)} · {describeChange(version, state.versions[index + 1])}
                {version.is_active ? ' · was active' : ''}
                {version.version_no === state.tested ? ' · tested' : ''}
                {version.version_no === state.published ? ' · published' : ''}
              </p>
            </div>
            {mode === 'versions' && (
              <button
                type="button"
                onClick={() => restore(version.version_no)}
                disabled={busyVersion !== null}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-brand-line px-2.5 py-1.5 text-xs font-semibold text-brand-muted hover:border-brand-accent/60 hover:text-brand-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busyVersion === version.version_no
                  ? <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  : <RotateCcw size={14} aria-hidden="true" />}
                Restore
              </button>
            )}
          </li>
        ))}
      </ol>
      {mode === 'versions' && (
        <p className="text-xs leading-5 text-brand-muted">
          Restoring puts the selected wording back as a new immutable draft version. The
          template stays unpublished until that exact version is tested and approved again.
        </p>
      )}
    </div>
  )
}
