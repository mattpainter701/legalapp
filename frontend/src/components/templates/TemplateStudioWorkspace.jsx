import { AlertTriangle, ArrowLeft, Clock3, Eye, FileText, FlaskConical, History, Loader2, Pencil, Sparkles } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'

import TemplateStudioEditor from './TemplateStudioEditor'
import TemplateVersionHistory from './TemplateVersionHistory'

const tabs = [
  { key: 'workspace', label: 'Workspace', suffix: '', icon: FileText },
  { key: 'test', label: 'Test', suffix: '/test', icon: FlaskConical },
  { key: 'versions', label: 'Versions', suffix: '/versions', icon: History },
  { key: 'activity', label: 'Activity', suffix: '/activity', icon: Clock3 },
]

export default function TemplateStudioWorkspace({
  template,
  section = 'workspace',
  statusMessage,
  onEdit,
  onGenerate,
  source,
  sourceLoading = false,
  sourceError = '',
  onSaveFields,
  onRestored,
}) {
  const base = `/templates/${encodeURIComponent(String(template.id).toLowerCase())}/studio`
  const statusRef = useRef(null)
  const sourceMissing = template.source_ready === false
    || (['pdf', 'docx'].includes(template.format) && (!template.source_filename || !template.source_sha256))

  useEffect(() => {
    if (statusMessage) statusRef.current?.focus()
  }, [statusMessage])

  return (
    <div className="h-full overflow-y-auto bg-brand-bg p-4 md:p-6">
      <main className="mx-auto max-w-6xl" aria-labelledby="template-studio-title">
        <Link to="/templates" className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-muted hover:text-brand-ink">
          <ArrowLeft size={16} aria-hidden="true" /> Template Studio
        </Link>
        <header className="mt-4 rounded-2xl bg-brand-ink p-5 text-white shadow-lg md:p-7">
          <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-white/60">Template workspace</p>
              <h1 id="template-studio-title" className="mt-2 truncate text-2xl font-semibold md:text-3xl">{template.title}</h1>
              <p className="mt-2 text-sm text-white/70">{template.description || `Review this ${template.format || 'document'} template, its fields, and generation readiness.`}</p>
            </div>
            <div className="flex gap-2">
              <button type="button" onClick={onEdit} className="inline-flex items-center gap-2 rounded-lg border border-white/20 px-4 py-2 text-sm font-semibold hover:bg-white/10">
                <Pencil size={16} aria-hidden="true" /> Edit template
              </button>
              <button type="button" onClick={onGenerate} disabled={sourceMissing} className="inline-flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-brand-ink disabled:cursor-not-allowed disabled:opacity-50">
                {template.is_active ? <Sparkles size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                {template.is_active ? 'Generate' : 'Preview draft'}
              </button>
            </div>
          </div>
        </header>

        <nav aria-label="Template Studio workspace sections" className="mt-4 flex gap-1 overflow-x-auto rounded-xl border border-brand-line bg-brand-surface-2 p-1">
          {tabs.map(({ key, label, suffix, icon: Icon }) => (
            <Link key={key} to={`${base}${suffix}`} aria-current={section === key ? 'page' : undefined} className={`inline-flex items-center gap-2 whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-semibold ${section === key ? 'bg-brand-ink text-white' : 'text-brand-muted hover:bg-brand-bg hover:text-brand-ink'}`}>
              <Icon size={15} aria-hidden="true" /> {label}
            </Link>
          ))}
        </nav>

        {statusMessage && <div ref={statusRef} role="status" aria-label="Workspace status" tabIndex={-1} className="mt-4 rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">{statusMessage}</div>}

        {section === 'workspace' ? (
          <section className="mt-4 grid gap-4 md:grid-cols-3" aria-label="Template workspace summary">
            <div className="rounded-xl border border-brand-line bg-brand-surface-2 p-5 md:col-span-2">
              <h2 className="font-semibold text-brand-ink">Current template</h2>
              <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
                <div><dt className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Format</dt><dd className="mt-1 text-brand-ink">{template.format || 'markdown'}</dd></div>
                <div><dt className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Status</dt><dd className="mt-1 text-brand-ink">{sourceMissing ? 'Needs source' : template.is_active ? 'Ready to generate' : 'Continue setup'}</dd></div>
                <div><dt className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Source</dt><dd className="mt-1 break-words text-brand-ink">{template.source_filename || 'No retained source filename'}</dd></div>
                <div><dt className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Fields</dt><dd className="mt-1 text-brand-ink">{template.variable_schema?.fields?.filter((field) => field.included !== false).length || 0}</dd></div>
              </dl>
            </div>
            <aside className="rounded-xl border border-brand-line bg-brand-surface-2 p-5">
              <h2 className="font-semibold text-brand-ink">Field placement</h2>
              <p className="mt-2 text-sm leading-6 text-brand-muted">Drag a field onto the page to set where its value is written. Positions are stored in the template and reused every time it generates.</p>
            </aside>
            {sourceMissing && (
              <div role="alert" className="flex gap-3 rounded-xl border border-brand-amber/40 bg-brand-amber/10 p-4 md:col-span-3">
                <AlertTriangle className="mt-0.5 shrink-0 text-brand-amber" size={18} aria-hidden="true" />
                <p className="text-sm text-brand-ink">The retained source is unavailable. Return to Studio home and recreate the template from the original sample before generation.</p>
              </div>
            )}
            <div className="md:col-span-3">
              {sourceLoading ? (
                <div className="flex items-center gap-2 rounded-xl border border-brand-line bg-brand-surface-2 px-5 py-10 text-sm text-brand-muted" role="status" aria-label="Document loading status">
                  <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                  Loading the document…
                </div>
              ) : (
                <TemplateStudioEditor
                  template={template}
                  source={source}
                  sourceError={sourceError}
                  onSave={onSaveFields}
                />
              )}
            </div>
          </section>
        ) : section === 'versions' || section === 'activity' ? (
          <section className="mt-4" aria-labelledby={`studio-${section}-title`}>
            <h2 id={`studio-${section}-title`} className="text-lg font-semibold capitalize text-brand-ink">{section}</h2>
            <p className="mt-1 text-sm text-brand-muted">
              {section === 'versions'
                ? 'Every published wording of this template, newest first. Restore an earlier one without retyping it.'
                : 'What changed on this template and when, drawn from its recorded versions.'}
            </p>
            <div className="mt-4">
              <TemplateVersionHistory
                templateId={template.id}
                mode={section}
                onRestored={onRestored}
              />
            </div>
          </section>
        ) : (
          <section className="mt-4 rounded-xl border border-dashed border-brand-line bg-brand-surface-2 px-6 py-12 text-center" aria-labelledby={`studio-${section}-title`}>
            <h2 id={`studio-${section}-title`} className="text-lg font-semibold capitalize text-brand-ink">{section}</h2>
            <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-brand-muted">This route is reserved for the Template Studio {section} view. No {section} records or controls are available in Phase 1; the current template remains unchanged.</p>
          </section>
        )}
      </main>
    </div>
  )
}
