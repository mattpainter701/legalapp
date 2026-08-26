import { useEffect, useMemo, useState } from 'react'
import { Check, FileText, Loader2, ShieldCheck, Sparkles } from 'lucide-react'
import { getTemplates } from '../../api'

const newIdempotencyKey = () => globalThis.crypto?.randomUUID?.()
  || `engagement-${Date.now()}-${Math.random().toString(36).slice(2)}`

const lines = value => String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean)

const initialForm = (lead, packet) => {
  const fields = packet?.fields || packet || {}
  const client = fields.client || {}
  const attorney = fields.attorney || {}
  const signer = fields.signers?.[0] || {}
  return {
    idempotency_key: fields.idempotency_key || newIdempotencyKey(),
    template_id: packet?.template_id || fields.template_id || '',
    fee_structure: fields.fee_structure || 'Flat fee',
    fee_amount: fields.fee_amount ?? '',
    scope: Array.isArray(fields.scope_bullets) ? fields.scope_bullets.join('\n') : (fields.scope || ''),
    exclusions: Array.isArray(fields.exclusions) ? fields.exclusions.join('\n') : (fields.exclusions || ''),
    attorney_name: attorney.name || fields.attorney_name || lead?.assigned_to_name || '',
    client_name: client.name || fields.client_name || lead?.contact?.display_name || '',
    client_email: client.email || fields.client_email || lead?.contact?.email || '',
    signer_name: signer.name || fields.signer_name || client.name || lead?.contact?.display_name || '',
    signer_email: signer.email || fields.signer_email || client.email || lead?.contact?.email || '',
  }
}

