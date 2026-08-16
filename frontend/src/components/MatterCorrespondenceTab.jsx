import { useState, useEffect, useCallback } from 'react'
import { format, parseISO } from 'date-fns'
import {
  Mail,
  Download,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  ArrowDownLeft,
  ArrowUpRight,
  Settings,
  Plus,
  X,
  Check,
} from 'lucide-react'
import {
  getMatterCorrespondence,
  scanMatterCorrespondence,
  getCorrespondenceRules,
  updateCorrespondenceRules,
  matterCorrespondenceDownloadUrl,
} from '../api'

function fmtDate(value) {
  if (!value) return '—'
  try {
    return format(parseISO(value), 'MMM d, yyyy · h:mm a')
  } catch {
    return value
  }
}

function DirectionBadge({ direction }) {
  const inbound = direction === 'inbound'
  const cls = inbound
    ? 'bg-blue-100 text-blue-800 border-blue-200'
    : 'bg-brand-bg-soft text-brand-ink-2 border-brand-line'
  const Icon = inbound ? ArrowDownLeft : ArrowUpRight
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}
    >
      <Icon size={11} /> {inbound ? 'In' : 'Out'}
    </span>
  )
}

function participantSummary(p) {
  if (!p) return { from: '—', to: '—' }
  const to = Array.isArray(p.to) ? p.to.join(', ') : p.to || '—'
  return { from: p.from || '—', to: to || '—' }
}

