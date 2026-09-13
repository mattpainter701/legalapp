import ClientSignatureDocument from '../components/ClientSignatureDocument'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import PortalDocumentTransfer from '../components/PortalDocumentTransfer'
import ClientIntakeChecklist from '../components/ClientIntakeChecklist'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { SIGNED_COPY_RECEIVED_MESSAGE, signedOutcomeMessage } from '../components/portal/signingMessages'
import {
  getClientIntake,
  getClientPortalSession,
  logoutClientPortal,
  getClientPortalMatter,
  getClientPortalMediation,
  listClientPortalMessages,
  sendClientPortalMessage,
  markClientPortalMessagesRead,
  listClientPortalDocuments,
  downloadClientPortalDocumentUrl,
  listClientPortalInvoices,
  createClientPortalInvoicePayment,
  downloadClientPortalInvoiceUrl,
  listClientPortalSignatures,
  declineClientPortalSignature,
  listClientPortalMatters,
  switchClientPortalMatter,
} from '../api'
import {
  ShieldCheck, MessageSquare, FileText, Receipt, Send,
  Download, AlertTriangle, Scale, PenLine, CheckCircle2, LockKeyhole,
  LogOut, CalendarClock, CreditCard, RefreshCw, Clock, Handshake,
  Phone, Mail, Globe, Repeat,
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
const MAX_MESSAGE_LENGTH = 10_000
// Drafts are stored per matter so a note meant for one case never surfaces in
// another, and every draft is cleared when the client signs out of this device.
const MESSAGE_DRAFT_KEY_PREFIX = 'client-portal-message-draft'

export function messageDraftKey(matterId) {
  return `${MESSAGE_DRAFT_KEY_PREFIX}:${matterId || ''}`
}

export function clearPortalMessageDrafts() {
  try {
    const stale = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i)
      if (key && key.startsWith(MESSAGE_DRAFT_KEY_PREFIX)) stale.push(key)
    }
    stale.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Storage that cannot be read cannot hold a draft either.
  }
}

