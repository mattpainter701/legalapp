import React, { useState, useEffect, useCallback } from 'react'
import {
  getClientPortalMatter,
  listClientPortalMessages,
  sendClientPortalMessage,
  listClientPortalDocuments,
  uploadClientPortalDocument,
  downloadClientPortalDocumentUrl,
  listClientPortalInvoices,
  listClientPortalSignatures,
  signClientPortalSignature,
  declineClientPortalSignature,
} from '../api'
import {
  ShieldCheck, MessageSquare, FileText, Receipt, Send,
  Upload, Download, AlertTriangle, Scale, PenLine, CheckCircle2, LockKeyhole,
} from 'lucide-react'

const TABS = [
  { key: 'overview', label: 'Overview', icon: Scale },
  { key: 'messages', label: 'Messages', icon: MessageSquare },
  { key: 'documents', label: 'Documents', icon: FileText },
  { key: 'signatures', label: 'Signatures', icon: PenLine },
  { key: 'invoices', label: 'Invoices', icon: Receipt },
]

function fmtBytes(n) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function ClientPortalMatterPage() {
  const [matter, setMatter] = useState(null)
  const [tab, setTab] = useState('overview')
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    getClientPortalMatter()
      .then(setMatter)
      .catch((err) => {
        const s = err?.response?.status
        setLoadError(
          s === 401 || s === 403
            ? 'Your portal session has expired. Please open the link from your invitation email again.'
            : 'Unable to load your matter. Please try again later.'
        )
      })
  }, [])

  if (loadError) {
    return (
      <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4">
        <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-10 text-center">
          <AlertTriangle size={40} className="mx-auto text-brand-rose mb-4" />
          <p className="text-brand-ink-2 font-sans text-sm">{loadError}</p>
        </div>
      </div>
    )
  }

  if (!matter) {
    return (
      <div className="min-h-screen bg-brand-bg flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg">
      <header className="bg-brand-ink text-white">
        <div className="max-w-4xl mx-auto px-4 py-5 flex items-center gap-3">
          <ShieldCheck size={26} strokeWidth={1.5} />
          <div>
            <p className="text-xs uppercase tracking-wide text-white/60 font-sans">Clarity Legal — Client Portal</p>
            <h1 className="font-serif font-bold text-xl">{matter.matter_name}</h1>
          </div>
        </div>
      </header>

      <div className="max-w-4xl mx-auto px-4">
        <nav className="flex gap-1 border-b border-brand-line overflow-x-auto">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-sans font-medium whitespace-nowrap border-b-2 transition-colors ${
                tab === key
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-ink-2 hover:text-brand-ink'
              }`}
            >
              <Icon size={16} /> {label}
            </button>
          ))}
        </nav>

        <div className="py-6">
          {tab === 'overview' && <OverviewTab matter={matter} />}
          {tab === 'messages' && <MessagesTab />}
          {tab === 'documents' && <DocumentsTab />}
          {tab === 'signatures' && <SignaturesTab />}
          {tab === 'invoices' && <InvoicesTab />}
        </div>
      </div>
    </div>
  )
}

function Card({ children }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-5">{children}</div>
  )
}

function OverviewTab({ matter }) {
  return (
    <div className="space-y-4">
      <Card>
        <div className="grid grid-cols-2 gap-4 text-sm font-sans">
          <Field label="Status" value={matter.status} />
          <Field label="Stage" value={matter.stage} />
          <Field label="Practice area" value={matter.practice_area} />
        </div>
        {matter.description && (
          <div className="mt-4">
            <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-1">Summary</p>
            <p className="text-sm text-brand-ink">{matter.description}</p>
          </div>
        )}
      </Card>

      <Card>
        <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-2">Your legal team</p>
        {matter.attorneys?.length ? (
          <ul className="space-y-1">
            {matter.attorneys.map((a, i) => (
              <li key={i} className="text-sm text-brand-ink">
                {a.name}{a.role ? <span className="text-brand-ink-2"> — {a.role}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-brand-ink-2">No team members listed yet.</p>
        )}
      </Card>

      {matter.key_dates && Object.keys(matter.key_dates).length > 0 && (
        <Card>
          <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-2">Key dates</p>
          <ul className="space-y-1">
            {Object.entries(matter.key_dates).map(([k, v]) => (
              <li key={k} className="text-sm text-brand-ink flex justify-between">
                <span className="text-brand-ink-2">{k}</span>
                <span>{String(v)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function Field({ label, value }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-1">{label}</p>
      <p className="text-brand-ink capitalize">{value || '—'}</p>
    </div>
  )
}

function MessagesTab() {
  const [messages, setMessages] = useState([])
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    listClientPortalMessages()
      .then(setMessages)
      .catch(() => setErr('Unable to load messages. Please retry or contact your legal team.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const send = async (e) => {
    e.preventDefault()
    if (!body.trim()) return
    setSending(true)
    setErr('')
    try {
      await sendClientPortalMessage({ body })
      setBody('')
      load()
    } catch (e2) {
      setErr(e2?.response?.data?.detail || 'Unable to send your message. Please try again.')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-4">
      {err && <p className="text-sm text-brand-rose">{err}</p>}
      <Card>
        {loading ? (
          <p className="text-sm text-brand-ink-2">Loading messages…</p>
        ) : messages.length === 0 ? (
          <p className="text-sm text-brand-ink-2">No messages yet. Send your legal team a message below.</p>
        ) : (
          <ul className="space-y-3">
            {messages.map((m) => (
              <li
                key={m.id}
                className={`text-sm p-3 rounded-lg ${
                  m.direction === 'inbound'
                    ? 'bg-brand-accent/10 ml-8'
                    : 'bg-brand-bg mr-8'
                }`}
              >
                <p className="text-xs text-brand-ink-2 mb-1">
                  {m.direction === 'inbound' ? 'You' : 'Legal team'} · {new Date(m.occurred_at).toLocaleString()}
                </p>
                <p className="text-brand-ink whitespace-pre-wrap">{m.body}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <form onSubmit={send} className="flex gap-2">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={2}
          placeholder="Write a message to your legal team…"
          className="flex-1 border border-brand-line rounded-xl px-3 py-2 text-sm font-sans resize-none focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
        />
        <button
          type="submit"
          disabled={sending || !body.trim()}
          className="px-4 self-end py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50 flex items-center gap-2"
        >
          <Send size={16} /> Send
        </button>
      </form>
    </div>
  )
}

function DocumentsTab() {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    listClientPortalDocuments()
      .then(setDocs)
      .catch(() => setErr('Unable to load documents. Please retry or contact your legal team.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const onUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setErr('')
    try {
      await uploadClientPortalDocument(file)
      load()
    } catch (e2) {
      setErr(e2?.response?.data?.detail || 'Upload failed. Please try again.')
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  return (
    <div className="space-y-4">
      {err && <p className="text-sm text-brand-rose">{err}</p>}
      <div className="flex justify-end">
        <label className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all cursor-pointer flex items-center gap-2">
          <Upload size={16} /> {uploading ? 'Uploading…' : 'Upload document'}
          <input type="file" className="hidden" onChange={onUpload} disabled={uploading} />
        </label>
      </div>
      <Card>
        {loading ? (
          <p className="text-sm text-brand-ink-2">Loading documents…</p>
        ) : docs.length === 0 ? (
          <p className="text-sm text-brand-ink-2">No shared documents yet.</p>
        ) : (
          <ul className="divide-y divide-brand-line">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center justify-between py-3">
                <div className="flex items-center gap-3">
                  <FileText size={18} className="text-brand-ink-2" />
                  <div>
                    <p className="text-sm text-brand-ink">{d.filename}</p>
                    <p className="text-xs text-brand-ink-2">
                      {fmtBytes(d.file_size)}{d.uploaded_by_client ? ' · uploaded by you' : ' · shared by your legal team'}
                    </p>
                  </div>
                </div>
                <a
                  href={downloadClientPortalDocumentUrl(d.id)}
                  className="text-brand-accent hover:text-brand-ink flex items-center gap-1 text-sm"
                >
                  <Download size={16} /> Download
                </a>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function formatSignatureDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit'
    }).format(new Date(value))
  } catch {
    return '—'
  }
}

function formatSignerRole(role) {
  return (role || 'signer').replace(/_/g, ' ')
}

function portalSignatureStatus(req) {
  if (req.status === 'expired') return 'Expired'
  if (req.status === 'declined') return 'Declined'
  if (req.status === 'voided') return 'Voided'
  return 'Action required'
}

function SignaturesTab() {
  const [requests, setRequests] = useState([])
  const [signing, setSigning] = useState(null) // request id being signed
  const [declining, setDeclining] = useState(null)
  const [typedByRequest, setTypedByRequest] = useState({})
  const [acceptedByRequest, setAcceptedByRequest] = useState({})
  const [declineReasonByRequest, setDeclineReasonByRequest] = useState({})
  const [err, setErr] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    listClientPortalSignatures()
      .then(setRequests)
      .catch(() => setErr('Unable to load signature requests. Please retry or contact your legal team.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const sign = async (req) => {
    setErr('')
    setSuccess('')
    const typed = (typedByRequest[req.id] || '').trim()
    if (!typed) { setErr('Type your full legal name exactly as you want it to appear on the signature certificate.'); return }
    if (!acceptedByRequest[req.id]) { setErr('Review and accept the electronic signature consent before signing.'); return }
    setSigning(req.id)
    try {
      await signClientPortalSignature(req.id, { typed_signature: typed })
      setTypedByRequest((prev) => ({ ...prev, [req.id]: '' }))
      setAcceptedByRequest((prev) => ({ ...prev, [req.id]: false }))
      setSuccess('Signature captured. Your legal team will see the executed copy in the matter documents.')
      load()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to sign. Please try again.')
    } finally {
      setSigning(null)
    }
  }

  const decline = async (req) => {
    setErr('')
    setSuccess('')
    const reason = (declineReasonByRequest[req.id] || '').trim()
    setDeclining(req.id)
    try {
      await declineClientPortalSignature(req.id, { reason })
      setDeclineReasonByRequest((prev) => ({ ...prev, [req.id]: '' }))
      setSuccess('Signature request declined. Your legal team will see the reason.')
      load()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to decline. Please try again.')
    } finally {
      setDeclining(null)
    }
  }

  if (loading) {
    return (
      <Card>
        <div className="flex items-center gap-3 text-sm text-brand-ink-2">
          <div className="w-5 h-5 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          Loading signature requests…
        </div>
      </Card>
    )
  }

  if (requests.length === 0) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <CheckCircle2 size={22} className="text-brand-green mt-0.5" />
          <div>
            <p className="text-sm font-medium text-brand-ink">You're all caught up.</p>
            <p className="text-sm text-brand-ink-2 mt-1">No documents are awaiting your signature. Completed signature certificates will appear in Documents when available.</p>
          </div>
        </div>
        {success && <p className="text-sm text-brand-green mt-3">{success}</p>}
        {err && <p className="text-sm text-brand-rose mt-3">{err}</p>}
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start gap-3">
          <LockKeyhole size={22} className="text-brand-accent mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-brand-ink">Secure e-signature</p>
            <p className="text-sm text-brand-ink-2 mt-1">Review each document name, type your legal name, and consent to sign electronically. We record the time, portal identity, and IP address for the completion certificate.</p>
          </div>
        </div>
      </Card>
      {err && <p className="text-sm text-brand-rose">{err}</p>}
      {success && <p className="text-sm text-brand-green">{success}</p>}
      {requests.map((req) => {
        const typed = typedByRequest[req.id] || ''
        const accepted = Boolean(acceptedByRequest[req.id])
        const canAct = ['sent', 'partially_signed'].includes(req.status)
        const declineReason = declineReasonByRequest[req.id] || ''
        return (
          <Card key={req.id}>
            <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-4">
              <div>
                <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wide mb-3 ${canAct ? 'bg-brand-amber/10 text-brand-amber' : 'bg-brand-bg-soft text-brand-ink-2'}`}>
                  {portalSignatureStatus(req)}
                </div>
                <p className="text-base font-serif font-bold text-brand-ink">{req.document_name || 'Document'}</p>
                <p className="text-xs text-brand-ink-2 mt-1">
                  Sent {formatSignatureDate(req.sent_at)} · Expires {formatSignatureDate(req.expires_at)} · {req.signers?.length || 0} signer(s)
                </p>
                {(req.decline_reason || req.void_reason) && (
                  <p className="text-xs text-brand-rose mt-1">{req.decline_reason || req.void_reason}</p>
                )}
              </div>
              <PenLine size={22} className="text-brand-accent" />
            </div>

            <div className="rounded-xl border border-brand-line overflow-hidden mb-4">
              {req.signers?.map((s, idx) => (
                <div key={s.id} className="flex items-center justify-between gap-3 px-3 py-2 border-b border-brand-line last:border-b-0 bg-white/60">
                  <div>
                    <p className="text-sm text-brand-ink">{idx + 1}. {s.name}</p>
                    <p className="text-xs text-brand-ink-2 capitalize">{formatSignerRole(s.role)}</p>
                    <p className="text-xs text-brand-ink-2">{s.email}</p>
                  </div>
                  <span className={`text-xs font-semibold capitalize ${s.status === 'signed' ? 'text-brand-green' : s.status === 'declined' ? 'text-brand-rose' : 'text-brand-amber'}`}>
                    {s.status === 'signed' ? `Signed ${formatSignatureDate(s.signed_at)}` : s.status === 'declined' ? 'Declined' : 'Pending'}
                  </span>
                </div>
              ))}
            </div>

            {canAct ? (
              <>
                <label className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Typed signature</label>
                <input
                  value={typed}
                  onChange={(e) => setTypedByRequest((prev) => ({ ...prev, [req.id]: e.target.value }))}
                  placeholder="Type your full legal name"
                  className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                />
                <label className="flex items-start gap-2 mt-3 text-xs text-brand-ink-2">
                  <input
                    type="checkbox"
                    checked={accepted}
                    onChange={(e) => setAcceptedByRequest((prev) => ({ ...prev, [req.id]: e.target.checked }))}
                    className="mt-0.5"
                  />
                  <span>I consent to use an electronic signature and understand this typed name will be attached to this document's completion certificate.</span>
                </label>
                <div className="mt-4 flex flex-col sm:flex-row gap-2">
                  <button
                    onClick={() => sign(req)}
                    disabled={signing === req.id || !typed.trim() || !accepted}
                    className="w-full sm:w-auto px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {signing === req.id ? 'Capturing signature…' : 'Sign document'}
                  </button>
                  <button
                    onClick={() => decline(req)}
                    disabled={declining === req.id}
                    className="w-full sm:w-auto px-5 py-2.5 border border-brand-rose text-brand-rose text-sm font-sans font-semibold rounded-lg hover:bg-brand-rose/5 transition-all disabled:opacity-50"
                  >
                    {declining === req.id ? 'Declining…' : 'Decline'}
                  </button>
                </div>
                <input
                  value={declineReason}
                  onChange={(e) => setDeclineReasonByRequest((prev) => ({ ...prev, [req.id]: e.target.value }))}
                  placeholder="Decline reason"
                  className="mt-3 w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                />
              </>
            ) : (
              <p className="text-sm text-brand-ink-2">This signature request is no longer open for signing.</p>
            )}
          </Card>
        )
      })}
    </div>
  )
}

