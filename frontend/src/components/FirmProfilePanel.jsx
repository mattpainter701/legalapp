import { useState, useEffect } from 'react'
import { getFirmBranding, updateFirmBranding } from '../api'

// Everything the PUT accepts. `tenant_name` renames the account record itself;
// the rest are per-tenant overrides layered on top of it.
const FIELDS = [
  'tenant_name',
  'firm_name',
  'firm_currency',
  'firm_logo_url',
  'firm_address',
  'firm_phone',
  'firm_email',
  'firm_website',
  'firm_pdf_footer',
]

const EMPTY = Object.fromEntries(FIELDS.map((f) => [f, '']))

const INPUT_CLASS =
  'w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted'

// The GET resolves `firm_name` through the tenant-name fallback, so the raw
// override decides what belongs in the input. Showing the resolved value would
// turn a no-op save into a permanent override.
const toForm = (data) => ({
  ...EMPTY,
  ...Object.fromEntries(FIELDS.map((f) => [f, data[f] || ''])),
  firm_name: data.firm_name_override || '',
})

function Card({ title, description, children }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
        <h3 className="font-serif font-bold text-xl text-brand-ink">{title}</h3>
        <p className="text-sm text-brand-ink-2 font-sans mt-1">{description}</p>
      </div>
      <div className="px-8 py-5">{children}</div>
    </div>
  )
}

function Field({ id, label, hint, wide, children }) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <label htmlFor={id} className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-brand-muted font-sans mt-1.5">{hint}</p>}
    </div>
  )
}

