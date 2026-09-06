import { AlertTriangle, ArrowRight, CircleCheck, Clock3, Pencil } from 'lucide-react'
import { Link } from 'react-router-dom'

const sourceMissing = (template) => {
  if (!['pdf', 'docx'].includes(template?.format)) return false
  if (typeof template?.source_ready === 'boolean') return !template.source_ready
  return !template?.source_filename || !template?.source_sha256
}

function TemplateLink({ template }) {
  return (
    <Link
      to={`/templates/${encodeURIComponent(template.id)}/studio`}
      className="group flex items-center justify-between gap-3 rounded-lg border border-brand-line bg-brand-bg px-3 py-2.5 hover:border-brand-accent/40"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-brand-ink">{template.title}</span>
        <span className="block text-xs text-brand-muted">{template.format || 'markdown'}</span>
      </span>
      <ArrowRight size={15} className="shrink-0 text-brand-muted group-hover:text-brand-accent-2" aria-hidden="true" />
    </Link>
  )
}

function StudioQueue({ title, count, icon: Icon, empty, templates }) {
  return (
    <section className="rounded-xl border border-brand-line bg-brand-surface-2 p-4 shadow-sm" aria-labelledby={`studio-${title.toLowerCase().replaceAll(' ', '-')}`}>
      <div className="flex items-center justify-between gap-3">
        <h2 id={`studio-${title.toLowerCase().replaceAll(' ', '-')}`} className="flex items-center gap-2 text-sm font-semibold text-brand-ink">
          <Icon size={16} aria-hidden="true" /> {title}
        </h2>
        <span className="rounded-full bg-brand-bg px-2 py-0.5 text-xs font-semibold text-brand-muted" aria-label={`${count} total`}>{count}</span>
      </div>
      <div className="mt-3 space-y-2">
        {templates.length ? templates.map((template) => <TemplateLink key={template.id} template={template} />) : (
          <p className="rounded-lg border border-dashed border-brand-line px-3 py-4 text-xs leading-5 text-brand-muted">{empty}</p>
        )}
      </div>
    </section>
  )
}

export default function TemplateStudioHome({ templates, summary, queues }) {
  const fallback = {
    needs_attention: { total: summary.source_missing || 0, items: templates.filter(sourceMissing).slice(0, 3) },
    continue_setup: { total: summary.inactive || 0, items: templates.filter((template) => !template.is_active && !sourceMissing(template)).slice(0, 3) },
    awaiting_publish: { total: 0, items: [] },
    published: { total: summary.ready || 0, items: templates.filter((template) => template.is_active && !sourceMissing(template)).slice(0, 3) },
  }
  const studioQueues = queues || fallback

  return (
    <section aria-labelledby="studio-home-heading" className="mt-5 space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="studio-home-heading" className="text-lg font-semibold text-brand-ink">Studio home</h2>
          <p className="mt-1 text-sm text-brand-muted">Resume setup, address source problems, test a draft, or open a published template. Queue counts and cards cover the complete firm library.</p>
        </div>
        <Link to="/templates/new" className="text-sm font-semibold text-brand-accent-2 hover:underline">Start a new template</Link>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StudioQueue title="Continue setup" count={studioQueues.continue_setup.total} icon={Pencil} templates={studioQueues.continue_setup.items} empty="No draft templates are waiting for setup." />
        <StudioQueue title="Needs attention" count={studioQueues.needs_attention.total} icon={AlertTriangle} templates={studioQueues.needs_attention.items} empty="No templates have a missing source." />
        <StudioQueue title="Awaiting publish" count={studioQueues.awaiting_publish.total} icon={Clock3} templates={studioQueues.awaiting_publish.items} empty="No tested drafts are awaiting publication." />
        <StudioQueue title="Published" count={studioQueues.published.total} icon={CircleCheck} templates={studioQueues.published.items} empty="No templates are published for generation." />
      </div>
    </section>
  )
}