function InvoicesTab() {
  const [invoices, setInvoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  useEffect(() => {
    setLoading(true)
    setErr('')
    listClientPortalInvoices()
      .then(setInvoices)
      .catch(() => setErr('Unable to load invoices. Please retry or contact your legal team.'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card>
      {err && <p className="text-sm text-brand-rose mb-2">{err}</p>}
      {loading ? (
        <p className="text-sm text-brand-ink-2">Loading invoices…</p>
      ) : invoices.length === 0 ? (
        <p className="text-sm text-brand-ink-2">No invoices to show.</p>
      ) : (
        <ul className="divide-y divide-brand-line">
          {invoices.map((inv) => (
            <li key={inv.id} className="flex items-center justify-between py-3 text-sm">
              <div>
                <p className="text-brand-ink font-medium">{inv.invoice_number}</p>
                <p className="text-xs text-brand-ink-2">
                  Issued {inv.issue_date} · Due {inv.due_date} · <span className="capitalize">{inv.status}</span>
                </p>
              </div>
              <div className="flex items-center gap-4">
                <span className="text-brand-ink font-medium">${Number(inv.total).toLocaleString()}</span>
                {inv.status !== 'paid' && inv.stripe_payment_link && (
                  <a
                    href={inv.stripe_payment_link}
                    target="_blank"
                    rel="noreferrer"
                    className="px-3 py-1.5 bg-brand-green text-white rounded-lg hover:opacity-90 transition-all"
                  >
                    Pay now
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
