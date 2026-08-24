import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  getClientPortalSession,
  logoutClientPortal,
  getClientPortalMatter,
  listClientPortalMessages,
  sendClientPortalMessage,
  markClientPortalMessagesRead,
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
  LogOut, CalendarClock, CreditCard, RefreshCw, Paperclip, Clock,
} from 'lucide-react'

const TABS = [
  { key: 'overview', label: 'Overview', icon: Scale },
  { key: 'messages', label: 'Messages', icon: MessageSquare },
  { key: 'documents', label: 'Documents', icon: FileText },
  { key: 'signatures', label: 'Signatures', icon: PenLine },
  { key: 'invoices', label: 'Invoices', icon: Receipt },
]

// The client's own tab is refreshed while they sit on it — a portal is only
// useful if a reply from the firm shows up without a manual reload.
const MESSAGE_POLL_MS = 30_000
// Matches the backend's default page size for /portal/client/messages.
const MESSAGE_PAGE_SIZE = 50
const MAX_MESSAGE_LENGTH = 10_000

function fmtBytes(n) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function fmtMoney(value) {
  const n = Number(value || 0)
  return n.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  })
}

function fmtDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function fmtDateTime(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function relativeDays(days) {
  if (days === null || days === undefined) return null
  if (days === 0) return 'today'
  if (days === 1) return 'tomorrow'
  if (days > 0) return `in ${days} days`
  if (days === -1) return 'yesterday'
  return `${Math.abs(days)} days ago`
}

function isSessionError(err) {
  const s = err?.response?.status
  return s === 401 || s === 403
}

function errorMessage(err, fallback) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return fallback
}