function groupByThread(items) {
  const groups = new Map()
  for (const it of items) {
    const key = it.thread_ref || `single:${it.id}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(it)
  }
  // Sort messages within a thread oldest→newest; order groups by most recent.
  const out = []
  for (const [key, msgs] of groups.entries()) {
    msgs.sort((a, b) => new Date(a.occurred_at) - new Date(b.occurred_at))
    const latest = msgs[msgs.length - 1]?.occurred_at
    out.push({ key, msgs, latest })
  }
  out.sort((a, b) => new Date(b.latest) - new Date(a.latest))
  return out
}

function CaptureRulesPanel({ matterId, onClose }) {
  const [rules, setRules] = useState(null)
  const [caseInput, setCaseInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    getCorrespondenceRules(matterId)
      .then(setRules)
      .catch(() => setRules({ enabled: false, match_parties: true, case_numbers: [] }))
  }, [matterId])

  if (!rules) {
    return (
      <div className="p-6 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const addCase = () => {
    const v = caseInput.trim()
    if (!v) return
    if (!(rules.case_numbers || []).includes(v)) {
      setRules({ ...rules, case_numbers: [...(rules.case_numbers || []), v] })
    }
    setCaseInput('')
  }

  const removeCase = (v) =>
    setRules({ ...rules, case_numbers: (rules.case_numbers || []).filter((c) => c !== v) })

  const save = async () => {
    setSaving(true)
    setToast(null)
    try {
      const updated = await updateCorrespondenceRules(matterId, rules)
      setRules(updated)
      setToast({ type: 'ok', msg: 'Capture rules saved' })
    } catch {
      setToast({ type: 'err', msg: 'Failed to save rules' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border border-brand-line rounded-xl p-5 bg-brand-bg-soft/30 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-sans font-semibold text-brand-ink flex items-center gap-2">
          <Settings size={16} /> Capture rules
        </h3>
        <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">
          <X size={18} />
        </button>
      </div>

      <p className="text-sm text-brand-muted">
        Choose which emails are archived to this matter. Emails are captured when a listed
        party is involved, or when a case number below appears in the subject or preview.
      </p>

      <label className="flex items-center gap-3 text-sm font-sans text-brand-ink">
        <input
          type="checkbox"
          checked={!!rules.enabled}
          onChange={(e) => setRules({ ...rules, enabled: e.target.checked })}
        />
        Enable automatic background capture for this matter
      </label>

      <label className="flex items-center gap-3 text-sm font-sans text-brand-ink">
        <input
          type="checkbox"
          checked={rules.match_parties !== false}
          onChange={(e) => setRules({ ...rules, match_parties: e.target.checked })}
        />
        Capture emails involving the matter's parties (client, attorney, etc.)
      </label>

      <div>
        <div className="text-sm font-sans font-medium text-brand-ink mb-2">Case / court numbers</div>
        <div className="flex flex-wrap gap-2 mb-2">
          {(rules.case_numbers || []).length === 0 && (
            <span className="text-sm text-brand-muted">None — seeded from the matter's case number.</span>
          )}
          {(rules.case_numbers || []).map((c) => (
            <span
              key={c}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-brand-surface border border-brand-line text-sm text-brand-ink-2"
            >
              {c}
              <button onClick={() => removeCase(c)} className="text-brand-muted hover:text-brand-rose">
                <X size={13} />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={caseInput}
            onChange={(e) => setCaseInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addCase())}
            placeholder="e.g. 2024-CV-1234"
            className="flex-1 px-3 py-2 border border-brand-line rounded-lg text-sm bg-brand-surface"
          />
          <button
            onClick={addCase}
            className="px-3 py-2 bg-brand-bg-soft border border-brand-line rounded-lg text-sm font-medium text-brand-ink hover:bg-brand-line/40 inline-flex items-center gap-1"
          >
            <Plus size={14} /> Add
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-60 inline-flex items-center gap-2"
        >
          {saving ? (
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
          ) : (
            <Check size={15} />
          )}
          Save rules
        </button>
        {toast && (
          <span className={`text-sm ${toast.type === 'ok' ? 'text-brand-green' : 'text-brand-rose'}`}>
            {toast.msg}
          </span>
        )}
      </div>
    </div>
  )
}

export default function MatterCorrespondenceTab({ matterId }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [provider, setProvider] = useState('microsoft')
  const [directionFilter, setDirectionFilter] = useState('')
  const [participant, setParticipant] = useState('')
  const [showRules, setShowRules] = useState(false)
  const [expanded, setExpanded] = useState({})
  const [toast, setToast] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    const params = {}
    if (directionFilter) params.direction = directionFilter
    if (participant.trim()) params.participant = participant.trim()
    getMatterCorrespondence(matterId, params)
      .then((data) => setItems(Array.isArray(data?.items) ? data.items : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [matterId, directionFilter, participant])

  useEffect(() => {
    load()
  }, [load])

  const scan = async () => {
    setScanning(true)
    setToast(null)
    try {
      const res = await scanMatterCorrespondence(matterId, provider)
      setToast({
        type: 'ok',
        msg: `Scanned ${res.scanned} email(s) — captured ${res.captured} new, ${res.skipped} already on file.`,
      })
      load()
    } catch (e) {
      const detail = e?.response?.data?.detail || 'Scan failed — check the mailbox connection.'
      setToast({ type: 'err', msg: detail })
    } finally {
      setScanning(false)
    }
  }

  const groups = groupByThread(items)
  const toggle = (key) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
            <Mail size={20} /> Correspondence
          </h2>
          <p className="text-sm text-brand-muted mt-1">
            Archived email correspondence for this matter — full messages saved as .eml.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-brand-surface"
          >
            <option value="microsoft">Outlook / 365</option>
            <option value="google">Gmail</option>
          </select>
          <button
            onClick={scan}
            disabled={scanning}
            className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-60 inline-flex items-center gap-2"
          >
            {scanning ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <RefreshCw size={15} />
            )}
            Scan now
          </button>
          <button
            onClick={() => setShowRules((v) => !v)}
            className="px-3 py-2 bg-brand-surface border border-brand-line text-sm font-medium text-brand-ink rounded-lg hover:bg-brand-bg-soft inline-flex items-center gap-2"
          >
            <Settings size={15} /> Rules
          </button>
        </div>
      </div>

      <div className="p-6 space-y-5">
        {toast && (
          <div
            className={`text-sm px-4 py-2 rounded-lg border ${
              toast.type === 'ok'
                ? 'bg-brand-bg-soft border-brand-line text-brand-ink-2'
                : 'bg-rose-50 border-rose-200 text-brand-rose'
            }`}
          >
            {toast.msg}
          </div>
        )}

        {showRules && <CaptureRulesPanel matterId={matterId} onClose={() => setShowRules(false)} />}

        {/* Filters */}
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={directionFilter}
            onChange={(e) => setDirectionFilter(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-brand-surface"
          >
            <option value="">All directions</option>
            <option value="inbound">Inbound</option>
            <option value="outbound">Outbound</option>
          </select>
          <input
            value={participant}
            onChange={(e) => setParticipant(e.target.value)}
            placeholder="Filter by sender / recipient…"
            className="flex-1 min-w-[220px] px-3 py-2 border border-brand-line rounded-lg text-sm bg-brand-surface"
          />
        </div>

        {loading ? (
          <div className="py-12 flex items-center justify-center">
            <div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="py-12 text-center text-brand-muted">
            <Mail size={32} className="mx-auto mb-3 opacity-40" />
            <p className="font-sans">No correspondence captured yet.</p>
            <p className="text-sm mt-1">
              Configure capture rules, then click <strong>Scan now</strong> to archive matching emails.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {groups.map((group) => {
              const isThread = group.msgs.length > 1
              const isOpen = expanded[group.key]
              const head = group.msgs[group.msgs.length - 1]
              const visible = isThread && !isOpen ? [head] : group.msgs
              return (
                <div key={group.key} className="border border-brand-line rounded-xl overflow-hidden">
                  {isThread && (
                    <button
                      onClick={() => toggle(group.key)}
                      className="w-full px-4 py-2 bg-brand-bg-soft/50 flex items-center gap-2 text-sm font-sans font-medium text-brand-ink-2 hover:bg-brand-bg-soft"
                    >
                      {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      Conversation · {group.msgs.length} messages
                      <span className="font-normal text-brand-muted truncate">— {head.subject}</span>
                    </button>
                  )}
                  {visible.map((it) => {
                    const who = participantSummary(it.participants)
                    return (
                      <div
                        key={it.id}
                        className="px-4 py-3 border-t border-brand-line first:border-t-0 flex items-start gap-3"
                      >
                        <div className="pt-0.5">
                          <DirectionBadge direction={it.direction} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between gap-2">
                            <p className="font-sans font-semibold text-brand-ink truncate">
                              {it.subject || '(no subject)'}
                            </p>
                            <span className="text-xs text-brand-muted whitespace-nowrap">
                              {fmtDate(it.occurred_at)}
                            </span>
                          </div>
                          <p className="text-xs text-brand-muted mt-0.5 truncate">
                            <span className="font-medium">{who.from}</span> → {who.to}
                          </p>
                          {(it.summary || it.body) && (
                            <p className="text-sm text-brand-ink-2 mt-1 line-clamp-2">
                              {it.summary || it.body}
                            </p>
                          )}
                        </div>
                        {it.has_attachment && (
                          <a
                            href={matterCorrespondenceDownloadUrl(matterId, it.id)}
                            className="shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-brand-ink border border-brand-line rounded-lg hover:bg-brand-bg-soft"
                          >
                            <Download size={13} /> .eml
                          </a>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
