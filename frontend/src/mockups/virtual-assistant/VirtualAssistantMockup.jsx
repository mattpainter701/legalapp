import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Briefcase,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardCheck,
  Clock3,
  FileStack,
  FileText,
  ListChecks,
  Loader2,
  MessageSquare,
  Mic,
  Minus,
  Plus,
  ReceiptText,
  RefreshCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  UserPlus,
  X,
} from 'lucide-react'
import LawHandLogo from '../../components/LawHandLogo'
import {
  CONTRACT_TYPES,
  DEMO_PROMPTS,
  MATTER_OPTIONS,
  PLEADING_TYPES,
  createWorkflow,
  detectScenario,
  getMatter,
  getWorkflowCopy,
  getWorkflowReadiness,
} from './fixtures'

const FIELD_CLASS = 'min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink shadow-sm focus:border-brand-accent focus:outline-none focus:ring-2 focus:ring-brand-accent/15'

const PROMPT_ICONS = {
  intake: UserPlus,
  time: Clock3,
  documents: FileStack,
}

const FLOW_ICONS = {
  intake: UserPlus,
  time: Clock3,
  documents: FileStack,
}

const ASSISTANT_RESPONSES = {
  intake: 'I parsed this as two linked changes. I’ll use a prospective-client status until conflict review and create a task—not a calendar event.',
  time: 'I found two accessible matters associated with Joel. Pick the exact matter and confirm billing before I prepare the entry.',
  documents: 'I can prepare three private drafts. I need the matter, pleading type, and contract type before I generate anything.',
}