export default function ClientPortalMatterPage() {
  const [matter, setMatter] = useState(null)
  const [session, setSession] = useState(null)
  const [tab, setTab] = useState('overview')
  const [loadError, setLoadError] = useState('')
  const [expired, setExpired] = useState(false)
  const [signingOut, setSigningOut] = useState(false)

  // Any tab hitting an expired session escalates to the whole-page notice —
  // otherwise a client sits on a screen of "unable to load" panels with no
  // explanation of why or what to do next.
  const handleSessionExpiry = useCallback((err) => {
    if (isSessionError(err)) {
      setExpired(true)
      return true
    }
    return false
  }, [])

  const refreshMatter = useCallback(
    () =>
      getClientPortalMatter()
        .then((data) => {
          setMatter(data)
          return data
        })
        .catch((err) => {
          if (handleSessionExpiry(err)) return null
          setLoadError('Unable to load your matter. Please try again later.')
          return null
        }),
    [handleSessionExpiry],
  )

  useEffect(() => {
    refreshMatter()
    getClientPortalSession().then(setSession).catch(() => {})
  }, [refreshMatter])

  const signOut = async () => {
    setSigningOut(true)
    try {
      await logoutClientPortal()
    } catch {
      // Sign-out is best-effort on the wire; the notice below is what the
      // client acts on either way.
    } finally {
      setSigningOut(false)
      setExpired(true)
    }
  }

  if (expired) {
    return (
      <PortalNotice
        icon={LockKeyhole}
        tone="accent"
        title="You've been signed out"
        body="Open the link from your invitation email again to return to your matter. If the link has expired, your legal team can send a new one."
      />
    )
  }

  if (loadError) {
    return (
      <PortalNotice
        icon={AlertTriangle}
        tone="rose"
        title="Something went wrong"
        body={loadError}
        action={{ label: 'Try again', onClick: () => { setLoadError(''); refreshMatter() } }}
      />
    )
  }

  if (!matter) {
    return (
      <div className="min-h-screen bg-brand-bg flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const badges = {
    messages: matter.unread_message_count || 0,
    signatures: matter.pending_signature_count || 0,
    invoices: matter.open_invoice_count || 0,
  }

  const tabProps = { matter, onSessionError: handleSessionExpiry, onChanged: refreshMatter }

  return (
    <div className="min-h-screen bg-brand-bg">
      <header className="bg-brand-ink text-white">
        <div className="max-w-5xl mx-auto px-4 py-5 flex items-start justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <ShieldCheck size={26} strokeWidth={1.5} className="shrink-0" />
            <div className="min-w-0">
              <p className="text-xs uppercase tracking-wide text-white/60 font-sans">
                LawHand — Client Portal
              </p>
              <h1 className="font-serif font-bold text-xl truncate">{matter.matter_name}</h1>
            </div>
          </div>
          <div className="text-right shrink-0">
            {session?.email && (
              <p className="text-xs text-white/60 font-sans hidden sm:block truncate max-w-[16rem]">
                {session.email}
              </p>
            )}
            <button
              onClick={signOut}
              disabled={signingOut}
              className="mt-1 inline-flex items-center gap-1.5 text-xs font-sans font-medium text-white/80 hover:text-white border border-white/25 hover:border-white/50 rounded-lg px-2.5 py-1.5 transition-colors disabled:opacity-50"
            >
              <LogOut size={14} /> {signingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-4">
        <nav role="tablist" aria-label="Client portal sections"
          className="flex gap-1 border-b border-brand-line overflow-x-auto">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              role="tab"
              id={`portal-tab-${key}`}
              aria-selected={tab === key}
              aria-controls={`portal-panel-${key}`}
              onClick={() => setTab(key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-sans font-medium whitespace-nowrap border-b-2 transition-colors ${
                tab === key
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-ink-2 hover:text-brand-ink'
              }`}
            >
              <Icon size={16} /> {label}
              {badges[key] > 0 && (
                <span
                  aria-label={`${badges[key]} needing attention`}
                  className="ml-0.5 min-w-[1.25rem] px-1.5 py-0.5 rounded-full bg-brand-accent text-white text-[11px] leading-none font-semibold"
                >
                  {badges[key]}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div
          role="tabpanel"
          id={`portal-panel-${tab}`}
          aria-labelledby={`portal-tab-${tab}`}
          className="py-6"
        >
          {tab === 'overview' && <OverviewTab {...tabProps} onNavigate={setTab} />}
          {tab === 'messages' && <MessagesTab {...tabProps} />}
          {tab === 'documents' && <DocumentsTab {...tabProps} />}
          {tab === 'signatures' && <SignaturesTab {...tabProps} />}
          {tab === 'invoices' && <InvoicesTab {...tabProps} />}
        </div>
      </div>
    </div>
  )
}

function PortalNotice({ icon: Icon, tone, title, body, action }) {
  const toneClass = tone === 'rose' ? 'text-brand-rose' : 'text-brand-accent'
  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4">
      <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-10 text-center">
        <Icon size={40} className={`mx-auto mb-4 ${toneClass}`} strokeWidth={1.5} />
        <h1 className="font-serif font-bold text-xl text-brand-ink mb-2">{title}</h1>
        <p className="text-brand-ink-2 font-sans text-sm leading-relaxed">{body}</p>
        {action && (
          <button
            onClick={action.onClick}
            className="mt-6 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all"
          >
            {action.label}
          </button>
        )}
      </div>
    </div>
  )
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-brand-surface border border-brand-line rounded-xl p-5 ${className}`}>
      {children}
    </div>
  )
}

function CardHeading({ children }) {
  return (
    <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-2 font-sans">{children}</p>
  )
}

function ErrorBanner({ message, onRetry }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3">
      <AlertTriangle size={16} className="text-brand-rose mt-0.5 shrink-0" />
      <p className="text-sm text-brand-ink flex-1">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-xs font-sans font-semibold text-brand-rose hover:underline flex items-center gap-1 shrink-0"
        >
          <RefreshCw size={12} /> Retry
        </button>
      )}
    </div>
  )
}

function Spinner({ label }) {
  return (
    <div className="flex items-center gap-3 text-sm text-brand-ink-2">
      <div className="w-4 h-4 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      {label}
    </div>
  )
}

