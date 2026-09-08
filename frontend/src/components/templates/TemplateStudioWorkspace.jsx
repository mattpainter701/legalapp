import { AlertTriangle, ArrowLeft, CircleCheck, Clock3, Eye, FileText, FlaskConical, History, Loader2, Pencil, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import TemplateStudioEditor from './TemplateStudioEditor'
import TemplateVersionHistory from './TemplateVersionHistory'
import TemplateTestSummary from './TemplateTestSummary'
import TemplateCopyAction from './TemplateCopyAction'
import './templateStudioWorkspace.css'

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
  onTest,
  onPublish,
  source,
  sourceLoading = false,
  sourceError = '',
  onSaveFields,
  onRestored,
  onDerived,
}) {
  const base = `/templates/${encodeURIComponent(String(template.id).toLowerCase())}/studio`
  const statusRef = useRef(null)
  const [focused, setFocused] = useState(true)
  const [hasChanges, setHasChanges] = useState(false)
  const [navigationNote, setNavigationNote] = useState('')
  const guardNavigation = event => {
    if (!hasChanges) return
    event.preventDefault()
    setNavigationNote('Save your field changes or finish the wording edit before leaving this document.')
  }
  const sourceMissing = template.source_ready === false
    || (['pdf', 'docx'].includes(template.format) && (!template.source_filename || !template.source_sha256))
  const lifecycleLabel = sourceMissing
    ? 'Source unavailable'
    : template.status === 'published' && template.is_active
      ? `Published version ${template.published_version_no}`
      : template.status === 'ready_to_publish'
        ? `Version ${template.current_version_no} tested · awaiting publication`
        : template.status === 'test_failed'
          ? 'Test failed · review values and template logic'
        : template.status === 'paused'
          ? 'Published template paused'
          : template.is_active && template.published_version_no
            ? `Draft version ${template.current_version_no} · published version ${template.published_version_no} remains available`
            : 'Draft · test before publishing'

  useEffect(() => {
    if (statusMessage) statusRef.current?.focus()
  }, [statusMessage])

  return (
    <div className={`studio-shell bg-brand-bg ${focused ? 'fixed inset-0 z-40' : 'h-full'}`} data-focused={focused}>
      <main className="studio-shell-main mx-auto w-full" aria-labelledby="template-studio-title">
        <div className="flex items-center justify-between gap-3 px-3 pt-2"><Link to="/templates" onClick={guardNavigation} className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-muted hover:text-brand-ink">
          <ArrowLeft size={16} aria-hidden="true" /> Template Studio
        </Link><button type="button" onClick={() => setFocused(value => !value)} className="text-xs font-semibold text-brand-muted underline">{focused ? 'Show app navigation' : 'Focus on document'}</button></div>
        <header className="border-b border-brand-line bg-brand-surface-2 px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 id="template-studio-title" className="break-words text-lg font-semibold text-brand-ink">{template.title}</h1>
              <p className="mt-1 text-xs text-brand-muted">{lifecycleLabel}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {onDerived && !hasChanges && <TemplateCopyAction key={template.id} template={template} onCreated={onDerived} />}
              <button type="button" disabled={hasChanges} onClick={onEdit} className="inline-flex items-center gap-2 rounded-lg border border-brand-line text-brand-ink px-3 py-2 text-sm font-semibold hover:bg-brand-bg disabled:opacity-40">
                <Pencil size={16} aria-hidden="true" /> Template settings
              </button>
              <button type="button" onClick={onGenerate} disabled={sourceMissing || hasChanges} className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
                {template.is_active ? <Sparkles size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                {template.is_active ? 'Generate' : 'Preview draft'}
              </button>
              {template.published_version_no !== template.current_version_no && template.tested_version_no === template.current_version_no && template.current_version_no > 0 && (
                <button type="button" onClick={onPublish} disabled={hasChanges} className="inline-flex items-center gap-2 rounded-lg bg-brand-green px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">
                  <CircleCheck size={16} aria-hidden="true" /> Publish tested version
                </button>
              )}
            </div>
          </div>
        </header>



        <nav aria-label="Template Studio workspace sections" className="flex gap-1 overflow-x-auto border-b border-brand-line bg-brand-surface-2 px-2 py-1">
          {tabs.map(({ key, label, suffix, icon: Icon }) => (
            <Link key={key} to={`${base}${suffix}`} onClick={guardNavigation} aria-current={section === key ? 'page' : undefined} className={`inline-flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-semibold ${section === key ? 'bg-brand-ink text-white' : 'text-brand-muted hover:bg-brand-bg hover:text-brand-ink'}`}>
              <Icon size={15} aria-hidden="true" /> {label}
            </Link>
          ))}
        </nav>
        {hasChanges && navigationNote && <p role="alert" className="px-3 py-2 text-sm text-brand-amber">{navigationNote}</p>}

        {statusMessage && <div ref={statusRef} role="status" aria-label="Workspace status" tabIndex={-1} className="mt-4 rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">{statusMessage}</div>}

        {section === 'workspace' ? (
          <section className="studio-editing-area" aria-label="Template workspace summary">
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
                  onDerived={onDerived}
                  onDirtyChange={setHasChanges}
                />
              )}
            </div>
          </section>
        ) : section === 'test' ? (
          <section className="mt-4 rounded-xl border border-brand-line bg-brand-surface-2 p-6" aria-labelledby="studio-test-title">
            <h2 id="studio-test-title" className="text-lg font-semibold text-brand-ink">Test this exact draft</h2>
            <TemplateTestSummary template={template} />
            <p className="mt-2 max-w-2xl text-sm leading-6 text-brand-muted">Generate with representative values and inspect the result. A successful test is tied to version {template.current_version_no || 'the first saved snapshot'}; any field, content, or logic edit invalidates it.</p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button type="button" onClick={onTest || onGenerate} disabled={sourceMissing} className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">
                <FlaskConical size={16} aria-hidden="true" /> Open test values and preview
              </button>
              <span className={`text-sm font-semibold ${template.tested_version_no === template.current_version_no && template.current_version_no > 0 ? 'text-brand-green' : 'text-brand-amber'}`}>
                {template.tested_version_no === template.current_version_no && template.current_version_no > 0 ? `Version ${template.current_version_no} passed` : template.status === 'test_failed' ? 'Latest test failed' : 'Not tested since the latest edit'}
              </span>
            </div>
          </section>
        ) : section === 'versions' || section === 'activity' ? (
          <section className="mt-4" aria-labelledby={`studio-${section}-title`}>
            <h2 id={`studio-${section}-title`} className="text-lg font-semibold capitalize text-brand-ink">{section}</h2>
            <p className="mt-1 text-sm text-brand-muted">
              {section === 'versions'
                ? 'Every immutable draft and published state, newest first. Restore an earlier one without retyping it.'
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
        <details aria-label="Template setup checklist" className="mt-3 rounded-lg border border-brand-line bg-brand-surface-2 px-4 py-2 text-sm">
          <summary className="cursor-pointer font-semibold">Help with setup</summary>
          <p className="mt-2 break-words text-xs text-brand-muted">{template.source_filename || template.format || 'Document'}{template.description ? ` · ${template.description}` : ''}</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-brand-muted">
            <li>Review highlighted details. Select text or place a box to add a field; labels and data sources can be changed below.</li>
            <li>Choose what each field fills from, and use “When to use this template” for a scenario such as divorce with children.</li>
            <li>Open Test, choose a matter for Smart Fill, review missing details and source evidence, then inspect every output page.</li>
            <li>Publish the tested version when the wording and values are correct.{template.is_active && template.published_version_no ? ` Version ${template.published_version_no} remains available to your team while you edit.` : ''}</li>
          </ol>
        </details>
      </main>
    </div>
  )
}
