import React, { useState } from 'react'
import { emailMatterClient } from '../api'

const inputCls = 'w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
const labelCls = 'block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5'

export default function ComposeEmailModal({ matterId, matterName, caseNumber, clientEmail, onSent, onClose }) {
  const defaultSubject = `Re: ${matterName}${caseNumber ? ` — ${caseNumber}` : ''}`
  const [form, setForm] = useState({
    to_email: clientEmail || '',
    subject: defaultSubject,
    body: '',
  })
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const handleSend = async (e) => {
    e.preventDefault()
    if (!form.subject.trim() || !form.body.trim()) return
    setSending(true)
    setError(null)
    try {
      const result = await emailMatterClient(matterId, {
        to_email: form.to_email || undefined,
        subject: form.subject.trim(),
        body: form.body.trim(),
      })
      if (result?.sent !== true) {
        setError(
          result?.delivery_error
          || 'The email was not sent. The outbound attempt was recorded on the matter.'
        )
        return
      }
      onSent(result)
    } catch (err) {
      const msg = err?.response?.data?.detail || 'Failed to send email.'
      setError(msg)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-brand-surface rounded-2xl shadow-2xl border border-brand-line w-full max-w-lg" role="dialog" aria-modal="true" aria-labelledby="compose-email-title">
        <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between">
          <h2 id="compose-email-title" className="font-serif font-bold text-xl text-brand-ink">Email Client</h2>
          <button type="button" onClick={onClose} aria-label="Close email composer" className="text-brand-muted hover:text-brand-ink transition-colors p-1 rounded">
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <form onSubmit={handleSend} className="p-6 space-y-4">
          <div>
            <label htmlFor="compose-email-to" className={labelCls}>To</label>
            <input
              id="compose-email-to"
              type="email"
              value={form.to_email}
              onChange={e => set('to_email', e.target.value)}
              placeholder="client@example.com"
              className={inputCls}
            />
            {!clientEmail && (
              <p className="text-[12px] text-brand-muted font-sans mt-1">No client email on file — enter manually or save one on the contact.</p>
            )}
          </div>
          <div>
            <label htmlFor="compose-email-subject" className={labelCls}>Subject</label>
            <input
              id="compose-email-subject"
              type="text"
              value={form.subject}
              onChange={e => set('subject', e.target.value)}
              className={inputCls}
              required
            />
          </div>
          <div>
            <label htmlFor="compose-email-body" className={labelCls}>Message</label>
            <textarea
              id="compose-email-body"
              autoFocus
              value={form.body}
              onChange={e => set('body', e.target.value)}
              rows={8}
              placeholder="Write your message..."
              className={`${inputCls} resize-y`}
              required
            />
          </div>

          {error && <p className="text-brand-rose text-sm font-sans">{error}</p>}

          <div className="flex gap-3 justify-end pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink transition-colors">
              Cancel
            </button>
            <button
              type="submit"
              disabled={sending || !form.subject.trim() || !form.body.trim()}
              className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all flex items-center gap-2"
            >
              {sending ? (
                <>
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Sending…
                </>
              ) : (
                <>
                  <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" /></svg>
                  Send
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