/** A tab hook that funnels expired sessions up to the page shell. */
function usePortalResource(loader, onSessionError, fallbackMessage) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(
    ({ quiet = false } = {}) => {
      if (!quiet) setLoading(true)
      return loader()
        .then((result) => {
          setData(result)
          setError('')
          return result
        })
        .catch((err) => {
          if (!onSessionError(err)) setError(errorMessage(err, fallbackMessage))
          return null
        })
        .finally(() => setLoading(false))
    },
    [loader, onSessionError, fallbackMessage],
  )

  useEffect(() => { load() }, [load])
  return { data, loading, error, reload: load, setData }
}

// ── Overview ────────────────────────────────────────────────────────────────

function OverviewTab({ matter, onNavigate }) {
  const tiles = [
    {
      key: 'messages',
      label: 'Unread messages',
      value: matter.unread_message_count || 0,
      icon: MessageSquare,
      highlight: (matter.unread_message_count || 0) > 0,
    },
    {
      key: 'signatures',
      label: 'Awaiting signature',
      value: matter.pending_signature_count || 0,
      icon: PenLine,
      highlight: (matter.pending_signature_count || 0) > 0,
    },
    {
      key: 'documents',
      label: 'Shared documents',
      value: matter.document_count || 0,
      icon: FileText,
    },
    {
      key: 'invoices',
      label: 'Balance due',
      value: fmtMoney(matter.outstanding_balance),
      icon: CreditCard,
      highlight: Number(matter.outstanding_balance || 0) > 0,
    },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {tiles.map(({ key, label, value, icon: Icon, highlight }) => (
          <button
            key={key}
            onClick={() => onNavigate(key)}
            className={`text-left bg-brand-surface border rounded-xl p-4 transition-colors hover:border-brand-accent/60 ${
              highlight ? 'border-brand-accent/50' : 'border-brand-line'
            }`}
          >
            <Icon size={16} className={highlight ? 'text-brand-accent' : 'text-brand-ink-2'} />
            <p className="text-xl font-serif font-bold text-brand-ink mt-2 leading-tight">{value}</p>
            <p className="text-xs text-brand-ink-2 font-sans mt-0.5">{label}</p>
          </button>
        ))}
      </div>

      {matter.next_key_date && (
        <Card className="border-brand-accent/40">
          <div className="flex items-start gap-3">
            <CalendarClock size={20} className="text-brand-accent mt-0.5 shrink-0" />
            <div>
              <CardHeading>Next key date</CardHeading>
              <p className="text-sm font-medium text-brand-ink">
                {matter.next_key_date.label} — {fmtDate(matter.next_key_date.iso_date) }
              </p>
              <p className="text-xs text-brand-ink-2 mt-0.5">
                {relativeDays(matter.next_key_date.days_away)}
              </p>
            </div>
          </div>
        </Card>
      )}

      <Card>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm font-sans">
          <Field label="Status" value={matter.status} />
          <Field label="Stage" value={matter.stage} />
          <Field label="Practice area" value={matter.practice_area} />
        </div>
        {matter.description && (
          <div className="mt-4">
            <CardHeading>Summary</CardHeading>
            <p className="text-sm text-brand-ink whitespace-pre-wrap">{matter.description}</p>
          </div>
        )}
      </Card>

      <Card>
        <CardHeading>Your legal team</CardHeading>
        {matter.attorneys?.length ? (
          <ul className="divide-y divide-brand-line">
            {matter.attorneys.map((a, i) => (
              <li key={i} className="py-2 first:pt-0 last:pb-0">
                <p className="text-sm text-brand-ink">
                  {a.name}
                  {a.role ? <span className="text-brand-ink-2 capitalize"> — {a.role}</span> : null}
                </p>
                {a.email && (
                  <a href={`mailto:${a.email}`} className="text-xs text-brand-accent hover:underline">
                    {a.email}
                  </a>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-brand-ink-2">No team members listed yet.</p>
        )}
      </Card>

      {matter.key_date_list?.length > 0 && (
        <Card>
          <CardHeading>Key dates</CardHeading>
          <ul className="divide-y divide-brand-line">
            {matter.key_date_list.map((k, i) => (
              <li key={`${k.label}-${i}`} className="flex items-baseline justify-between gap-3 py-2 first:pt-0 last:pb-0">
                <span className={`text-sm ${k.is_past ? 'text-brand-ink-2' : 'text-brand-ink'}`}>
                  {k.label}
                </span>
                <span className="text-sm text-right">
                  <span className={k.is_past ? 'text-brand-ink-2' : 'text-brand-ink'}>
                    {k.iso_date ? fmtDate(k.iso_date) : k.value}
                  </span>
                  {k.days_away !== null && k.days_away !== undefined && (
                    <span className="block text-xs text-brand-ink-2">
                      {relativeDays(k.days_away)}
                    </span>
                  )}
                </span>
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

// ── Messages ────────────────────────────────────────────────────────────────

function MessagesTab({ onSessionError, onChanged }) {
  // The newest page is polled; older pages are fetched on demand and never
  // re-fetched. Keeping them apart means a new arrival cannot renumber the
  // offsets of history the client has already scrolled back through.
  const [newest, setNewest] = useState([])
  const [older, setOlder] = useState([])
  const [total, setTotal] = useState(0)
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [err, setErr] = useState('')
  const scrollRef = useRef(null)
  const markedRef = useRef(false)

  const messages = useMemo(() => {
    // A message posted between two page fetches can land in both, so identity
    // decides what is rendered, not concatenation order.
    const seen = new Set()
    return [...older, ...newest].filter((m) => {
      if (seen.has(m.id)) return false
      seen.add(m.id)
      return true
    })
  }, [older, newest])

  const hasOlder = messages.length < total

  const load = useCallback(
    ({ quiet = false } = {}) => {
      if (!quiet) setLoading(true)
      return listClientPortalMessages({ limit: MESSAGE_PAGE_SIZE })
        .then((data) => {
          setNewest(data?.messages || [])
          setTotal(data?.total || 0)
          setErr('')
          return data
        })
        .catch((e) => {
          if (!onSessionError(e)) {
            setErr('Unable to load messages. Please retry or contact your legal team.')
          }
          return null
        })
        .finally(() => setLoading(false))
    },
    [onSessionError],
  )

  const loadOlder = useCallback(() => {
    if (loadingOlder) return
    setLoadingOlder(true)
    setErr('')
    const node = scrollRef.current
    const anchorHeight = node ? node.scrollHeight : 0
    listClientPortalMessages({ limit: MESSAGE_PAGE_SIZE, offset: messages.length })
      .then((data) => {
        setOlder((prev) => [...(data?.messages || []), ...prev])
        setTotal(data?.total || 0)
        // Hold the client's place: prepending would otherwise jump them to the
        // top of a thread they were reading the middle of.
        requestAnimationFrame(() => {
          if (node) node.scrollTop = node.scrollHeight - anchorHeight
        })
      })
      .catch((e) => {
        if (!onSessionError(e)) {
          setErr('Unable to load earlier messages. Please try again.')
        }
      })
      .finally(() => setLoadingOlder(false))
  }, [loadingOlder, messages.length, onSessionError])

  useEffect(() => { load() }, [load])

  // Keep the thread live while the client is reading it.
  useEffect(() => {
    const id = setInterval(() => load({ quiet: true }), MESSAGE_POLL_MS)
    return () => clearInterval(id)
  }, [load])

  // Opening the tab is the read receipt; only clear the badge once, and only up
  // to the newest message actually delivered here.
  useEffect(() => {
    if (markedRef.current || loading || messages.length === 0) return
    if (!messages.some((m) => m.unread)) return
    markedRef.current = true
    markClientPortalMessagesRead(messages[messages.length - 1]?.occurred_at)
      .then(() => onChanged())
      .catch(() => {})
  }, [loading, messages, onChanged])

  useEffect(() => {
    const node = scrollRef.current
    if (node && older.length === 0) node.scrollTop = node.scrollHeight
  }, [newest.length, older.length])

  const send = async (e) => {
    e.preventDefault()
    const trimmed = body.trim()
    if (!trimmed || sending) return
    setSending(true)
    setErr('')
    try {
      await sendClientPortalMessage({ body: trimmed })
      setBody('')
      await load({ quiet: true })
      onChanged()
    } catch (e2) {
      if (!onSessionError(e2)) {
        setErr(errorMessage(e2, 'Unable to send your message. Please try again.'))
      }
    } finally {
      setSending(false)
    }
  }

  const onKeyDown = (e) => {
    // Enter sends; Shift+Enter is a newline, the convention every messaging
    // surface the client already uses follows.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) {
      e.preventDefault()
      send(e)
    }
  }

  const remaining = MAX_MESSAGE_LENGTH - body.length

  return (
    <div className="space-y-4">
      <ErrorBanner message={err} onRetry={() => load()} />
      <Card className="p-0 overflow-hidden">
        <div ref={scrollRef} className="max-h-[55vh] overflow-y-auto p-5">
          {loading ? (
            <Spinner label="Loading messages…" />
          ) : messages.length === 0 ? (
            <p className="text-sm text-brand-ink-2">
              No messages yet. Send your legal team a message below.
            </p>
          ) : (
            <>
              {hasOlder && (
                <div className="text-center mb-3">
                  <button
                    onClick={loadOlder}
                    disabled={loadingOlder}
                    className="text-sm font-sans font-medium text-brand-accent hover:text-brand-ink disabled:opacity-50"
                  >
                    {loadingOlder
                      ? 'Loading earlier messages…'
                      : `Load earlier messages (${total - messages.length} older)`}
                  </button>
                </div>
              )}
              <ul className="space-y-3">
                {messages.map((m) => {
                  const fromClient = m.direction === 'inbound'
                  return (
                    <li
                      key={m.id}
                      className={`text-sm p-3 rounded-lg max-w-[85%] ${
                        fromClient
                          ? 'bg-brand-accent/10 ml-auto'
                          : `bg-brand-bg-soft mr-auto ${m.unread ? 'ring-1 ring-brand-accent/40' : ''}`
                      }`}
                    >
                      <p className="text-xs text-brand-ink-2 mb-1 flex items-center gap-1.5">
                        {fromClient ? 'You' : 'Legal team'} · {fmtDateTime(m.occurred_at)}
                        {m.unread && (
                          <span className="text-[10px] uppercase tracking-wide font-semibold text-brand-accent">
                            New
                          </span>
                        )}
                      </p>
                      <p className="text-brand-ink whitespace-pre-wrap break-words">{m.body}</p>
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>
      </Card>
      <form onSubmit={send} className="space-y-1.5">
        <div className="flex gap-2">
          <label htmlFor="portal-message-body" className="sr-only">
            Message to your legal team
          </label>
          <textarea
            id="portal-message-body"
            value={body}
            onChange={(e) => setBody(e.target.value.slice(0, MAX_MESSAGE_LENGTH))}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={MAX_MESSAGE_LENGTH}
            placeholder="Write a message to your legal team…"
            className="flex-1 border border-brand-line rounded-xl px-3 py-2 text-sm font-sans resize-none focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
          />
          <button
            type="submit"
            disabled={sending || !body.trim()}
            className="px-4 self-end py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50 flex items-center gap-2"
          >
            <Send size={16} /> {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
        <p className="text-xs text-brand-ink-2">
          Press Enter to send, Shift+Enter for a new line.
          {remaining < 500 && <span className="ml-2">{remaining} characters left</span>}
        </p>
      </form>
    </div>
  )
}

// ── Documents ───────────────────────────────────────────────────────────────

function DocumentsTab({ onSessionError, onChanged }) {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [description, setDescription] = useState('')
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const inputRef = useRef(null)

  const load = useCallback(() => {
    setLoading(true)
    return listClientPortalDocuments()
      .then((data) => { setDocs(Array.isArray(data) ? data : []); setErr('') })
      .catch((e) => {
        if (!onSessionError(e)) {
          setErr('Unable to load documents. Please retry or contact your legal team.')
        }
      })
      .finally(() => setLoading(false))
  }, [onSessionError])

  useEffect(() => { load() }, [load])

  const upload = useCallback(
    async (file) => {
      if (!file || uploading) return
      setUploading(true)
      setProgress(0)
      setErr('')
      try {
        await uploadClientPortalDocument(file, description.trim() || undefined, setProgress)
        setDescription('')
        await load()
        onChanged()
      } catch (e2) {
        if (!onSessionError(e2)) {
          setErr(errorMessage(e2, 'Upload failed. Please try again.'))
        }
      } finally {
        setUploading(false)
        setProgress(0)
        if (inputRef.current) inputRef.current.value = ''
      }
    },
    [description, load, onChanged, onSessionError, uploading],
  )

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    upload(e.dataTransfer.files?.[0])
  }

  const { fromFirm, fromClient } = useMemo(
    () => ({
      fromFirm: docs.filter((d) => !d.uploaded_by_client),
      fromClient: docs.filter((d) => d.uploaded_by_client),
    }),
    [docs],
  )

  return (
    <div className="space-y-4">
      <ErrorBanner message={err} onRetry={() => load()} />

      <Card>
        <CardHeading>Send a document to your legal team</CardHeading>
        <label htmlFor="portal-doc-description" className="sr-only">
          Description of the document
        </label>
        <input
          id="portal-doc-description"
          value={description}
          onChange={(e) => setDescription(e.target.value.slice(0, 500))}
          placeholder="What is this document? (optional)"
          className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans mb-3 focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
        />
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`border-2 border-dashed rounded-xl px-4 py-8 text-center transition-colors ${
            dragging ? 'border-brand-accent bg-brand-accent/5' : 'border-brand-line'
          }`}
        >
          <Paperclip size={22} className="mx-auto text-brand-ink-2 mb-2" />
          <p className="text-sm text-brand-ink-2 mb-3">
            Drag a file here, or choose one from your device.
          </p>
          <label className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all cursor-pointer disabled:opacity-50">
            <Upload size={16} /> {uploading ? `Uploading… ${progress}%` : 'Choose file'}
            <input
              ref={inputRef}
              type="file"
              className="hidden"
              onChange={(e) => upload(e.target.files?.[0])}
              disabled={uploading}
            />
          </label>
          {uploading && (
            <div className="mt-3 h-1.5 bg-brand-bg-soft rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-accent transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}
          <p className="text-xs text-brand-ink-2 mt-3">
            PDFs, documents, spreadsheets, images, and emails are supported.
          </p>
        </div>
      </Card>

      {loading ? (
        <Card><Spinner label="Loading documents…" /></Card>
      ) : docs.length === 0 ? (
        <Card>
          <p className="text-sm text-brand-ink-2">
            No shared documents yet. Anything your legal team shares with you will appear here.
          </p>
        </Card>
      ) : (
        <>
          <DocumentGroup title="Shared by your legal team" docs={fromFirm} />
          <DocumentGroup title="Sent by you" docs={fromClient} />
        </>
      )}
    </div>
  )
}

function DocumentGroup({ title, docs }) {
  if (docs.length === 0) return null
  return (
    <Card>
      <CardHeading>{title}</CardHeading>
      <ul className="divide-y divide-brand-line">
        {docs.map((d) => (
          <li key={d.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
            <div className="flex items-center gap-3 min-w-0">
              <FileText size={18} className="text-brand-ink-2 shrink-0" />
              <div className="min-w-0">
                <p className="text-sm text-brand-ink truncate">{d.filename}</p>
                {d.description && (
                  <p className="text-xs text-brand-ink-2 truncate">{d.description}</p>
                )}
                <p className="text-xs text-brand-ink-2">
                  {fmtDate(d.created_at)}
                  {d.file_size ? ` · ${fmtBytes(d.file_size)}` : ''}
                </p>
              </div>
            </div>
            <a
              href={downloadClientPortalDocumentUrl(d.id)}
              className="text-brand-accent hover:text-brand-ink flex items-center gap-1 text-sm shrink-0"
            >
              <Download size={16} /> <span className="hidden sm:inline">Download</span>
            </a>
          </li>
        ))}
      </ul>
    </Card>
  )
}

// ── Signatures ──────────────────────────────────────────────────────────────

function formatSignerRole(role) {
  return (role || 'signer').replace(/_/g, ' ')
}

function portalSignatureStatus(req) {
  if (req.status === 'expired') return 'Expired'
  if (req.status === 'declined') return 'Declined'
  if (req.status === 'voided') return 'Voided'
  return 'Action required'
}

function SignaturesTab({ onSessionError, onChanged }) {
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
    return listClientPortalSignatures()
      .then((data) => setRequests(Array.isArray(data) ? data : []))
      .catch((e) => {
        if (!onSessionError(e)) {
          setErr('Unable to load signature requests. Please retry or contact your legal team.')
        }
      })
      .finally(() => setLoading(false))
  }, [onSessionError])
  useEffect(() => { load() }, [load])

  const sign = async (req) => {
    setErr('')
    setSuccess('')
    const typed = (typedByRequest[req.id] || '').trim()
    if (!typed) { setErr('Type your full legal name exactly as you want it to appear on the signature certificate.'); return }
    if (!acceptedByRequest[req.id]) { setErr('Review and accept the electronic signature consent before signing.'); return }
    setSigning(req.id)
    try {
      await signClientPortalSignature(req.id, {
        typed_signature: typed,
        consent_to_electronic_signature: true,
        consent_text_version: 'clarity-esign-consent-v1',
      })
      setTypedByRequest((prev) => ({ ...prev, [req.id]: '' }))
      setAcceptedByRequest((prev) => ({ ...prev, [req.id]: false }))
      setSuccess('Signature acknowledgment captured. Your legal team will receive an evidence certificate linked to the source document.')
      await load()
      onChanged()
    } catch (e) {
      if (!onSessionError(e)) setErr(errorMessage(e, 'Failed to sign. Please try again.'))
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
      await load()
      onChanged()
    } catch (e) {
      if (!onSessionError(e)) setErr(errorMessage(e, 'Failed to decline. Please try again.'))
    } finally {
      setDeclining(null)
    }
  }

  if (loading) {
    return <Card><Spinner label="Loading signature requests…" /></Card>
  }

  if (requests.length === 0) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <CheckCircle2 size={22} className="text-brand-green mt-0.5" />
          <div>
            <p className="text-sm font-medium text-brand-ink">You're all caught up.</p>
            <p className="text-sm text-brand-ink-2 mt-1">No signature acknowledgments are awaiting your action. Evidence certificates will appear in Documents when available.</p>
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
            <p className="text-sm font-semibold text-brand-ink">Signature acknowledgment</p>
            <p className="text-sm text-brand-ink-2 mt-1">Review each source document, type your legal name, and consent to sign electronically. We record the time, portal identity, IP address, and document hashes in an evidence certificate; this does not alter the source document.</p>
          </div>
        </div>
      </Card>
      <ErrorBanner message={err} />
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
                  Sent {fmtDateTime(req.sent_at)} · Expires {fmtDateTime(req.expires_at)} · {req.signers?.length || 0} signer(s)
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
                    {s.status === 'signed' ? `Signed ${fmtDateTime(s.signed_at)}` : s.status === 'declined' ? 'Declined' : 'Pending'}
                  </span>
                </div>
              ))}
            </div>

            {canAct ? (
              <>
                <label htmlFor={`signature-${req.id}`} className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Typed signature</label>
                <input
                  id={`signature-${req.id}`}
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
                    required
                  />
                  <span>I consent to use an electronic signature for this acknowledgment. I understand my typed name and audit evidence will be attached to an evidence certificate linked by hash to the source document, and the source document itself is not modified.</span>
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
                <label htmlFor={`decline-reason-${req.id}`} className="sr-only">Decline reason</label>
                <input
                  id={`decline-reason-${req.id}`}
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

// ── Invoices ────────────────────────────────────────────────────────────────

function InvoicesTab({ onSessionError }) {
  const loader = useCallback(() => listClientPortalInvoices(), [])
  const { data, loading, error, reload } = usePortalResource(
    loader,
    onSessionError,
    'Unable to load invoices. Please retry or contact your legal team.',
  )

  if (loading) return <Card><Spinner label="Loading invoices…" /></Card>
  if (error) return <ErrorBanner message={error} onRetry={() => reload()} />

  const invoices = data?.invoices || []
  const outstanding = Number(data?.outstanding_balance || 0)
  const overdue = Number(data?.overdue_balance || 0)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <SummaryTile label="Billed to date" value={fmtMoney(data?.total_billed)} />
        <SummaryTile label="Paid" value={fmtMoney(data?.total_paid)} />
        <SummaryTile
          label={overdue > 0 ? 'Balance due (overdue)' : 'Balance due'}
          value={fmtMoney(outstanding)}
          tone={overdue > 0 ? 'rose' : outstanding > 0 ? 'amber' : 'green'}
        />
      </div>

      <Card>
        {invoices.length === 0 ? (
          <p className="text-sm text-brand-ink-2">No invoices to show.</p>
        ) : (
          <ul className="divide-y divide-brand-line">
            {invoices.map((inv) => (
              <li
                key={inv.id}
                className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 py-3 text-sm first:pt-0 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="text-brand-ink font-medium flex items-center gap-2">
                    {inv.invoice_number}
                    {inv.is_overdue && (
                      <span className="inline-flex items-center gap-1 text-[11px] uppercase tracking-wide font-semibold text-brand-rose bg-brand-rose/10 px-1.5 py-0.5 rounded">
                        <Clock size={10} /> {inv.days_overdue}d overdue
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-brand-ink-2">
                    Issued {fmtDate(inv.issue_date)} · Due {fmtDate(inv.due_date)} ·{' '}
                    <span className="capitalize">{inv.status.replace(/_/g, ' ')}</span>
                  </p>
                </div>
                <div className="flex items-center justify-between sm:justify-end gap-4 shrink-0">
                  <div className="text-right">
                    <p className="text-brand-ink font-medium">{fmtMoney(inv.balance_due)}</p>
                    {Number(inv.amount_paid) > 0 && Number(inv.balance_due) > 0 && (
                      <p className="text-xs text-brand-ink-2">
                        {fmtMoney(inv.amount_paid)} of {fmtMoney(inv.total)} paid
                      </p>
                    )}
                    {Number(inv.balance_due) === 0 && (
                      <p className="text-xs text-brand-green">Paid in full</p>
                    )}
                  </div>
                  {Number(inv.balance_due) > 0 && inv.stripe_payment_link && (
                    <a
                      href={inv.stripe_payment_link}
                      target="_blank"
                      rel="noreferrer"
                      className="px-3 py-1.5 bg-brand-green text-white rounded-lg hover:opacity-90 transition-all whitespace-nowrap"
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
    </div>
  )
}

function SummaryTile({ label, value, tone }) {
  const toneClass =
    tone === 'rose' ? 'text-brand-rose'
      : tone === 'amber' ? 'text-brand-amber'
        : tone === 'green' ? 'text-brand-green'
          : 'text-brand-ink'
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-4">
      <p className="text-xs uppercase tracking-wide text-brand-ink-2 font-sans">{label}</p>
      <p className={`text-xl font-serif font-bold mt-1 ${toneClass}`}>{value}</p>
    </div>
  )
}
