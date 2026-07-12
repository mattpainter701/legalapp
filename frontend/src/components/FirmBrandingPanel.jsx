import React, { useState, useEffect } from 'react'
import { getFirmBranding, updateFirmBranding } from '../api'

export default function FirmBrandingPanel() {
  const [form, setForm] = useState({
    firm_name: '',
    firm_logo_url: '',
    firm_address: '',
    firm_phone: '',
    firm_email: '',
    firm_website: '',
    firm_pdf_footer: '',
  })
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getFirmBranding()
      .then((data) => {
        setForm({
          firm_name: data.firm_name || '',
          firm_logo_url: data.firm_logo_url || '',
          firm_address: data.firm_address || '',
          firm_phone: data.firm_phone || '',
          firm_email: data.firm_email || '',
          firm_website: data.firm_website || '',
          firm_pdf_footer: data.firm_pdf_footer || '',
        })
      })
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load firm branding.'))
      .finally(() => setLoaded(true))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const body = Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, v.trim() === '' ? null : v.trim()])
      )
      const updated = await updateFirmBranding(body)
      setForm({
        firm_name: updated.firm_name || '',
        firm_logo_url: updated.firm_logo_url || '',
        firm_address: updated.firm_address || '',
        firm_phone: updated.firm_phone || '',
        firm_email: updated.firm_email || '',
        firm_website: updated.firm_website || '',
        firm_pdf_footer: updated.firm_pdf_footer || '',
      })
      setMsg({ type: 'success', text: 'Firm branding saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err?.response?.data?.detail || 'Failed to save firm branding.' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-brand-line bg-brand-bg-soft/50">
        <h3 className="font-serif font-bold text-xl text-brand-ink">Firm Branding</h3>
        <p className="text-sm text-brand-ink-2 font-sans mt-1">
          Customize how your firm appears on invoices, statements, and other generated documents.
        </p>
      </div>
      <div className="px-8 py-5 space-y-5">
        {error && (
          <div className="px-4 py-2.5 rounded-lg text-sm font-sans bg-red-50 text-red-700 border border-red-200">
            {error}
          </div>
        )}
        {msg && (
          <div className={`px-4 py-2.5 rounded-lg text-sm font-sans ${msg.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
            {msg.text}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label htmlFor="firmbrandingpanel-firm-name" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Firm name
            </label>
            <input id="firmbrandingpanel-firm-name"
              type="text"
              value={form.firm_name}
              onChange={(e) => setForm((f) => ({ ...f, firm_name: e.target.value }))}
              placeholder="Acme Legal Group"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div>
            <label htmlFor="firmbrandingpanel-logo-url" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Logo URL
            </label>
            <input id="firmbrandingpanel-logo-url"
              type="text"
              value={form.firm_logo_url}
              onChange={(e) => setForm((f) => ({ ...f, firm_logo_url: e.target.value }))}
              placeholder="https://example.com/logo.png"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div>
            <label htmlFor="firmbrandingpanel-phone" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Phone
            </label>
            <input id="firmbrandingpanel-phone"
              type="text"
              value={form.firm_phone}
              onChange={(e) => setForm((f) => ({ ...f, firm_phone: e.target.value }))}
              placeholder="(555) 123-4567"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div>
            <label htmlFor="firmbrandingpanel-email" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Email
            </label>
            <input id="firmbrandingpanel-email"
              type="email"
              value={form.firm_email}
              onChange={(e) => setForm((f) => ({ ...f, firm_email: e.target.value }))}
              placeholder="contact@acmelegal.com"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="firmbrandingpanel-website" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Website
            </label>
            <input id="firmbrandingpanel-website"
              type="text"
              value={form.firm_website}
              onChange={(e) => setForm((f) => ({ ...f, firm_website: e.target.value }))}
              placeholder="https://www.acmelegal.com"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="firmbrandingpanel-address" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              Address
            </label>
            <textarea id="firmbrandingpanel-address"
              value={form.firm_address}
              onChange={(e) => setForm((f) => ({ ...f, firm_address: e.target.value }))}
              rows={2}
              placeholder="123 Main St, Suite 400, Springfield, IL 62701"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="firmbrandingpanel-pdf-footer-text" className="block text-sm font-sans font-semibold text-brand-ink mb-1.5">
              PDF footer text
            </label>
            <textarea id="firmbrandingpanel-pdf-footer-text"
              value={form.firm_pdf_footer}
              onChange={(e) => setForm((f) => ({ ...f, firm_pdf_footer: e.target.value }))}
              rows={2}
              placeholder="Confidential — Attorney/Client Privileged Communication"
              className="w-full px-3 py-2.5 border border-brand-line rounded-lg text-sm font-sans bg-white focus:outline-none focus:ring-2 focus:ring-brand-ink/20 placeholder:text-brand-muted"
            />
            <p className="text-xs text-brand-muted font-sans mt-1.5">
              Shown at the bottom of generated PDFs (invoices, statements, reports).
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={handleSave}
            disabled={saving || !loaded}
            className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