function StatusChip({ readiness }) {
  const styles = {
    needs_input: 'border-brand-amber/30 bg-brand-amber/10 text-amber-800',
    ready: 'border-brand-accent/25 bg-brand-accent/10 text-brand-accent-2',
    running: 'border-brand-accent/25 bg-brand-accent/10 text-brand-accent-2',
    completed: 'border-brand-green/30 bg-brand-green/10 text-brand-green',
  }

  return (
    <span className={`inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 text-[10px] font-bold uppercase tracking-[0.12em] ${styles[readiness.status]}`}>
      {readiness.status === 'completed' && <Check size={12} aria-hidden="true" />}
      {readiness.status === 'running' && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
      {readiness.label}
    </span>
  )
}

function PromptCard({ prompt, onChoose, compact = false }) {
  const Icon = PROMPT_ICONS[prompt.id]
  return (
    <button
      type="button"
      onClick={() => onChoose(prompt.prompt)}
      className={`group text-left ${compact
        ? 'inline-flex min-h-10 shrink-0 items-center gap-2 rounded-full border border-brand-line bg-brand-surface px-3 text-xs font-semibold text-brand-ink hover:border-brand-line-2 hover:bg-brand-bg-soft'
        : 'rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm hover:-translate-y-0.5 hover:border-brand-line-2 hover:shadow-md'
      }`}
    >
      <span className={`${compact ? 'contents' : 'flex items-start gap-3'}`}>
        <span className={`flex shrink-0 items-center justify-center bg-brand-accent/10 text-brand-accent-2 ${compact ? 'h-6 w-6 rounded-full' : 'h-10 w-10 rounded-xl'}`}>
          <Icon size={compact ? 13 : 18} aria-hidden="true" />
        </span>
        <span className="min-w-0">
          <span className={`block font-semibold text-brand-ink ${compact ? 'whitespace-nowrap' : 'text-sm'}`}>
            {compact ? prompt.shortLabel : prompt.label}
          </span>
          {!compact && (
            <span className="mt-1 block text-xs leading-relaxed text-brand-muted">
              {prompt.description}
            </span>
          )}
        </span>
      </span>
    </button>
  )
}

function DesktopSidebar({ onReset, onPlaceholderNavigation }) {
  const items = [
    { label: 'Assistant', icon: MessageSquare, active: true },
    { label: 'My matters', icon: Briefcase },
    { label: 'Calendar', icon: CalendarDays },
    { label: 'Tasks', icon: ListChecks },
    { label: 'Time entries', icon: Clock3 },
  ]

  return (
    <aside aria-label="Workspace navigation" className="hidden h-full w-[248px] shrink-0 flex-col border-r border-brand-line bg-brand-surface lg:flex">
      <div className="flex h-16 items-center border-b border-brand-line px-5">
        <LawHandLogo compact />
      </div>
      <nav className="flex-1 space-y-1 p-3" aria-label="Prototype navigation">
        {items.map(({ label, icon: Icon, active }) => (
          <button
            key={label}
            type="button"
            onClick={() => active ? null : onPlaceholderNavigation(label)}
            aria-current={active ? 'page' : undefined}
            className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-semibold ${active
              ? 'bg-brand-bg-soft text-brand-ink'
              : 'text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink'
            }`}
          >
            <Icon size={18} className={active ? 'text-brand-accent-2' : ''} aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>
      <div className="border-t border-brand-line p-4">
        <div className="rounded-2xl border border-brand-accent/20 bg-brand-accent/5 p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">Prototype</p>
          <p className="mt-1 text-xs leading-relaxed text-brand-muted">No records are written. Use this surface to test flow and feel.</p>
        </div>
        <button
          type="button"
          onClick={onReset}
          className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-brand-line text-sm font-semibold text-brand-ink hover:bg-brand-bg-soft"
        >
          <RefreshCcw size={15} aria-hidden="true" /> Reset demo
        </button>
      </div>
    </aside>
  )
}

function MobileBottomNav({ onPlaceholderNavigation }) {
  const items = [
    { label: 'Assistant', icon: MessageSquare, active: true },
    { label: 'Matters', icon: Briefcase },
    { label: 'Calendar', icon: CalendarDays },
    { label: 'Tasks', icon: ListChecks },
  ]

  return (
    <nav
      className="grid shrink-0 grid-cols-4 border-t border-brand-line bg-brand-surface lg:hidden"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      aria-label="Mobile prototype navigation"
    >
      {items.map(({ label, icon: Icon, active }) => (
        <button
          key={label}
          type="button"
          onClick={() => active ? null : onPlaceholderNavigation(label)}
          aria-current={active ? 'page' : undefined}
          className={`flex min-h-[58px] flex-col items-center justify-center gap-1 text-[10px] font-semibold ${active ? 'text-brand-accent-2' : 'text-brand-muted'}`}
        >
          <Icon size={19} aria-hidden="true" />
          {label}
        </button>
      ))}
    </nav>
  )
}

function MessageBubble({ item }) {
  const assistant = item.role === 'assistant'
  return (
    <div className={`flex ${assistant ? 'justify-start' : 'justify-end'}`}>
      <div className={`flex max-w-[92%] items-start gap-2.5 sm:max-w-[78%] ${assistant ? '' : 'flex-row-reverse'}`}>
        {assistant && (
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-brand-ink text-white shadow-sm">
            <Sparkles size={15} aria-hidden="true" />
          </span>
        )}
        <div className={`rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${assistant
          ? 'rounded-tl-md border border-brand-line bg-brand-surface text-brand-ink'
          : 'rounded-tr-md bg-brand-ink text-white'
        }`}>
          {item.text}
        </div>
      </div>
    </div>
  )
}

function OptionButtons({ label, options, value, onChange }) {
  return (
    <div>
      <p className="mb-2 text-xs font-semibold text-brand-ink">{label}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const normalized = typeof option === 'string' ? { value: option, label: option } : option
          const selected = normalized.value === value
          return (
            <button
              key={normalized.value}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(normalized.value)}
              className={`inline-flex min-h-10 items-center gap-1.5 rounded-xl border px-3 text-xs font-semibold ${selected
                ? 'border-brand-accent bg-brand-accent/10 text-brand-accent-2'
                : 'border-brand-line bg-brand-surface text-brand-ink hover:bg-brand-bg-soft'
              }`}
            >
              {selected && <Check size={13} aria-hidden="true" />}
              {normalized.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function LabeledSelect({ id, label, value, onChange, children, hint }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-semibold text-brand-ink">{label}</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)} className={FIELD_CLASS}>
        {children}
      </select>
      {hint && <p className="mt-1.5 text-[11px] leading-relaxed text-brand-muted">{hint}</p>}
    </div>
  )
}

function GuardrailNote({ children }) {
  return (
    <div className="flex items-start gap-2 rounded-xl border border-brand-amber/25 bg-brand-amber/10 px-3 py-2.5 text-xs leading-relaxed text-amber-900">
      <ShieldCheck size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  )
}

function IntakeDetails({ workflow, onUpdate }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-brand-line bg-brand-bg/70 p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">Contact</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">Jane Doe</p>
          <p className="mt-0.5 text-xs text-brand-muted">(701) 123-2255 · unverified</p>
        </div>
        <div className="rounded-xl border border-brand-line bg-brand-bg/70 p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">Task</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">Schedule consultation with Jane Doe</p>
          <p className="mt-0.5 text-xs text-brand-muted">Intake · Medium priority</p>
        </div>
      </div>

      <div className="flex items-start gap-2 rounded-xl border border-brand-green/25 bg-brand-green/10 px-3 py-2.5">
        <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-brand-green" aria-hidden="true" />
        <div>
          <p className="text-xs font-semibold text-brand-ink">Demo duplicate check: no exact match</p>
          <p className="mt-0.5 text-[11px] text-brand-muted">Phone plausibility still needs human verification.</p>
        </div>
      </div>

      <LabeledSelect
        id={`contact-type-${workflow.id}`}
        label="Contact status"
        value={workflow.contactType}
        onChange={(contactType) => onUpdate({ contactType })}
        hint="Prospective client is the safer default until conflict clearance."
      >
        <option value="prospect">Prospective client</option>
        <option value="client">Client</option>
      </LabeledSelect>

      <div className="grid gap-3 sm:grid-cols-2">
        <LabeledSelect
          id={`assignee-${workflow.id}`}
          label="Task owner"
          value={workflow.assignee}
          onChange={(assignee) => onUpdate({ assignee })}
        >
          <option value="me">Me · Alex Morgan</option>
          <option value="intake">Intake team</option>
          <option value="unassigned">Unassigned</option>
        </LabeledSelect>
        <LabeledSelect
          id={`due-${workflow.id}`}
          label="Task due"
          value={workflow.due}
          onChange={(due) => onUpdate({ due })}
        >
          <option value="none">No due date</option>
          <option value="tomorrow">Tomorrow · Aug 5</option>
          <option value="friday">Friday · Aug 7</option>
        </LabeledSelect>
      </div>

      <GuardrailNote>
        This creates a contact and a task to schedule the consultation. It does not clear conflicts, open a matter, call or text Jane, create a calendar event, or send an invitation.
      </GuardrailNote>
    </div>
  )
}

function TimeDetails({ workflow, onUpdate }) {
  const matter = getMatter(workflow.matterId)
  const amount = matter ? workflow.hours * matter.rate : null

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-brand-amber/25 bg-brand-amber/10 px-3 py-2.5">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-brand-amber" aria-hidden="true" />
        <div>
          <p className="text-xs font-semibold text-brand-ink">“Joel’s case” matched two matters</p>
          <p className="mt-0.5 text-[11px] text-brand-muted">Select the exact client and case number before review.</p>
        </div>
      </div>

      <LabeledSelect
        id={`time-matter-${workflow.id}`}
        label="Exact matter"
        value={workflow.matterId}
        onChange={(matterId) => onUpdate({ matterId })}
      >
        <option value="">Choose a Joel matter…</option>
        {MATTER_OPTIONS.filter((item) => item.client.startsWith('Joel')).map((item) => (
          <option key={item.id} value={item.id}>{item.name} · {item.caseNumber}</option>
        ))}
      </LabeledSelect>

      <div>
        <label htmlFor={`time-description-${workflow.id}`} className="mb-1.5 block text-xs font-semibold text-brand-ink">Description</label>
        <input
          id={`time-description-${workflow.id}`}
          value={workflow.description}
          onChange={(event) => onUpdate({ description: event.target.value })}
          className={FIELD_CLASS}
        />
        <p className="mt-1.5 text-[11px] text-brand-muted">Suggested from your wording—edit if “planning case ruling” meant something else.</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`time-date-${workflow.id}`} className="mb-1.5 block text-xs font-semibold text-brand-ink">Work date</label>
          <input
            id={`time-date-${workflow.id}`}
            type="date"
            value={workflow.date}
            onChange={(event) => onUpdate({ date: event.target.value })}
            className={FIELD_CLASS}
          />
        </div>
        <div>
          <p className="mb-1.5 text-xs font-semibold text-brand-ink">Hours</p>
          <div className="flex min-h-11 items-center rounded-xl border border-brand-line bg-brand-surface shadow-sm">
            <button
              type="button"
              onClick={() => onUpdate({ hours: Math.max(0.25, workflow.hours - 0.25) })}
              className="tap-target rounded-l-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink"
              aria-label="Reduce hours"
            >
              <Minus size={16} />
            </button>
            <span className="flex-1 text-center text-sm font-semibold text-brand-ink">{workflow.hours.toFixed(2)}</span>
            <button
              type="button"
              onClick={() => onUpdate({ hours: workflow.hours + 0.25 })}
              className="tap-target rounded-r-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink"
              aria-label="Increase hours"
            >
              <Plus size={16} />
            </button>
          </div>
        </div>
      </div>

      <OptionButtons
        label="Billing status"
        value={workflow.billable}
        onChange={(billable) => onUpdate({ billable })}
        options={[
          { value: true, label: 'Billable' },
          { value: false, label: 'Non-billable' },
        ]}
      />

      <div className="grid grid-cols-2 gap-3 rounded-xl border border-brand-line bg-brand-bg/70 p-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">Rate</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">{matter ? `$${matter.rate}/hour` : 'After matter selection'}</p>
        </div>
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">Draft value</p>
          <p className="mt-1 text-sm font-semibold text-brand-ink">{amount == null ? '—' : `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2 })}`}</p>
        </div>
      </div>

      <GuardrailNote>
        The timekeeper will be the signed-in user. This creates a draft, unbilled time entry; it does not create or send an invoice.
      </GuardrailNote>
    </div>
  )
}

function DocumentDetails({ workflow, onUpdate }) {
  return (
    <div className="space-y-4">
      <LabeledSelect
        id={`document-matter-${workflow.id}`}
        label="Matter for all drafts"
        value={workflow.matterId}
        onChange={(matterId) => onUpdate({ matterId })}
        hint="The selected matter supplies parties, caption, and approved firm context."
      >
        <option value="">Choose a matter…</option>
        {MATTER_OPTIONS.map((item) => (
          <option key={item.id} value={item.id}>{item.name} · {item.caseNumber}</option>
        ))}
      </LabeledSelect>

      <div className="space-y-3">
        <section className="rounded-xl border border-brand-line bg-brand-bg/60 p-3">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-brand-ink">1. Pleading</p>
              <p className="mt-0.5 text-[11px] text-brand-muted">Choose the filing type; the assistant will not infer requested relief.</p>
            </div>
            <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${workflow.pleadingType ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-amber/10 text-amber-800'}`}>
              {workflow.pleadingType ? 'Scoped' : 'Needs type'}
            </span>
          </div>
          <OptionButtons
            label="Pleading type"
            options={PLEADING_TYPES}
            value={workflow.pleadingType}
            onChange={(pleadingType) => onUpdate({ pleadingType })}
          />
        </section>

        <section className="rounded-xl border border-brand-line bg-brand-bg/60 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-brand-ink">2. Standard fee agreement</p>
              <p className="mt-0.5 text-[11px] text-brand-muted">Interpreted “XYZ standard fee” as the active firm template.</p>
            </div>
            <span className="shrink-0 rounded-full bg-brand-green/10 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-brand-green">Template match</span>
          </div>
          <div className="mt-3 rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-xs font-semibold text-brand-ink">
            {workflow.feeTemplate}
          </div>
        </section>

        <section className="rounded-xl border border-brand-line bg-brand-bg/60 p-3">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-brand-ink">3. Contract</p>
              <p className="mt-0.5 text-[11px] text-brand-muted">“Contracts” is too broad to draft without an agreement type.</p>
            </div>
            <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${workflow.contractType ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-amber/10 text-amber-800'}`}>
              {workflow.contractType ? 'Scoped' : 'Needs type'}
            </span>
          </div>
          <LabeledSelect
            id={`contract-type-${workflow.id}`}
            label="Agreement type"
            value={workflow.contractType}
            onChange={(contractType) => onUpdate({ contractType })}
          >
            <option value="">Choose contract type…</option>
            {CONTRACT_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
          </LabeledSelect>
        </section>
      </div>

      <GuardrailNote>
        Draft preparation only. This action will not file a pleading, publish to the portal, send email, request signatures, or save a final document to the matter.
      </GuardrailNote>
    </div>
  )
}

function CompletedReceipt({ workflow }) {
  const copy = getWorkflowCopy(workflow)
  return (
    <div className="rounded-xl border border-brand-green/30 bg-brand-green/10 p-3" role="status">
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-green text-white">
          <Check size={16} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-brand-ink">{copy.receiptTitle}</p>
          <p className="mt-1 text-xs leading-relaxed text-brand-ink-2">{copy.receiptDetail}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-[10px] font-bold uppercase tracking-[0.1em] text-brand-green">
            <span>Mock receipt</span>
            <span aria-hidden="true">·</span>
            <span>No backend write</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function WorkflowCard({ workflow, onUpdate, onReview }) {
  const readiness = getWorkflowReadiness(workflow)
  const copy = getWorkflowCopy(workflow)
  const Icon = FLOW_ICONS[workflow.kind]
  const ready = readiness.status === 'ready'

  return (
    <article className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
      <button
        type="button"
        onClick={() => onUpdate({ expanded: !workflow.expanded })}
        aria-expanded={workflow.expanded}
        className="flex min-h-[72px] w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-brand-bg/60 sm:px-4"
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-accent/10 text-brand-accent-2">
          <Icon size={18} aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">LawHand action</span>
          <span className="mt-0.5 block truncate text-sm font-semibold text-brand-ink">{copy.title}</span>
          <span className="mt-0.5 block truncate text-xs text-brand-muted">{copy.collapsedTarget}</span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1.5">
          <StatusChip readiness={readiness} />
          <ChevronDown size={15} className={`text-brand-muted transition-transform ${workflow.expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
        </span>
      </button>

      {workflow.expanded && (
        <div className="border-t border-brand-line px-3.5 py-4 sm:px-4">
          {readiness.status === 'completed' ? (
            <CompletedReceipt workflow={workflow} />
          ) : (
            <>
              {workflow.kind === 'intake' && <IntakeDetails workflow={workflow} onUpdate={onUpdate} />}
              {workflow.kind === 'time' && <TimeDetails workflow={workflow} onUpdate={onUpdate} />}
              {workflow.kind === 'documents' && <DocumentDetails workflow={workflow} onUpdate={onUpdate} />}

              <div className="mt-4 border-t border-brand-line pt-4">
                {readiness.missing.length > 0 && (
                  <p className="mb-2 text-xs text-brand-muted">
                    Resolve {readiness.missing.join(' and ')} to continue.
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => onReview(workflow.id)}
                  disabled={!ready}
                  className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 text-sm font-semibold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-line-2 disabled:text-brand-muted sm:w-auto"
                >
                  <ClipboardCheck size={17} aria-hidden="true" />
                  {workflow.kind === 'intake' ? 'Review 2 changes' : workflow.kind === 'time' ? `Review ${workflow.hours.toFixed(2)} hours` : 'Review 3 drafts'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </article>
  )
}

function ReviewSection({ eyebrow, title, children }) {
  return (
    <section className="rounded-2xl border border-brand-line bg-brand-bg/60 p-4">
      <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">{eyebrow}</p>
      <h3 className="mt-1 text-base font-semibold text-brand-ink">{title}</h3>
      <div className="mt-3 text-sm text-brand-ink-2">{children}</div>
    </section>
  )
}

function ReviewDetails({ workflow }) {
  if (workflow.kind === 'intake') {
    const dueLabels = { none: 'No due date', tomorrow: 'Tomorrow · Aug 5, 2026', friday: 'Friday · Aug 7, 2026' }
    const assigneeLabels = { me: 'Alex Morgan (you)', intake: 'Intake team', unassigned: 'Unassigned' }
    return (
      <div className="space-y-3">
        <ReviewSection eyebrow="Contact" title="Jane Doe">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div><dt className="text-brand-muted">Phone</dt><dd className="mt-0.5 font-semibold text-brand-ink">(701) 123-2255</dd></div>
            <div><dt className="text-brand-muted">Status</dt><dd className="mt-0.5 font-semibold text-brand-ink">{workflow.contactType === 'prospect' ? 'Prospective client' : 'Client'}</dd></div>
          </dl>
        </ReviewSection>
        <ReviewSection eyebrow="Task" title="Schedule consultation with Jane Doe">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div><dt className="text-brand-muted">Owner</dt><dd className="mt-0.5 font-semibold text-brand-ink">{assigneeLabels[workflow.assignee]}</dd></div>
            <div><dt className="text-brand-muted">Due</dt><dd className="mt-0.5 font-semibold text-brand-ink">{dueLabels[workflow.due]}</dd></div>
            <div><dt className="text-brand-muted">Priority</dt><dd className="mt-0.5 font-semibold text-brand-ink">Medium</dd></div>
            <div><dt className="text-brand-muted">Link</dt><dd className="mt-0.5 font-semibold text-brand-ink">Jane Doe · No matter</dd></div>
          </dl>
        </ReviewSection>
        <GuardrailNote>No calendar event, invitation, call, text, or email will be created or sent.</GuardrailNote>
      </div>
    )
  }

  if (workflow.kind === 'time') {
    const matter = getMatter(workflow.matterId)
    const amount = workflow.billable ? workflow.hours * matter.rate : 0
    return (
      <div className="space-y-3">
        <ReviewSection eyebrow="Matter" title={matter.name}>
          <p className="text-xs">{matter.caseNumber} · {matter.client}</p>
        </ReviewSection>
        <ReviewSection eyebrow="Time entry" title={workflow.description}>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
            <div><dt className="text-brand-muted">Timekeeper</dt><dd className="mt-0.5 font-semibold text-brand-ink">Alex Morgan (you)</dd></div>
            <div><dt className="text-brand-muted">Date</dt><dd className="mt-0.5 font-semibold text-brand-ink">Aug 4, 2026</dd></div>
            <div><dt className="text-brand-muted">Time</dt><dd className="mt-0.5 font-semibold text-brand-ink">{workflow.hours.toFixed(2)} hours</dd></div>
            <div><dt className="text-brand-muted">Billing</dt><dd className="mt-0.5 font-semibold text-brand-ink">{workflow.billable ? 'Billable' : 'Non-billable'}</dd></div>
            <div><dt className="text-brand-muted">Rate</dt><dd className="mt-0.5 font-semibold text-brand-ink">${matter.rate}/hour</dd></div>
            <div><dt className="text-brand-muted">Draft value</dt><dd className="mt-0.5 font-semibold text-brand-ink">${amount.toLocaleString('en-US', { minimumFractionDigits: 2 })}</dd></div>
          </dl>
        </ReviewSection>
        <GuardrailNote>This creates a draft, unbilled time entry. It does not create or send an invoice.</GuardrailNote>
      </div>
    )
  }

  const matter = getMatter(workflow.matterId)
  return (
    <div className="space-y-3">
      <ReviewSection eyebrow="Matter" title={matter.name}>
        <p className="text-xs">{matter.caseNumber} · {matter.client}</p>
      </ReviewSection>
      <ReviewSection eyebrow="Draft packet" title="3 independently reviewable drafts">
        <ol className="space-y-2 text-xs">
          <li className="flex items-center gap-2"><FileText size={14} className="text-brand-accent-2" /> {workflow.pleadingType} · active firm pleading template</li>
          <li className="flex items-center gap-2"><FileText size={14} className="text-brand-accent-2" /> {workflow.feeTemplate}</li>
          <li className="flex items-center gap-2"><FileText size={14} className="text-brand-accent-2" /> {workflow.contractType} · active firm template</li>
        </ol>
      </ReviewSection>
      <GuardrailNote>Private drafts only. Nothing will be filed, finalized, saved to the matter, published, sent, or signed.</GuardrailNote>
    </div>
  )
}

function ReviewDialog({ workflow, confirming, onClose, onConfirm }) {
  const copy = getWorkflowCopy(workflow)
  const closeRef = useRef(null)

  useEffect(() => {
    const previousActive = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !confirming) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      previousActive?.focus?.()
    }
  }, [confirming, onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-brand-ink/35 sm:items-center sm:p-4" role="presentation">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-title"
        aria-describedby="review-description"
        className="flex max-h-[94dvh] w-full flex-col overflow-hidden rounded-t-3xl border border-brand-line bg-brand-surface shadow-2xl sm:max-w-xl sm:rounded-3xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-brand-line px-4 py-4 sm:px-5">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-accent-2">Exact review</p>
            <h2 id="review-title" className="mt-1 text-xl font-semibold text-brand-ink">{copy.reviewTitle}</h2>
            <p id="review-description" className="mt-1 text-xs leading-relaxed text-brand-muted">{copy.reviewIntro}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            disabled={confirming}
            className="tap-target -mr-2 rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink disabled:opacity-40"
            aria-label="Close review"
          >
            <X size={20} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          <ReviewDetails workflow={workflow} />
          <details className="mt-4 rounded-xl border border-brand-line bg-brand-surface">
            <summary className="cursor-pointer px-3 py-2.5 text-xs font-semibold text-brand-muted">Source command</summary>
            <p className="border-t border-brand-line px-3 py-2.5 text-xs leading-relaxed text-brand-ink">“{workflow.sourcePrompt}”</p>
          </details>
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-brand-bg-soft px-3 py-2.5 text-[11px] leading-relaxed text-brand-muted">
            <ShieldCheck size={15} className="mt-0.5 shrink-0 text-brand-accent-2" aria-hidden="true" />
            <span>This is an interactive prototype. The confirmation creates a mock receipt only; no backend request is made.</span>
          </div>
        </div>

        <footer
          className="shrink-0 border-t border-brand-line bg-brand-surface px-4 pt-3 sm:px-5"
          style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom, 0.75rem))' }}
        >
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={onClose}
              disabled={confirming}
              className="inline-flex min-h-11 items-center justify-center rounded-xl border border-brand-line bg-brand-surface px-4 text-sm font-semibold text-brand-ink hover:bg-brand-bg-soft disabled:opacity-40"
            >
              Back to edit
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={confirming}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-brand-ink px-5 text-sm font-semibold text-white hover:bg-brand-ink-2 disabled:cursor-wait disabled:opacity-80"
            >
              {confirming ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : <Check size={17} aria-hidden="true" />}
              {confirming ? copy.runningLabel : copy.actionLabel}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

function ActivityPanel({ activities, workflowCount, onClose }) {
  return (
    <div className="flex h-full flex-col bg-brand-surface">
      <header className="flex min-h-16 shrink-0 items-center justify-between border-b border-brand-line px-4">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Working session</p>
          <h2 className="mt-0.5 text-base font-semibold text-brand-ink">Activity</h2>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft" aria-label="Close activity">
            <X size={19} />
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-xl border border-brand-line bg-brand-bg/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Prepared</p>
            <p className="mt-1 text-xl font-semibold text-brand-ink">{workflowCount}</p>
          </div>
          <div className="rounded-xl border border-brand-line bg-brand-bg/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Receipts</p>
            <p className="mt-1 text-xl font-semibold text-brand-ink">{activities.length}</p>
          </div>
        </div>

        <div className="mt-5">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-muted">Mock receipts</p>
          {activities.length === 0 ? (
            <div className="mt-2 rounded-2xl border border-dashed border-brand-line-2 p-4 text-center">
              <ReceiptText size={22} className="mx-auto text-brand-line-2" aria-hidden="true" />
              <p className="mt-2 text-xs leading-relaxed text-brand-muted">Reviewed actions will appear here with a truthful result.</p>
            </div>
          ) : (
            <ol className="mt-2 space-y-3">
              {activities.map((activity) => (
                <li key={activity.id} className="relative rounded-2xl border border-brand-line bg-brand-surface p-3 shadow-sm">
                  <div className="flex items-start gap-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-green/10 text-brand-green">
                      <Check size={15} aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-brand-ink">{activity.title}</p>
                      <p className="mt-1 text-[11px] leading-relaxed text-brand-muted">{activity.detail}</p>
                      <p className="mt-2 text-[10px] font-bold uppercase tracking-wide text-brand-green">Completed · mock</p>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="mt-5 rounded-2xl border border-brand-accent/20 bg-brand-accent/5 p-4">
          <div className="flex items-center gap-2 text-brand-accent-2">
            <ShieldCheck size={16} aria-hidden="true" />
            <p className="text-xs font-semibold">Safety pattern</p>
          </div>
          <ul className="mt-3 space-y-2 text-[11px] leading-relaxed text-brand-muted">
            <li>Chat prepares; a dedicated button creates.</li>
            <li>Missing matter, billing, and document scope block review.</li>
            <li>Each mutation returns its own persistent receipt.</li>
          </ul>
        </div>
      </div>
    </div>
  )
}

function Composer({ input, onInput, onSend, onChoosePrompt, isWorking, recording, recordingSeconds, onStartRecording, onStopRecording, onCancelRecording }) {
  const textareaRef = useRef(null)
  const submit = () => {
    if (!input.trim() || isWorking || recording) return
    onSend(input)
  }

  return (
    <div className="shrink-0 border-t border-brand-line bg-brand-surface/95 px-3 pt-2.5 backdrop-blur sm:px-5">
      <div className="mx-auto max-w-3xl">
        <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-2" aria-label="Try a sample command">
          {DEMO_PROMPTS.map((prompt) => <PromptCard key={prompt.id} prompt={prompt} onChoose={onChoosePrompt} compact />)}
        </div>

        {recording && (
          <div className="mb-2 flex items-center gap-3 rounded-xl border border-brand-rose/25 bg-brand-rose/10 px-3 py-2" role="status">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-brand-rose" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-brand-ink">Demo listening · 0:{String(recordingSeconds).padStart(2, '0')}</p>
              <p className="truncate text-[10px] text-brand-muted">Stop to insert a sample transcript. It will not send automatically.</p>
            </div>
            <button type="button" onClick={onCancelRecording} className="min-h-9 rounded-lg px-2 text-xs font-semibold text-brand-muted hover:bg-brand-surface">Cancel</button>
            <button type="button" onClick={onStopRecording} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-brand-ink px-3 text-xs font-semibold text-white">
              <Square size={11} fill="currentColor" /> Stop
            </button>
          </div>
        )}

        <div className="rounded-2xl border border-brand-line-2 bg-brand-surface p-1.5 shadow-sm focus-within:border-brand-accent focus-within:ring-2 focus-within:ring-brand-accent/15">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(event) => onInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder="Tell LawHand what to create, log, or prepare…"
            aria-label="Message the assistant prototype"
            className="min-h-[48px] max-h-32 w-full resize-none bg-transparent px-2 py-2 text-base leading-relaxed text-brand-ink placeholder:text-brand-muted focus:outline-none sm:text-[15px]"
            rows={1}
            disabled={isWorking || recording}
          />
          <div className="flex items-center justify-between gap-2 border-t border-brand-line/70 px-1 pt-1.5">
            <button
              type="button"
              onClick={onStartRecording}
              disabled={isWorking || recording}
              className="inline-flex min-h-10 items-center gap-2 rounded-xl px-2.5 text-xs font-semibold text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink disabled:opacity-40"
              aria-label="Start demo dictation"
            >
              <Mic size={17} aria-hidden="true" />
              <span className="hidden sm:inline">Dictate</span>
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={!input.trim() || isWorking || recording}
              className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-brand-ink px-3.5 text-sm font-semibold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:bg-brand-line-2 disabled:text-brand-muted"
            >
              {isWorking ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <Send size={16} aria-hidden="true" />}
              <span className="hidden sm:inline">{isWorking ? 'Understanding' : 'Send'}</span>
            </button>
          </div>
        </div>
        <p className="py-1.5 text-center text-[10px] leading-relaxed text-brand-muted">
          Review names, dates, matters, and amounts. Sending a message never creates a record.
        </p>
      </div>
    </div>
  )
}

export default function VirtualAssistantMockup() {
  const [timeline, setTimeline] = useState([])
  const [input, setInput] = useState('')
  const [isWorking, setIsWorking] = useState(false)
  const [reviewId, setReviewId] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [activities, setActivities] = useState([])
  const [mobileActivityOpen, setMobileActivityOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const [recording, setRecording] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const [dictationIndex, setDictationIndex] = useState(0)
  const sequenceRef = useRef(1)
  const timersRef = useRef([])
  const noticeTimerRef = useRef(null)
  const endRef = useRef(null)

  const workflows = useMemo(
    () => timeline.filter((item) => item.type === 'workflow').map((item) => item.workflow),
    [timeline],
  )
  const reviewWorkflow = workflows.find((workflow) => workflow.id === reviewId) || null
  const modalOpen = Boolean(reviewWorkflow || mobileActivityOpen)

  const addTimer = (callback, delay) => {
    const timer = window.setTimeout(callback, delay)
    timersRef.current.push(timer)
    return timer
  }

  const showNotice = (message) => {
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
    setNotice(message)
    noticeTimerRef.current = window.setTimeout(() => setNotice(''), 2800)
  }

  useEffect(() => () => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer))
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [isWorking, timeline])

  useEffect(() => {
    if (!recording) return undefined
    const interval = window.setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000)
    return () => window.clearInterval(interval)
  }, [recording])

  const updateWorkflow = (workflowId, patch) => {
    setTimeline((current) => current.map((item) => (
      item.type === 'workflow' && item.workflow.id === workflowId
        ? { ...item, workflow: { ...item.workflow, ...patch } }
        : item
    )))
  }

  const sendPrompt = (rawPrompt) => {
    const prompt = String(rawPrompt || '').trim()
    if (!prompt || isWorking || recording) return
    const messageId = `message-${sequenceRef.current++}`
    setTimeline((current) => [...current, { id: messageId, type: 'message', role: 'user', text: prompt }])
    setInput('')
    setIsWorking(true)

    addTimer(() => {
      const scenario = detectScenario(prompt)
      const responseId = `message-${sequenceRef.current++}`
      if (!scenario) {
        setTimeline((current) => [...current, {
          id: responseId,
          type: 'message',
          role: 'assistant',
          text: 'This prototype recognizes client-and-task, time-entry, and document-packet commands. Try one of the sample prompts below.',
        }])
        setIsWorking(false)
        return
      }

      const workflowId = `workflow-${sequenceRef.current++}`
      setTimeline((current) => [
        ...current,
        { id: responseId, type: 'message', role: 'assistant', text: ASSISTANT_RESPONSES[scenario] },
        { id: workflowId, type: 'workflow', workflow: createWorkflow(scenario, workflowId, prompt) },
      ])
      setIsWorking(false)
    }, 360)
  }

  const confirmWorkflow = (workflow) => {
    if (confirming) return
    setConfirming(true)
    updateWorkflow(workflow.id, { runtimeStatus: 'running' })
    const copy = getWorkflowCopy(workflow)

    addTimer(() => {
      updateWorkflow(workflow.id, { runtimeStatus: 'completed', expanded: true })
      setActivities((current) => [{
        id: `receipt-${sequenceRef.current++}`,
        workflowId: workflow.id,
        title: copy.receiptTitle,
        detail: copy.receiptDetail,
      }, ...current])
      setTimeline((current) => [...current, {
        id: `message-${sequenceRef.current++}`,
        type: 'message',
        role: 'assistant',
        text: 'Done—the mock receipt is attached to the action card. No real records were written.',
      }])
      setConfirming(false)
      setReviewId(null)
      showNotice('Mock receipt added to this session')
    }, 720)
  }

  const resetDemo = () => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer))
    timersRef.current = []
    setTimeline([])
    setInput('')
    setIsWorking(false)
    setReviewId(null)
    setConfirming(false)
    setActivities([])
    setMobileActivityOpen(false)
    setRecording(false)
    setRecordingSeconds(0)
    showNotice('Prototype reset')
  }

  const startRecording = () => {
    setRecordingSeconds(0)
    setRecording(true)
  }

  const stopRecording = () => {
    const prompt = DEMO_PROMPTS[dictationIndex % DEMO_PROMPTS.length]
    setInput(prompt.prompt)
    setDictationIndex((index) => index + 1)
    setRecording(false)
    setRecordingSeconds(0)
    showNotice('Demo transcript inserted—review it before sending')
  }

  const cancelRecording = () => {
    setRecording(false)
    setRecordingSeconds(0)
  }

  return (
    <div className="h-screen [height:100dvh] overflow-hidden bg-brand-bg text-brand-ink">
      <div
        className="flex h-full"
        {...(modalOpen ? { inert: '', 'aria-hidden': true } : {})}
      >
        <DesktopSidebar
          onReset={resetDemo}
          onPlaceholderNavigation={(label) => showNotice(`${label} navigation is disabled in this prototype`)}
        />

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex min-h-14 shrink-0 items-center justify-between gap-3 border-b border-brand-line bg-brand-surface px-3 sm:min-h-16 sm:px-5">
            <div className="flex min-w-0 items-center gap-3">
              <LawHandLogo markOnly compact className="lg:hidden" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate text-base font-semibold text-brand-ink sm:text-lg">Assistant</h1>
                  <span className="hidden rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.12em] text-brand-accent-2 sm:inline-flex">Interactive prototype</span>
                </div>
                <p className="truncate text-[10px] text-brand-muted sm:text-xs">Natural-language work, reviewed before anything changes</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
              <div className="hidden items-center gap-1.5 rounded-full border border-brand-accent/20 bg-brand-accent/10 px-2.5 py-1 text-[10px] font-semibold text-brand-accent-2 sm:flex">
                <ShieldCheck size={13} aria-hidden="true" /> Review required
              </div>
              <button
                type="button"
                onClick={() => setMobileActivityOpen(true)}
                className="tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink xl:hidden"
                aria-label={`Open activity, ${activities.length} receipts`}
              >
                <ReceiptText size={19} />
              </button>
              <button
                type="button"
                onClick={resetDemo}
                className="hidden min-h-10 items-center gap-2 rounded-xl border border-brand-line px-3 text-xs font-semibold text-brand-ink hover:bg-brand-bg-soft sm:inline-flex lg:hidden"
              >
                <RefreshCcw size={14} /> Reset
              </button>
            </div>
          </header>

          <div className="flex min-h-0 flex-1">
            <main id="conversation" className="flex min-w-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5 sm:py-6">
                <div className="mx-auto max-w-3xl">
                  <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-brand-line bg-brand-surface/90 px-3 py-2 shadow-sm">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-muted"><Briefcase size={15} /></span>
                      <div className="min-w-0">
                        <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-brand-muted">Working context</p>
                        <p className="truncate text-xs font-semibold text-brand-ink">No matter linked · resolve per action</p>
                      </div>
                    </div>
                    <span className="hidden text-[10px] text-brand-muted sm:inline">Demo data only</span>
                  </div>

                  {timeline.length === 0 && (
                    <section className="py-5 text-center sm:py-10">
                      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-ink text-white shadow-sm">
                        <Sparkles size={21} aria-hidden="true" />
                      </span>
                      <h2 className="mt-4 text-2xl font-semibold text-brand-ink sm:text-3xl">Tell LawHand what to handle</h2>
                      <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-brand-muted">
                        Type it the way you would say it. LawHand extracts the work, asks only for consequential details, and shows an exact review before creating anything.
                      </p>
                      <div className="mt-6 grid gap-3 text-left sm:grid-cols-3">
                        {DEMO_PROMPTS.map((prompt) => <PromptCard key={prompt.id} prompt={prompt} onChoose={sendPrompt} />)}
                      </div>
                    </section>
                  )}

                  <div className="space-y-4" aria-live="polite">
                    {timeline.map((item) => (
                      item.type === 'message' ? (
                        <MessageBubble key={item.id} item={item} />
                      ) : (
                        <WorkflowCard
                          key={item.id}
                          workflow={item.workflow}
                          onUpdate={(patch) => updateWorkflow(item.workflow.id, patch)}
                          onReview={(workflowId) => {
                            setMobileActivityOpen(false)
                            setReviewId(workflowId)
                          }}
                        />
                      )
                    ))}
                    {isWorking && (
                      <div className="flex items-start gap-2.5" role="status">
                        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-brand-ink text-white"><Sparkles size={15} /></span>
                        <div className="flex min-h-10 items-center gap-1 rounded-2xl rounded-tl-md border border-brand-line bg-brand-surface px-4 shadow-sm" aria-label="Understanding request">
                          {[0, 1, 2].map((dot) => <span key={dot} className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-muted" style={{ animationDelay: `${dot * 120}ms` }} />)}
                        </div>
                      </div>
                    )}
                    <div ref={endRef} />
                  </div>
                </div>
              </div>

              <Composer
                input={input}
                onInput={setInput}
                onSend={sendPrompt}
                onChoosePrompt={sendPrompt}
                isWorking={isWorking}
                recording={recording}
                recordingSeconds={recordingSeconds}
                onStartRecording={startRecording}
                onStopRecording={stopRecording}
                onCancelRecording={cancelRecording}
              />
            </main>

            <aside aria-label="Session activity" className="hidden w-[320px] shrink-0 border-l border-brand-line xl:block">
              <ActivityPanel activities={activities} workflowCount={workflows.length} />
            </aside>
          </div>

          <MobileBottomNav onPlaceholderNavigation={(label) => showNotice(`${label} navigation is disabled in this prototype`)} />
        </div>
      </div>

      {mobileActivityOpen && (
        <div className="fixed inset-0 z-40 flex justify-end bg-brand-ink/30 xl:hidden" role="dialog" aria-modal="true" aria-label="Session activity">
          <button type="button" className="absolute inset-0" onClick={() => setMobileActivityOpen(false)} aria-label="Close activity overlay" />
          <div className="relative h-full w-[min(88vw,360px)] border-l border-brand-line shadow-2xl">
            <ActivityPanel activities={activities} workflowCount={workflows.length} onClose={() => setMobileActivityOpen(false)} />
          </div>
        </div>
      )}

      {reviewWorkflow && (
        <ReviewDialog
          workflow={reviewWorkflow}
          confirming={confirming}
          onClose={() => confirming ? null : setReviewId(null)}
          onConfirm={() => confirmWorkflow(reviewWorkflow)}
        />
      )}

      {notice && (
        <div className="pointer-events-none fixed left-1/2 top-3 z-[70] w-[min(92vw,420px)] -translate-x-1/2 rounded-xl border border-brand-line bg-brand-ink px-4 py-2.5 text-center text-xs font-semibold text-white shadow-xl" role="status">
          {notice}
        </div>
      )}
    </div>
  )
}
