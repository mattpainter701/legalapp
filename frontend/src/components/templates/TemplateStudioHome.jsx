import { AlertTriangle, ArrowRight, CircleCheck, Clock3, Pencil } from 'lucide-react'
import { Link } from 'react-router-dom'

const sourceMissing = (template) => {
  if (!['pdf', 'docx'].includes(template?.format)) return false
  if (typeof template?.source_ready === 'boolean') return !template.source_ready
  return !template?.source_filename || !template?.source_sha256
}

const newestFirst = (left, right) => (
  new Date(right.updated_at || right.created_at || 0) - new Date(left.updated_at || left.created_at || 0)
)

function TemplateLink({ template }) {
  return (
    <Link
      to={`/templates/${template.id}/studio`}
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

export default function TemplateStudioHome({ templates, summary }) {
  const needsAttention = templates.filter(sourceMissing).slice(0, 3)
  const continueSetup = templates.filter((template) => !template.is_active && !sourceMissing(template)).slice(0, 3)
  const ready = templates.filter((template) => template.is_active && !sourceMissing(template)).slice(0, 3)
  const recent = [...templates].sort(newestFirst).slice(0, 4)

  return (
    <section aria-labelledby="studio-home-heading" className="mt-5 space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="studio-home-heading" className="text-lg font-semibold text-brand-ink">Studio home</h2>
          <p className="mt-1 text-sm text-brand-muted">Resume setup, address source problems, or open a ready template. Lists reflect the templates loaded from the current library response.</p>
        </div>
        <Link to="/templates/new" className="text-sm font-semibold text-brand-accent-2 hover:underline">Start a new template</Link>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StudioQueue title="Continue setup" count={summary.inactive || 0} icon={Pencil} templates={continueSetup} empty="No loaded draft templates are waiting for setup." />
        <StudioQueue title="Needs attention" count={summary.source_missing || 0} icon={AlertTriangle} templates={needsAttention} empty="No loaded templates have a missing source." />
        <StudioQueue title="Ready to generate" count={summary.ready || 0} icon={CircleCheck} templates={ready} empty="No loaded templates are ready to generate." />
        <StudioQueue title="Recent templates" count={summary.total || 0} icon={Clock3} templates={recent} empty="Create a template to begin the recent list." />
      </div>
    </section>
  )
}
