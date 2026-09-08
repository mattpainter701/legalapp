import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getTemplateFieldLibrary, getTemplateFieldUsage } from '../../api'
import TemplateFirmValue from './TemplateFirmValue'

const PAGE_SIZE = 20
const errorMessage = (error) => typeof error?.response?.data?.detail === 'string'
  ? error.response.data.detail : 'The field library could not be loaded. Please try again.'

export default function TemplateFieldLibrary({ refreshKey = 0 }) {
  const [fields, setFields] = useState([])
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState('all')
  const [selectedPath, setSelectedPath] = useState(null)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [usage, setUsage] = useState(null)
  const [usageError, setUsageError] = useState('')
  const [usageRetry, setUsageRetry] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setPage(0)
    getTemplateFieldLibrary().then((data) => {
      if (cancelled) return
      setFields(data.fields)
      setGroup((previous) => previous === 'all' || data.fields.some((field) => field.group === previous) ? previous : 'all')
      setSelectedPath((previous) => data.fields.some((field) => field.path === previous) ? previous : null)
    }).catch((err) => {
      if (!cancelled) setError(errorMessage(err))
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refreshKey, retry])

  useEffect(() => {
    let cancelled = false
    setUsage(null)
    setUsageError('')
    if (!selectedPath || loading || error) return () => { cancelled = true }
    getTemplateFieldUsage({ binding: selectedPath, limit: PAGE_SIZE, offset: page * PAGE_SIZE }).then((data) => {
      if (!cancelled) setUsage(data)
    }).catch((err) => { if (!cancelled) setUsageError(errorMessage(err)) })
    return () => { cancelled = true }
  }, [selectedPath, page, loading, error, usageRetry])

  const groups = useMemo(() => [...new Set(fields.map((field) => field.group))], [fields])
  const filtered = useMemo(() => {
    const search = query.trim().toLowerCase()
    return fields.filter((field) => (group === 'all' || field.group === group)
      && [field.label, field.path, field.suggested_name, field.group].join(' ').toLowerCase().includes(search))
  }, [fields, query, group])
  const selected = fields.find((field) => field.path === selectedPath)

  return (
    <section aria-labelledby="field-library-heading" className="space-y-5">
      <div>
        <h2 id="field-library-heading" className="text-xl font-semibold text-brand-ink">Field Library</h2>
        <p className="mt-1 text-sm text-brand-muted">Reuse firm, client, and matter details across documents. Firm profile values are shared across all matters; client and matter values come from the matter you select. Choose a field to see its explicit mappings in saved templates, including drafts and inactive templates.</p>
      </div>
      <details className="rounded-xl border border-brand-line bg-brand-surface-2 p-4 text-sm">
        <summary className="cursor-pointer font-semibold text-brand-ink">How to mark fields in Word</summary>
        <div className="mt-3 space-y-2 text-brand-muted">
          <p>Use a meaningful placeholder such as <code>{'{{client_name}}'}</code> or <code>{'{{retainer_amount}}'}</code>. In Studio, select the field and choose its <strong>Fills from</strong> source.</p>
          <p>Repeat the exact same placeholder for the same fact. Give different facts different names, such as <code>{'{{retainer_amount}}'}</code> and <code>{'{{hourly_rate}}'}</code>.</p>
          <p>Uppercase brackets such as <code>[CLIENT NAME]</code> are supported. Repeated generic brackets such as <code>[AMOUNT]</code> are reviewed separately. Bold, underline and blank lines are review hints; they do not declare a shared field.</p>
          <p>Keep the entire placeholder together in one paragraph with consistent formatting. Review the source, save the mappings, then test the generated document before publishing.</p>
        </div>
      </details>
      <p className="text-sm text-brand-muted">Custom client and matter fields are managed in Workflow configuration and appear here when active and supported. Fields marked sensitive are unavailable in Studio. Their values remain on individual client or matter records.</p>
      {loading ? <p role="status">Loading shared fields…</p> : error ? (
        <div role="alert"><p>{error}</p><button type="button" className="mt-2 underline" onClick={() => setRetry((value) => value + 1)}>Retry field library</button></div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(260px,1fr)_minmax(0,2fr)]">
          <div className="rounded-xl border border-brand-line bg-brand-surface-2 p-4">
            <label className="block text-sm font-medium" htmlFor="shared-field-search">Find a shared field</label>
            <input id="shared-field-search" value={query} onChange={(event) => setQuery(event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-brand-bg p-2 text-sm" placeholder="Client name, court, fee…" />
            <label className="mt-3 block text-sm font-medium" htmlFor="shared-field-group">Data source</label>
            <select id="shared-field-group" value={group} onChange={(event) => setGroup(event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-brand-bg p-2 text-sm">
              <option value="all">All sources</option>
              {groups.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <p className="my-3 text-xs text-brand-muted">{filtered.length} shared fields</p>
            <ul className="max-h-[34rem] space-y-1 overflow-y-auto" aria-label="Shared fields">
              {filtered.map((field) => (
                <li key={field.path}>
                  <button type="button" aria-pressed={field.path === selectedPath} onClick={() => { setSelectedPath(field.path); setPage(0) }} className={`w-full rounded-lg border p-3 text-left ${field.path === selectedPath ? 'border-brand-ink bg-brand-bg' : 'border-transparent hover:bg-brand-bg'}`}>
                    <span className="block text-sm font-semibold">{field.label}</span>
                    <span className="block text-xs text-brand-muted">{field.group} · {field.template_count} {field.template_count === 1 ? 'template' : 'templates'}</span>
                  </button>
                </li>
              ))}
            </ul>
            {!filtered.length && <p className="text-sm text-brand-muted">No shared fields match these filters.</p>}
          </div>
          <div className="rounded-xl border border-brand-line bg-brand-surface-2 p-5">
            {!selected ? <p className="text-sm text-brand-muted">Select a shared field to see where it is used.</p> : (
              <>
                <h3 className="text-lg font-semibold">{selected.label}</h3>
                {selected.group === 'Firm profile' && <TemplateFirmValue field={selected} />}
                <p className="mt-1 text-sm text-brand-muted">Fills from: {selected.group} / {selected.label}{selected.field_type ? ` · ${selected.field_type.replaceAll('_', ' ')}` : ''}</p>
                {selected.options?.length > 0 && <p className="mt-2 text-sm">Choices: {selected.options.join(', ')}</p>}
                {selected.suggested_name && <p className="mt-3 text-sm">Suggested Word placeholder: <code>{`{{${selected.suggested_name}}}`}</code>. Choose this source in Studio’s <strong>Fills from</strong> menu.</p>}
                {selected.path.startsWith('item.') && <p className="mt-3 text-sm">This field belongs inside a repeating section and fills once for each item.</p>}
                {!selected.suggested_name && !selected.path.startsWith('item.') && <p className="mt-3 text-sm">Give the Word placeholder a descriptive name, then select this field in Studio’s <strong>Fills from</strong> menu.</p>}
                <h4 className="mt-6 font-semibold">Used by these templates</h4>
                <p className="mt-1 text-xs text-brand-muted">Shows explicit source mappings in the current saved version. Name-based guesses, excluded fields and older published versions are not counted. “Use the same value as” shares a value within one template.</p>
                {usageError ? <div role="alert" className="mt-4"><p>{usageError}</p><button type="button" className="mt-2 underline" onClick={() => setUsageRetry((value) => value + 1)}>Retry template usage</button></div> : !usage ? <p role="status" className="mt-4">Loading template usage…</p> : (
                  <>
                    {!usage.total && <p className="mt-4 text-sm">No templates explicitly use this source yet. Open a template, select a field, and set <strong>Fills from</strong> to {selected.label}.</p>}
                    <ul className="mt-4 divide-y divide-brand-line">
                      {usage.items.map((template) => (
                        <li key={template.template_id} className="py-3">
                          <Link className="font-semibold text-brand-ink underline" to={`/templates/${encodeURIComponent(template.template_id)}/studio`}>{template.title}</Link>
                          <p className="mt-1 text-xs text-brand-muted">Version {template.current_version_no} · {(template.status || 'draft').replaceAll('_', ' ')}</p>
                          <p className="mt-1 text-sm">Mapped fields: {template.fields.map((field) => field.label || field.name).join(', ')}</p>
                        </li>
                      ))}
                    </ul>
                    {usage.total > 0 && <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                      <span>{usage.offset + 1}–{usage.offset + usage.items.length} of {usage.total} templates</span>
                      <button type="button" disabled={!page} onClick={() => setPage((value) => value - 1)} className="rounded border border-brand-line px-3 py-1 disabled:opacity-40">Previous</button>
                      <button type="button" disabled={!usage.has_more} onClick={() => setPage((value) => value + 1)} className="rounded border border-brand-line px-3 py-1 disabled:opacity-40">Next</button>
                    </div>}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