export default function FeeAgreementPacket({ lead, initialValue, onSave, onApprove, saving = false }) {
  const [form, setForm] = useState(() => initialForm(lead, initialValue))
  const [templates, setTemplates] = useState([])
  const [templateError, setTemplateError] = useState('')
  const approved = initialValue?.status === 'approved'
  const preview = initialValue?.preview || initialValue?.prepared_content?.rendered
  const unresolved = initialValue?.unresolved_fields || []

  useEffect(() => {
    if (initialValue?.id) setForm(initialForm(lead, initialValue))
  }, [initialValue?.id, initialValue?.version, lead])

  useEffect(() => {
    let cancelled = false
    getTemplates({ include_inactive: false })
      .then((result) => {
        if (cancelled) return
        const approvedTemplates = (result?.items || []).filter(template => (
          ['engagement_letter', 'retainer'].includes(template.category)
          && (['approved', 'active'].includes(template.status) || Boolean(template.approved_at))
        ))
        setTemplates(approvedTemplates)
        if (approvedTemplates.length === 1) {
          setForm(current => current.template_id
            ? current
            : { ...current, template_id: String(approvedTemplates[0].id) })
        }
        setTemplateError(approvedTemplates.length ? '' : 'No active approved engagement or retainer template is available.')
      })
      .catch(() => {
        if (!cancelled) setTemplateError('Firm templates could not be loaded.')
      })
    return () => { cancelled = true }
  }, [])

  const selectedTemplateAvailable = useMemo(() => (
    !form.template_id || templates.some(template => String(template.id) === String(form.template_id))
  ), [form.template_id, templates])

  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const submit = async (event) => {
    event.preventDefault()
    await onSave?.({
      idempotency_key: form.idempotency_key,
      template_id: form.template_id,
      fee_structure: form.fee_structure,
      fee_amount: form.fee_amount === '' ? null : form.fee_amount,
      scope_bullets: lines(form.scope),
      exclusions: lines(form.exclusions),
      client: { name: form.client_name.trim(), email: form.client_email.trim() },
      attorney: { name: form.attorney_name.trim() },
      signers: [{
        name: (form.signer_name || form.client_name).trim(),
        email: (form.signer_email || form.client_email).trim(),
        role: 'client',
      }],
    })
  }

  return (
    <section aria-labelledby="fee-agreement-heading" className="rounded-xl border border-brand-line bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <span className="rounded-lg bg-brand-accent/10 p-2 text-brand-accent"><FileText size={17} aria-hidden="true" /></span>
          <div>
            <h3 id="fee-agreement-heading" className="text-sm font-bold text-brand-ink">Fee Agreement Packet</h3>
            <p className="mt-0.5 text-xs text-brand-muted">Confirm the firm decisions, render the approved template, then review the artifact.</p>
          </div>
        </div>
        <span className="rounded-full bg-brand-bg-soft px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-brand-muted">No send</span>
      </div>
      <form onSubmit={submit} className="mt-4 space-y-3">
        <fieldset disabled={saving || approved} className="grid gap-3 sm:grid-cols-2 disabled:opacity-70">
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Template
            <select required aria-label="Agreement template" value={form.template_id} onChange={event => set('template_id', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm">
              <option value="">Choose an approved firm template</option>
              {!selectedTemplateAvailable && <option value={form.template_id}>Previously selected template</option>}
              {templates.map(template => <option key={template.id} value={template.id}>{template.title}</option>)}
            </select>
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Fee structure
            <select required aria-label="Fee structure" value={form.fee_structure} onChange={event => set('fee_structure', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm">
              {['Flat fee', 'Hourly', 'Contingency', 'Retainer', 'Other approved schedule'].map(value => <option key={value}>{value}</option>)}
            </select>
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted sm:col-span-2">Approved fee / amount
            <input required type="number" min="0" step="0.01" inputMode="decimal" aria-label="Approved fee amount" value={form.fee_amount} onChange={event => set('fee_amount', event.target.value)} placeholder="2500.00" className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted sm:col-span-2">Scope of representation
            <textarea required aria-label="Scope of representation" value={form.scope} onChange={event => set('scope', event.target.value)} rows={3} placeholder="One approved scope item per line" className="mt-1 w-full resize-y rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted sm:col-span-2">Exclusions / boundaries
            <textarea aria-label="Exclusions and boundaries" value={form.exclusions} onChange={event => set('exclusions', event.target.value)} rows={2} placeholder="One explicit exclusion per line" className="mt-1 w-full resize-y rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Client legal name
            <input required aria-label="Client name" value={form.client_name} onChange={event => set('client_name', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Client email
            <input required type="email" aria-label="Client email" value={form.client_email} onChange={event => set('client_email', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted sm:col-span-2">Responsible attorney
            <input required aria-label="Attorney name" value={form.attorney_name} onChange={event => set('attorney_name', event.target.value)} placeholder="Assigned attorney" className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Signer name
            <input required aria-label="Signer name" value={form.signer_name} onChange={event => set('signer_name', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-wider text-brand-muted">Signer email
            <input required type="email" aria-label="Signer email" value={form.signer_email} onChange={event => set('signer_email', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
          </label>
        </fieldset>
        {templateError && <p role="status" className="text-xs text-brand-amber">{templateError}</p>}
        {unresolved.length > 0 && <p role="alert" className="text-xs text-brand-amber">Still required: {unresolved.join(', ')}</p>}
        {preview && <div className="rounded-lg border border-brand-line bg-brand-bg-soft p-3 text-sm whitespace-pre-wrap"><p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-brand-muted">Rendered preview</p>{preview}</div>}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-brand-line pt-3">
          <p className="flex items-center gap-1 text-[11px] text-brand-muted"><Sparkles size={13} aria-hidden="true" /> Fee, scope, people, and approval always stay human-confirmed.</p>
          <div className="flex flex-wrap gap-2">
            {!approved && <button type="submit" disabled={saving || templates.length === 0} className="inline-flex min-h-[40px] items-center gap-2 rounded-lg bg-brand-ink px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
              {saving ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}{saving ? 'Rendering…' : 'Save & render preview'}
            </button>}
            {!approved && preview && unresolved.length === 0 && <button type="button" disabled={saving} onClick={onApprove} className="inline-flex min-h-[40px] items-center gap-2 rounded-lg bg-brand-green px-4 py-2 text-xs font-bold text-white disabled:opacity-50"><ShieldCheck size={14} aria-hidden="true" />Approve reviewed packet</button>}
            {approved && <span className="inline-flex min-h-[40px] items-center gap-2 rounded-lg bg-brand-green/10 px-4 py-2 text-xs font-bold text-brand-green"><ShieldCheck size={14} aria-hidden="true" />Approved artifact</span>}
          </div>
        </div>
      </form>
    </section>
  )
}