export default function FirmProfilePanel() {
  const [form, setForm] = useState(EMPTY)
  const [resolved, setResolved] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getFirmBranding()
      .then((data) => {
        setForm(toForm(data))
        setResolved(data)
      })
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load firm profile.'))
      .finally(() => setLoaded(true))
  }, [])

  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }))

  const handleSave = async () => {
    if (!form.tenant_name.trim()) {
      setMsg({ type: 'error', text: 'Account name cannot be blank.' })
      setTimeout(() => setMsg(null), 4000)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = Object.fromEntries(
        FIELDS.map((f) => [f, form[f].trim() === '' ? null : form[f].trim()])
      )
      // The account name is the fallback for everything else, so it is the one
      // field the API refuses to clear — never send it as null.
      body.tenant_name = form.tenant_name.trim()
      const updated = await updateFirmBranding(body)
      setForm(toForm(updated))
      setResolved(updated)
      setMsg({ type: 'success', text: 'Firm profile saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed to save firm profile.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  const displayName = form.firm_name.trim() || form.tenant_name.trim() || '—'

  return (
    <div id="firm-profile" className="space-y-6 scroll-mt-6">
      {error && (
        <div className="px-4 py-2.5 rounded-lg text-sm font-sans bg-red-50 text-red-700 border border-red-200">
          {error}
        </div>
      )}
      {msg && (
        <div
          className={`px-4 py-2.5 rounded-lg text-sm font-sans ${msg.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}
        >
          {msg.text}
        </div>
      )}

      <Card
        title="Identity"
        description="How your firm is named across the app — client portal invitations, intake and engagement emails, invoices, statements, and Smart Fill templates."
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field
            id="firmprofile-tenant-name"
            label="Account name"
            hint="The name on your LawHand account. Sign-up created it from your email domain, so it is often wrong — change it here."
          >
            <input
              id="firmprofile-tenant-name"
              type="text"
              value={form.tenant_name}
              onChange={set('tenant_name')}
              placeholder="Painter Law Group"
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            id="firmprofile-firm-name"
            label="Display name"
            hint="Optional. Leave blank to use the account name. Set it when your letterhead name differs from the account name."
          >
            <input
              id="firmprofile-firm-name"
              type="text"
              value={form.firm_name}
              onChange={set('firm_name')}
              placeholder={form.tenant_name || 'Painter Law Group, PLLC'}
              className={INPUT_CLASS}
            />
          </Field>
          <Field
            id="firmprofile-currency"
            label="Currency"
            hint="ISO 4217 code used on invoices, trust statements, and portal payment screens. Defaults to USD."
          >
            <input
              id="firmprofile-currency"
              type="text"
              value={form.firm_currency}
              onChange={set('firm_currency')}
              placeholder="USD"
              maxLength={3}
              className={`${INPUT_CLASS} uppercase`}
            />
          </Field>
          <Field
            id="firmprofile-domain"
            label="Tenant domain"
            hint="Your account's permanent identifier. It cannot be changed and is never shown to clients."
          >
            <input
              id="firmprofile-domain"
              type="text"
              value={resolved?.tenant_domain || ''}
              readOnly
              disabled
              className={`${INPUT_CLASS} bg-brand-bg-soft text-brand-muted cursor-not-allowed`}
            />
          </Field>
          <div className="sm:col-span-2 rounded-lg border border-brand-line bg-brand-bg-soft/60 px-4 py-3">
            <p className="text-xs font-sans text-brand-muted">Clients will see</p>
            <p className="text-sm font-sans font-semibold text-brand-ink mt-0.5">{displayName}</p>
          </div>
        </div>
      </Card>

      <Card
        title="Contact details"
        description="Used on generated documents and shown to clients in the portal. Address falls back to the address captured at sign-up when left blank."
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field id="firmprofile-phone" label="Phone">
            <input
              id="firmprofile-phone"
              type="text"
              value={form.firm_phone}
              onChange={set('firm_phone')}
              placeholder="(555) 123-4567"
              className={INPUT_CLASS}
            />
          </Field>
          <Field id="firmprofile-email" label="Email">
            <input
              id="firmprofile-email"
              type="email"
              value={form.firm_email}
              onChange={set('firm_email')}
              placeholder="contact@painterlaw.com"
              className={INPUT_CLASS}
            />
          </Field>
          <Field id="firmprofile-website" label="Website" wide>
            <input
              id="firmprofile-website"
              type="text"
              value={form.firm_website}
              onChange={set('firm_website')}
              placeholder="https://www.painterlaw.com"
              className={INPUT_CLASS}
            />
          </Field>
          <Field id="firmprofile-address" label="Address" wide>
            <textarea
              id="firmprofile-address"
              value={form.firm_address}
              onChange={set('firm_address')}
              rows={2}
              placeholder="123 Main St, Suite 400, Springfield, IL 62701"
              className={INPUT_CLASS}
            />
          </Field>
        </div>
      </Card>

      <Card
        title="Branding"
        description="Applied to future document runs and portal pages. Documents already generated keep the branding they were created with."
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field
            id="firmprofile-logo-url"
            label="Logo URL"
            hint="A publicly reachable image. Shown in the client portal header and on invoice and statement PDFs."
            wide
          >
            <input
              id="firmprofile-logo-url"
              type="text"
              value={form.firm_logo_url}
              onChange={set('firm_logo_url')}
              placeholder="https://example.com/logo.png"
              className={INPUT_CLASS}
            />
          </Field>
          {form.firm_logo_url.trim() !== '' && (
            <div className="sm:col-span-2">
              <img
                src={form.firm_logo_url}
                alt="Firm logo preview"
                className="max-h-16 w-auto rounded border border-brand-line bg-white p-2"
                onError={(e) => {
                  e.currentTarget.style.display = 'none'
                }}
              />
            </div>
          )}
          <Field
            id="firmprofile-pdf-footer"
            label="PDF footer text"
            hint="Shown at the bottom of generated PDFs (invoices, statements, reports)."
            wide
          >
            <textarea
              id="firmprofile-pdf-footer"
              value={form.firm_pdf_footer}
              onChange={set('firm_pdf_footer')}
              rows={2}
              placeholder="Confidential — Attorney/Client Privileged Communication"
              className={INPUT_CLASS}
            />
          </Field>
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving || !loaded}
          className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : 'Save firm profile'}
        </button>
      </div>
    </div>
  )
}