function fmtBytes(n) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function fmtMoney(value, currency = 'USD') {
  const n = Number(value || 0)
  try {
    return n.toLocaleString(undefined, {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    })
  } catch {
    // An unrecognized firm currency must never blank out an amount.
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }
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
  const confirmAction = useConfirm()
  const [matter, setMatter] = useState(null)
  const [mediation, setMediation] = useState(null)
  const [session, setSession] = useState(null)
  const [matters, setMatters] = useState([])
  const [switching, setSwitching] = useState(false)
  const [tab, setTab] = useState('overview')
  const [loadError, setLoadError] = useState('')
  const [expired, setExpired] = useState(false)
  const [signedOut, setSignedOut] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const matterRequestSequence = useRef(0)
  const mattersLoadedRef = useRef(false)

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

  const refreshMatter = useCallback(() => {
    const requestSequence = ++matterRequestSequence.current
    return getClientPortalMatter()
        .then((data) => {
          if (requestSequence !== matterRequestSequence.current) return data
          setMatter(data)
          setMediation(null)
          // Mediation is an optional add-on. A missing entitlement, a stale
          // deployment, or a transient failure must never hide the core matter.
          return getClientPortalMediation()
            .then((mediationData) => {
              if (requestSequence === matterRequestSequence.current) {
                setMediation(normalizeClientPortalMediation(mediationData))
              }
            })
            .catch((err) => {
              if (requestSequence !== matterRequestSequence.current) return
              // Only a genuinely expired session ends the visit. An initial
              // paperwork link is denied this add-on by design, and treating
              // that as a sign-out would strand the client before signing.
              if (err?.response?.status === 401) {
                setExpired(true)
                return
              }
              setMediation(null)
            })
            .then(() => data)
        })
        .catch((err) => {
          if (requestSequence !== matterRequestSequence.current) return null
          if (handleSessionExpiry(err)) return null
          setLoadError('Unable to load your matter. Please try again later.')
          return null
        })
  }, [handleSessionExpiry])

  useEffect(() => {
    refreshMatter()
    getClientPortalSession().then(setSession).catch(() => {})
  }, [refreshMatter])

  useEffect(() => {
    if (!mediation && tab === 'mediation') setTab('overview')
  }, [mediation, tab])

  useEffect(() => {
    if (!matter?.paperwork_only) return undefined
    if (!['overview', 'signatures'].includes(tab)) setTab('overview')
    const timer = setInterval(() => { getClientIntake().then(() => refreshMatter()).catch(() => {}) }, 15000)
    return () => clearInterval(timer)
  }, [matter?.paperwork_only, refreshMatter, tab])

  // The client may hold several matters. Keep the list for the header switcher
  // so it is always obvious which matter a document is being sent to. The set
  // of matters does not change while the client is signed in, so it is fetched
  // once per visit rather than on every load or switch.
  useEffect(() => {
    if (!matter || mattersLoadedRef.current) return undefined
    mattersLoadedRef.current = true
    let active = true
    listClientPortalMatters()
      .then((rows) => { if (active) setMatters(Array.isArray(rows) ? rows : []) })
      .catch(() => {
        // Let a later matter load try again rather than hiding the switcher for good.
        mattersLoadedRef.current = false
        if (active) setMatters([])
      })
    return () => { active = false }
  }, [matter])

  const switchMatter = async (matterId) => {
    if (!matterId || matterId === matter?.matter_id || switching) return
    setSwitching(true)
    setLoadError('')
    try {
      await switchClientPortalMatter(matterId)
      setTab('overview')
      setMediation(null)
      await refreshMatter()
    } catch (err) {
      if (!handleSessionExpiry(err)) setLoadError('Unable to switch matters. Please try again.')
    } finally {
      setSwitching(false)
    }
  }

  const signOut = async () => {
    const confirmed = await confirmAction({
      title: 'Sign out of your portal?',
      message: 'To get back in, enter your email and we will send you a new sign-in code. Any message you have started but not sent will be discarded.',
      confirmLabel: 'Sign out',
      destructive: true,
    })
    if (!confirmed) return
    setSigningOut(true)
    try {
      await logoutClientPortal()
    } catch {
      // Sign-out is best-effort on the wire; the notice below is what the
      // client acts on either way.
    } finally {
      clearPortalMessageDrafts()
      setSigningOut(false)
      setSignedOut(true)
      setExpired(true)
    }
  }

  if (expired) {
    return (
      <PortalNotice
        icon={LockKeyhole}
        tone="accent"
        title={signedOut ? "You've signed out" : 'Your secure session ended'}
        body={signedOut
          ? 'Your portal is closed on this device. Sign back in any time with your email and the code we send you.'
          : 'For your security we sign you out after a period of inactivity. Sign back in to continue, or use the link from your invitation email.'}
        action={{ label: 'Sign in to the portal', href: '/portal/client/login' }}
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
  const firm = matter.firm || session?.firm || null
  const firmName = firm?.firm_name

  return (
    <div className="min-h-screen bg-brand-bg">
      <header className="bg-brand-ink text-white">
        <div className="max-w-5xl mx-auto px-4 py-5 flex items-start justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            {firm?.firm_logo_url
              ? <img src={firm.firm_logo_url} alt={firmName || 'Law firm'} className="h-9 max-w-[7rem] object-contain shrink-0" />
              : <ShieldCheck size={26} strokeWidth={1.5} className="shrink-0" />}
            <div className="min-w-0">
              <p className="text-xs uppercase tracking-wide text-white/60 font-sans">
                {matter.paperwork_only ? 'Complete your paperwork' : (firmName || 'Client Portal')}
              </p>
              <h1 className="font-serif font-bold text-xl truncate">{matter.matter_name}</h1>
              {matter.matter_number && (
                // The reference a client quotes when they call or write. The
                // portal URL stays session-scoped, so this is shown, never
                // navigated to.
                <p className="font-mono text-xs tracking-wide text-white/70">
                  Matter <span className="font-semibold text-white/90">{matter.matter_number}</span>
                </p>
              )}
            </div>
          </div>
          <div className="text-right shrink-0">
            {session?.email && (
              <p className="text-xs text-white/60 font-sans hidden sm:block truncate max-w-[16rem]">
                {session.email}
              </p>
            )}
            {matters.length > 1 && (
              <label className="mt-1 flex items-center justify-end gap-1.5 text-xs text-white/80">
                <Repeat size={13} className="shrink-0" />
                <span className="sr-only">Switch matter</span>
                <select
                  value={matter.matter_id}
                  disabled={switching}
                  onChange={(event) => switchMatter(event.target.value)}
                  className="max-w-[13rem] bg-white/10 border border-white/25 rounded-lg px-2 py-1.5 text-xs font-sans text-white focus:outline-none focus:ring-2 focus:ring-white/40 disabled:opacity-50"
                >
                  {matters.map((option) => (
                    <option key={option.matter_id} value={option.matter_id} className="text-brand-ink">
                      {option.matter_name}{option.matter_number ? ` — ${option.matter_number}` : ''}
                    </option>
                  ))}
                </select>
              </label>
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
        <SessionExpiryBanner expiresAt={session?.expires_at} />
        <nav role="tablist" aria-label="Client portal sections"
          className="flex flex-wrap gap-1 border-b border-brand-line sm:flex-nowrap sm:overflow-x-auto">
          {[...TABS.filter(item => !matter.paperwork_only || ['overview', 'signatures'].includes(item.key)), ...(mediation ? [{ key: 'mediation', label: 'Mediation', icon: Handshake }] : [])].map(({ key, label, icon: Icon }) => (
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
          {tab === 'overview' && <><ClientIntakeChecklist onSign={() => setTab('signatures')} />{!matter.paperwork_only && <OverviewTab {...tabProps} onNavigate={setTab} />}</>}
          {tab === 'messages' && <MessagesTab key={matter.matter_id} {...tabProps} />}
          {tab === 'documents' && <DocumentsTab {...tabProps} />}
          {tab === 'signatures' && <SignaturesTab {...tabProps} />}
          {tab === 'invoices' && <InvoicesTab {...tabProps} />}
          {tab === 'mediation' && mediation && <ClientPortalMediationTab mediation={mediation} />}
        </div>
        <PortalHelpFooter firm={firm} />
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
          action.href ? (
            <a
              href={action.href}
              className="mt-6 inline-block px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all"
            >
              {action.label}
            </a>
          ) : (
            <button
              onClick={action.onClick}
              className="mt-6 px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all"
            >
              {action.label}
            </button>
          )
        )}
      </div>
    </div>
  )
}

function SessionExpiryBanner({ expiresAt }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    // Nothing here is worth a per-second clock; a minute's resolution is
    // enough to warn before a long message or upload is interrupted.
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [])
  if (!expiresAt) return null
  const expiry = new Date(expiresAt).getTime()
  if (Number.isNaN(expiry)) return null
  const minutesLeft = Math.floor((expiry - now) / 60_000)
  if (minutesLeft > 30) return null
  return (
    <div role="status" className="mt-3 flex items-start gap-2 bg-brand-amber/10 border border-brand-amber/30 rounded-xl px-4 py-3 text-sm text-brand-ink font-sans">
      <Clock size={16} className="text-brand-amber mt-0.5 shrink-0" />
      <span>
        {minutesLeft <= 0
          ? 'Your secure session has ended. Sign in again to keep going.'
          : `Your secure session ends in ${minutesLeft} minute${minutesLeft === 1 ? '' : 's'}. Save anything you are working on.`}
      </span>
    </div>
  )
}

function PortalHelpFooter({ firm }) {
  if (!firm) return null
  const hasContact = firm.firm_phone || firm.firm_email || firm.firm_website || firm.firm_address
  if (!hasContact) return null
  return (
    <footer className="border-t border-brand-line py-6 mt-2 text-sm font-sans" aria-label="Firm contact">
      <p className="text-xs uppercase tracking-wide text-brand-ink-2 mb-3">Need help?</p>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-brand-ink-2">
        {firm.firm_phone && (
          <a href={`tel:${firm.firm_phone}`} className="inline-flex items-center gap-2 hover:text-brand-ink">
            <Phone size={15} /> {firm.firm_phone}
          </a>
        )}
        {firm.firm_email && (
          <a href={`mailto:${firm.firm_email}`} className="inline-flex items-center gap-2 hover:text-brand-ink">
            <Mail size={15} /> {firm.firm_email}
          </a>
        )}
        {firm.firm_website && (
          <a href={firm.firm_website} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 hover:text-brand-ink">
            <Globe size={15} /> {firm.firm_website}
          </a>
        )}
      </div>
      {firm.firm_address && (
        <p className="text-xs text-brand-ink-2 mt-3 whitespace-pre-line">{firm.firm_address}</p>
      )}
      <p className="text-xs text-brand-ink-2 mt-4">
        Messages sent here are part of your matter record. For anything urgent, please call the firm.
      </p>
    </footer>
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
    <div role="alert" className="flex items-start gap-2 bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3">
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

function normalizeClientPortalMediation(data) {
  if (!data || typeof data !== 'object') return null
  if (!data.mediation && !data.case && !data.id && !data.case_id) return null
  const value = data.mediation || data.case || data
  if (!value || typeof value !== 'object') return null
  const normalizeAssets = (rows) => (Array.isArray(rows) ? rows : []).map((row) => ({
    id: row?.id,
    description: row?.description,
    status: row?.status,
    value: row?.value,
  }))
  const normalizeDocuments = (rows) => (Array.isArray(rows) ? rows : []).map((row) => ({
    id: row?.id,
    filename: row?.filename,
    created_at: row?.created_at,
    is_own: row?.is_own === true,
    release_state: row?.release_state,
    download_url: row?.download_url,
  }))
  const normalizeProposals = (rows) => (Array.isArray(rows) ? rows : []).map((row) => ({
    id: row?.id,
    title: row?.title,
    status: row?.status,
    review_state: row?.review_state,
    is_own: row?.is_own === true,
    release_state: row?.release_state,
  }))
  // Copy only fields this read-only view renders. The server owns the privacy
  // boundary; this allowlist prevents a future response expansion from being
  // retained or accidentally surfaced by the client component.
  return {
    id: value.id || value.case_id,
    case_name: value.case_name,
    title: value.title,
    status: value.status,
    mediation_stage: value.mediation_stage,
    stage: value.stage,
    scheduled_session: value.scheduled_session,
    own_assets: normalizeAssets(data.own_assets || value.own_assets),
    shared_assets: normalizeAssets(data.shared_assets || value.shared_assets),
    documents: normalizeDocuments(data.documents || value.documents),
    proposals: normalizeProposals(data.proposals || value.proposals),
  }
}

function mediationLabel(value) {
  return String(value || '—').replace(/_/g, ' ')
}

function mediationReleaseLabel(item) {
  if (item.release_state === 'released_to_you') return 'Released to you'
  if (item.release_state === 'released') return 'Released by your legal team'
  return item.is_own ? 'Private · attorney review' : 'Not released'
}

function ClientPortalMediationTab({ mediation }) {
  const ownAssets = Array.isArray(mediation.own_assets) ? mediation.own_assets : []
  const sharedAssets = Array.isArray(mediation.shared_assets) ? mediation.shared_assets : []
  const documents = Array.isArray(mediation.documents) ? mediation.documents : []
  const proposals = Array.isArray(mediation.proposals) ? mediation.proposals : []

  return (
    <div className="space-y-4" data-testid="client-portal-mediation">
      <Card>
        <CardHeading>Mediation workflow</CardHeading>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm font-sans">
          <Field label="Case" value={mediation.case_name || mediation.title} />
          <Field label="Status" value={mediation.status} />
          <Field label="Stage" value={mediation.mediation_stage || mediation.stage} />
        </div>
        {mediation.scheduled_session && (
          <p className="text-xs text-brand-ink-2 mt-4">Next session: {fmtDateTime(mediation.scheduled_session)}</p>
        )}
      </Card>

      <Card>
        <CardHeading>Assets</CardHeading>
        <div className="space-y-3 text-sm">
          {[...ownAssets.map((item) => ({ ...item, _label: 'Your submission' })), ...sharedAssets.map((item) => ({ ...item, _label: 'Shared with you' }))].map((item, index) => (
            <div key={item.id || `${item.description}-${index}`} className="border-b border-brand-line last:border-0 pb-3 last:pb-0">
              <div className="flex items-start justify-between gap-3">
                <span className="text-brand-ink font-medium">{item.description || item.name || 'Asset'}</span>
                <span className="text-xs text-brand-ink-2 capitalize">{item._label}</span>
              </div>
              <p className="text-xs text-brand-ink-2 mt-1">{item.status ? mediationLabel(item.status) : 'No status recorded'}{item.value != null ? ` · ${item.value}` : ''}</p>
            </div>
          ))}
          {ownAssets.length + sharedAssets.length === 0 && <p className="text-sm text-brand-ink-2">No assets are available yet.</p>}
        </div>
      </Card>

      <Card>
        <CardHeading>Mediation documents</CardHeading>
        <div className="space-y-3 text-sm">
          {documents.map((document, index) => {
            const href = typeof document.download_url === 'string' && document.download_url.startsWith('/') && !document.download_url.startsWith('//')
              ? document.download_url
              : null
            return (
              <div key={document.id || `${document.filename}-${index}`} className="flex items-center justify-between gap-3 border-b border-brand-line last:border-0 pb-3 last:pb-0">
                <div className="min-w-0">
                  <p className="text-brand-ink font-medium truncate">{document.filename || document.name || 'Document'}</p>
                  <p className="text-xs text-brand-ink-2">{fmtDate(document.created_at)} · {mediationReleaseLabel(document)}</p>
                </div>
                {href && <a href={href} className="inline-flex items-center gap-1.5 text-brand-accent hover:underline shrink-0"><Download size={14} /> Download</a>}
              </div>
            )
          })}
          {documents.length === 0 && <p className="text-sm text-brand-ink-2">No documents are available yet.</p>}
        </div>
      </Card>

      <Card>
        <CardHeading>Proposal review</CardHeading>
        <div className="space-y-3 text-sm">
          {proposals.map((proposal, index) => (
            <div key={proposal.id || `${proposal.title}-${index}`} className="border-b border-brand-line last:border-0 pb-3 last:pb-0">
              <p className="text-brand-ink font-medium">{proposal.title || 'Settlement proposal'}</p>
              <p className="text-xs text-brand-ink-2 mt-1">Review: <span className="capitalize">{mediationLabel(proposal.review_state || proposal.status)}</span> · {mediationReleaseLabel(proposal)}</p>
            </div>
          ))}
          {proposals.length === 0 && <p className="text-sm text-brand-ink-2">No proposals are available yet.</p>}
        </div>
      </Card>
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
      value: fmtMoney(matter.outstanding_balance, matter.firm?.currency),
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

export function MessagesTab({ matter, onSessionError, onChanged }) {
  const draftKey = messageDraftKey(matter?.matter_id)
  const [messages, setMessages] = useState([])
  const [body, setBody] = useState(() => {
    // A long message should survive a session expiry or accidental reload.
    try { return localStorage.getItem(draftKey) || '' } catch { return '' }
  })
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const scrollRef = useRef(null)
  const markedRef = useRef(false)
  const loadInFlightRef = useRef(null)

  useEffect(() => {
    try {
      if (body) localStorage.setItem(draftKey, body)
      else localStorage.removeItem(draftKey)
    } catch {
      // Private-mode or full storage must never block writing a message.
    }
  }, [body, draftKey])

  const load = useCallback(
    async ({ quiet = false, force = false } = {}) => {
      if (loadInFlightRef.current) {
        if (!force) return loadInFlightRef.current
        await loadInFlightRef.current
      }
      if (!quiet) setLoading(true)
      const request = listClientPortalMessages()
        .then((data) => {
          setMessages(data?.messages || [])
          setErr('')
          return data
        })
        .catch((e) => {
          if (!onSessionError(e)) {
            setErr('Unable to load messages. Please retry or contact your legal team.')
          }
          return null
        })
        .finally(() => {
          setLoading(false)
          loadInFlightRef.current = null
        })
      loadInFlightRef.current = request
      return request
    },
    [onSessionError],
  )

  useEffect(() => { load() }, [load])

  // Keep the thread live while the client is reading it.
  useEffect(() => {
    let stopped = false
    let timer
    const schedule = () => {
      if (!stopped) timer = setTimeout(poll, MESSAGE_POLL_MS)
    }
    const poll = () => {
      if (stopped) return
      if (document.visibilityState !== 'visible') {
        schedule()
        return
      }
      load({ quiet: true }).finally(schedule)
    }
    poll()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [load])

  // Opening the tab is the read receipt; only clear the badge once.
  useEffect(() => {
    if (markedRef.current || loading || messages.length === 0) return
    if (!messages.some((m) => m.unread)) return
    markedRef.current = true
    markClientPortalMessagesRead()
      .then(() => onChanged())
      .catch(() => {})
  }, [loading, messages, onChanged])

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages.length])

  const send = async (e) => {
    e.preventDefault()
    const trimmed = body.trim()
    if (!trimmed || sending) return
    setSending(true)
    setErr('')
    try {
      await sendClientPortalMessage({ body: trimmed })
      setBody('')
      await load({ quiet: true, force: true })
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
    // Enter starts a new line; Ctrl/Cmd+Enter sends. A client who presses
    // Enter to begin a paragraph should not have just sent half a thought to
    // their lawyer with no undo.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !e.nativeEvent?.isComposing) {
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
                      {fromClient ? 'You' : (m.sender_name || 'Your legal team')} · {fmtDateTime(m.occurred_at)}
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
          Press Enter for a new line; Ctrl+Enter (⌘+Enter on Mac) to send.
          {remaining < 500 && <span className="ml-2">{remaining} characters left</span>}
        </p>
      </form>
    </div>
  )
}

// ── Documents ───────────────────────────────────────────────────────────────

function DocumentsTab({ matter, onSessionError, onChanged }) {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

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
        <CardHeading>Send documents to your legal team</CardHeading>
        <PortalDocumentTransfer matterName={matter?.matter_name} onSessionError={onSessionError} onUploaded={async () => { await load(); onChanged() }} />
      </Card>

      <div className="flex justify-end">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-xs font-sans font-medium text-brand-ink-2 hover:text-brand-ink border border-brand-line rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
          disabled={loading}
          onClick={load}
        >
          <RefreshCw size={13} /> Refresh list
        </button>
      </div>
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
  if (req.status === 'completed') return 'Signed'
  if (req.submitted_document_id) return 'Awaiting review'
  if (req.completion_pending) return 'Signed — filing'
  return 'Action required'
}

function SignaturesTab({ onSessionError, onChanged }) {
  const confirmAction = useConfirm()
  const [requests, setRequests] = useState([])
  const [declining, setDeclining] = useState(null)
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

  // The in-document form reports back after a sign or an upload; the tab owns
  // the lasting confirmation because the reloaded request no longer shows the form.
  const signed = async (result) => {
    setErr('')
    setSuccess(result?.submitted_document_id ? SIGNED_COPY_RECEIVED_MESSAGE : signedOutcomeMessage(result))
    await load()
    onChanged()
  }

  const decline = async (req) => {
    setErr('')
    setSuccess('')
    const reason = (declineReasonByRequest[req.id] || '').trim()
    // Declining is recorded against the signature request and cannot be undone
    // from the portal. Confirm before the click becomes a refusal.
    const confirmed = await confirmAction({
      title: 'Decline to sign?',
      message: 'Your legal team will be told you will not sign this document. If you only need more time, cancel and leave it for now.',
      confirmLabel: 'Decline to sign',
      destructive: true,
    })
    if (!confirmed) return
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
            <p className="text-sm text-brand-ink-2 mt-1">No documents are waiting for your signature. Signed copies and evidence certificates appear in Documents when available.</p>
          </div>
        </div>
        {success && <p role="status" aria-live="polite" className="text-sm text-brand-green mt-3">{success}</p>}
        {err && <p role="alert" className="text-sm text-brand-rose mt-3">{err}</p>}
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-start gap-3">
          <LockKeyhole size={22} className="text-brand-accent mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-brand-ink">Sign your documents here</p>
            <p className="text-sm text-brand-ink-2 mt-1">Fill in any fields on the document, type your legal name or draw your signature, and click each signature line to place it. We record the time, portal identity, IP address, and document hashes in an evidence certificate filed with the signed copy.</p>
          </div>
        </div>
      </Card>
      <ErrorBanner message={err} />
      {success && <p role="status" aria-live="polite" className="text-sm text-brand-green">{success}</p>}
      {requests.map((req) => {
        const open = ['sent', 'partially_signed'].includes(req.status)
        const waiting = Boolean(req.submitted_document_id || req.completion_pending)
        const declineReason = declineReasonByRequest[req.id] || ''
        return (
          <Card key={req.id}>
            <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-4">
              <div>
                <div className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wide mb-3 ${open && !waiting ? 'bg-brand-amber/10 text-brand-amber' : req.status === 'completed' ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-bg-soft text-brand-ink-2'}`}>
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

            {open ? (
              <>
                <ClientSignatureDocument request={req} onChanged={signed} onSessionError={onSessionError} />
                {!waiting && (
                  <details className="mt-4 border-t border-brand-line pt-4">
                    <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-brand-ink-2">Not signing?</summary>
                    <div className="mt-3">
                      <label htmlFor={`decline-reason-${req.id}`} className="block text-xs text-brand-ink-2 mb-1">
                        Tell your legal team why (optional)
                      </label>
                      <input
                        id={`decline-reason-${req.id}`}
                        value={declineReason}
                        onChange={(e) => setDeclineReasonByRequest((prev) => ({ ...prev, [req.id]: e.target.value }))}
                        placeholder="Add a reason before declining"
                        className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                      />
                      <button
                        onClick={() => decline(req)}
                        disabled={declining === req.id}
                        className="mt-2 w-full sm:w-auto px-5 py-2.5 border border-brand-rose text-brand-rose text-sm font-sans font-semibold rounded-lg hover:bg-brand-rose/5 transition-all disabled:opacity-50"
                      >
                        {declining === req.id ? 'Declining…' : 'Decline to sign'}
                      </button>
                      <p className="text-xs text-brand-ink-2 mt-2">You will be asked to confirm. A decline is recorded and cannot be undone here.</p>
                    </div>
                  </details>
                )}
              </>
            ) : (
              <p className="text-sm text-brand-ink-2">
                {req.status === 'completed'
                  ? 'Signed. The signed copy and its evidence certificate are in Documents.'
                  : 'This signature request is no longer open for signing.'}
              </p>
            )}
          </Card>
        )
      })}
    </div>
  )
}

// ── Invoices ────────────────────────────────────────────────────────────────

function InvoicesTab({ matter, onSessionError }) {
  const currency = matter?.firm?.currency
  const [paying, setPaying] = useState(null)
  const [payError, setPayError] = useState('')
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
  const pay = async (invoiceId) => {
    setPaying(invoiceId)
    setPayError('')
    try {
      const result = await createClientPortalInvoicePayment(invoiceId)
      if (!result.checkout_url) throw new Error('Payment provider did not return a checkout URL')
      window.location.assign(result.checkout_url)
    } catch (err) {
      if (!onSessionError(err)) setPayError(errorMessage(err, 'Unable to start payment. Please try again.'))
      setPaying(null)
    }
  }

  return (
    <div className="space-y-4">
      {payError && <ErrorBanner message={payError} onRetry={() => setPayError('')} />}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <SummaryTile label="Billed to date" value={fmtMoney(data?.total_billed, currency)} />
        <SummaryTile label="Paid" value={fmtMoney(data?.total_paid, currency)} />
        <SummaryTile
          label={overdue > 0 ? 'Balance due (overdue)' : 'Balance due'}
          value={fmtMoney(outstanding, currency)}
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
                    <p className="text-brand-ink font-medium">{fmtMoney(inv.balance_due, currency)}</p>
                    {Number(inv.amount_paid) > 0 && Number(inv.balance_due) > 0 && (
                      <p className="text-xs text-brand-ink-2">
                        {fmtMoney(inv.amount_paid, currency)} of {fmtMoney(inv.total, currency)} paid
                      </p>
                    )}
                    {Number(inv.balance_due) === 0 && (
                      <p className="text-xs text-brand-green">Paid in full</p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <a
                      href={downloadClientPortalInvoiceUrl(inv.id)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink rounded-lg hover:bg-brand-bg transition-all whitespace-nowrap"
                    >
                      <Download size={14} /> PDF
                    </a>
                    {Number(inv.balance_due) > 0 && (
                      <a
                        href={inv.stripe_payment_link || '#'}
                        onClick={(event) => { event.preventDefault(); pay(inv.id) }}
                        aria-disabled={paying === inv.id}
                        className="px-3 py-1.5 bg-brand-green text-white rounded-lg hover:opacity-90 transition-all whitespace-nowrap"
                      >
                        <CreditCard size={14} className="inline mr-1" /> {paying === inv.id ? 'Opening…' : 'Pay now'}
                      </a>
                    )}
                  </div>
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
