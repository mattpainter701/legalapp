import React, { useState, useEffect, useCallback } from 'react'
import {
  getClientPortalMatter,
  listClientPortalMessages,
  sendClientPortalMessage,
  listClientPortalDocuments,
  uploadClientPortalDocument,
  downloadClientPortalDocumentUrl,
  listClientPortalInvoices,
} from '../api'
import {
  ShieldCheck, MessageSquare, FileText, Receipt, Send,
  Upload, Download, AlertTriangle, Scale,
} from 'lucide-react'

const TABS = [
  { key: 'overview', label: 'Overview', icon: Scale },
  { key: 'messages', label: 'Messages', icon: MessageSquare },
  { key: 'documents', label: 'Documents', icon: FileText },
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

  const load = useCallback(() => {
    listClientPortalMessages().then(setMessages).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const send = async (e) => {
    e.preventDefault()
    if (!body.trim()) return
    setSending(true)
    try {
      await sendClientPortalMessage({ body })
      setBody('')
      load()
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        {messages.length === 0 ? (
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

  const load = useCallback(() => {
    listClientPortalDocuments().then(setDocs).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const onUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await uploadClientPortalDocument(file)
      load()
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <label className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all cursor-pointer flex items-center gap-2">
          <Upload size={16} /> {uploading ? 'Uploading…' : 'Upload document'}
          <input type="file" className="hidden" onChange={onUpload} disabled={uploading} />
        </label>
      </div>
      <Card>
        {docs.length === 0 ? (
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

function InvoicesTab() {
  const [invoices, setInvoices] = useState([])
  useEffect(() => {
    listClientPortalInvoices().then(setInvoices).catch(() => {})
  }, [])

  return (
    <Card>
      {invoices.length === 0 ? (
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
