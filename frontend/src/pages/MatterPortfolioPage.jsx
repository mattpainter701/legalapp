import { useState, useEffect, useMemo } from 'react'
import { reportError } from '../utils/reportError'
import { Link, useNavigate } from 'react-router-dom'
import { format, parseISO, differenceInDays } from 'date-fns'
import { getMattersV2, getMyMatters, setAssignmentActive } from '../api'
import NewMatterModal from '../components/NewMatterModal'
import { TableSkeleton } from '../components/LoadingSkeleton'
import { AlertBanner, EmptyState, Spinner } from '../components/ui'

function Icon({ d, size = 16, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={d} />
    </svg>
  )
}
const Icons = {
  plus: 'M12 5v14M5 12h14',
  search: 'M21 21l-6-6m2-5a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  filter: 'M22 3H2l8 9.46V19l4 2v-8.54L22 3z',
  briefcase: 'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
  clock: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zm0-14v4l3 3',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  check: 'M20 6L9 17l-5-5',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  alert: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  folder: 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
}

const STATUS_COLORS = {
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  active: 'bg-green-50 text-green-700 border-green-200',
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  threatened: 'bg-amber-50 text-amber-700 border-amber-200',
  closed: 'bg-gray-100 text-gray-500 border-gray-200',
  settled: 'bg-gray-100 text-gray-500 border-gray-200',
  dismissed: 'bg-gray-100 text-gray-500 border-gray-200',
}

function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status?.toLowerCase()] || 'bg-gray-100 text-gray-500 border-gray-200'
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cls}`}>
      {status || '—'}
    </span>
  )
}

function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-red-50 text-red-700 border-red-200',
    high: 'bg-orange-50 text-orange-700 border-orange-200',
    medium: 'bg-amber-50 text-amber-700 border-amber-200',
    low: 'bg-green-50 text-green-700 border-green-200',
  }[level?.toLowerCase()] || null
  if (!cfg) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wide border ${cfg}`}>
      {level}
    </span>
  )
}

function DeadlineBadge({ label }) {
  if (!label) return null
  const isOverdue = label.includes('overdue')
  const isToday = label === 'Due today'
  const isSoon = label.startsWith('Due in')
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold border ${
      isOverdue ? 'bg-red-50 text-red-700 border-red-200' :
      isToday ? 'bg-amber-50 text-amber-700 border-amber-200' :
      isSoon ? 'bg-blue-50 text-blue-700 border-blue-200' :
      'bg-gray-100 text-gray-500 border-gray-200'
    }`}>
      <Icon d={Icons.clock} size={11} />
      {label}
    </span>
  )
}

function CloudFolderLinks({ cloudFolder, compact = false }) {
  const primaryLinks = [
    { key: 'onedrive', label: 'OneDrive', className: 'text-blue-700 bg-blue-50 border-blue-200 hover:bg-blue-100' },
    { key: 'google_drive', label: 'Google Drive', className: 'text-green-700 bg-green-50 border-green-200 hover:bg-green-100' },
  ]
    .map((cfg) => ({ ...cfg, data: cloudFolder?.[cfg.key] }))
    .filter(({ data }) => data?.url)
    .map(({ key, label, className, data }) => ({
      key,
      label: compact ? label : `${label}${data.folder_name ? `: ${data.folder_name}` : ''}`,
      title: data.folder_name ? `Open ${data.folder_name}` : `Open ${label} folder`,
      className,
      url: data.url,
    }))

  const contextLinks = (Array.isArray(cloudFolder?.context_folders) ? cloudFolder.context_folders : [])
    .filter((folder) => folder?.url)
    .map((folder) => {
      const providerLabel = folder.provider === 'onedrive' ? 'OneDrive' : 'Google Drive'
      const name = folder.label || folder.folder_name || 'Context'
      return {
        key: folder.id || `${folder.provider}:${folder.matter_folder_id}`,
        label: compact ? name : `Context: ${name}`,
        title: `Open ${folder.folder_name || name} (${providerLabel})`,
        className: 'text-amber-700 bg-amber-50 border-amber-200 hover:bg-amber-100',
        url: folder.url,
      }
    })

  const links = [...primaryLinks, ...contextLinks]

  if (links.length === 0) return null

  return (
    <div className="flex flex-wrap gap-1.5">
      {links.map(({ key, label, title, className, url }) => (
        <a
          key={key}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          title={title}
          className={`inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1 px-2 py-0.5 rounded border text-[11px] font-sans font-semibold transition-colors ${className}`}
        >
          <Icon d={Icons.folder} size={11} />
          {label}
        </a>
      ))}
    </div>
  )
}

function hasCloudFolderLinks(cloudFolder) {
  return Boolean(
    cloudFolder?.onedrive?.url ||
    cloudFolder?.google_drive?.url ||
    (Array.isArray(cloudFolder?.context_folders) && cloudFolder.context_folders.some((folder) => folder?.url))
  )
}

const STATUS_OPTIONS = ['all', 'open', 'active', 'pending', 'closed']

// ── "Needs Action" classification ─────────────────────────────────────────────
function needsAction(m) {
  if (m.risk_level === 'critical' || m.risk_level === 'high') return true
  if (m.status === 'threatened') return true
  if (m.overdue_deadline_label && m.overdue_deadline_label.toLowerCase().includes('overdue')) return true
  if (m.overdue_deadline_label && m.overdue_deadline_label.toLowerCase().includes('due today')) return true
  if (m.updated_at) {
    try {
      const daysStale = differenceInDays(new Date(), parseISO(m.updated_at))
      if (daysStale > 14 && (m.status === 'open' || m.status === 'active')) return true
    } catch { /* ignore */ }
  }
  return false
}

// Matters due tomorrow (shown as "Upcoming")
function dueTomorrow(m) {
  if (m.overdue_deadline_label && m.overdue_deadline_label.toLowerCase().includes('due tomorrow')) return true
  return false
}

// ── Matter Card (board view) ──────────────────────────────────────────────────
export function MatterCard({ m, onToggleActive, togglingId, showAlert }) {
  const isToggling = togglingId === m.my_assignment_id
  return (
    <div
      className={`bg-brand-surface border rounded-2xl p-4 hover:border-brand-accent/30 hover:shadow-md transition-all group ${
        showAlert ? 'border-brand-rose/30' : 'border-brand-line'
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0 flex-1">
          <Link
            to={`/matters/${m.id}`}
            className="flex min-h-[44px] w-full items-center truncate rounded-sm font-sans text-[14px] font-semibold leading-snug text-brand-ink transition-colors hover:text-brand-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
          >
            {m.matter_name}
          </Link>
          {m.client_name && (
            <div className="text-[12px] text-brand-muted font-sans mt-0.5 truncate">{m.client_name}</div>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {showAlert && <Icon d={Icons.alert} size={15} className="text-brand-rose" />}
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-3">
        <StatusBadge status={m.status} />
        <RiskBadge level={m.risk_level} />
        {m.overdue_deadline_label && <DeadlineBadge label={m.overdue_deadline_label} />}
      </div>

      {m.practice_area && (
        <div className="text-[12px] text-brand-accent font-semibold font-sans mb-2">{m.practice_area}</div>
      )}

      <div className="mb-2">
        <CloudFolderLinks cloudFolder={m.cloud_folder} compact />
      </div>

      {m.active_workers?.length > 0 && (
        <div className="flex items-center gap-1.5 mb-2">
          <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
          <span className="text-[11px] text-brand-muted font-sans truncate">
            {m.active_workers.slice(0, 2).join(', ')} working
          </span>
        </div>
      )}

      {m.my_assignment_id && (
        <div className="mt-3 pt-3 border-t border-brand-line">
          <button
            type="button"
            onClick={() => onToggleActive(m.my_assignment_id, m.id, !m.is_active_working)}
            disabled={isToggling}
            className={`w-full min-h-[44px] min-w-[44px] flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-semibold border transition-all ${
              m.is_active_working
                ? 'bg-green-50 text-green-700 border-green-200 hover:bg-green-100'
                : 'bg-brand-bg-soft text-brand-muted border-brand-line hover:text-brand-ink hover:border-brand-line-2'
            } ${isToggling ? 'opacity-50 cursor-wait' : ''}`}
          >
            <Icon d={Icons.activity} size={12} />
            {m.is_active_working ? 'Active' : 'Set Active'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── My Matters list row ───────────────────────────────────────────────────────
export function MyMatterRow({ m, onToggleActive, togglingId }) {
  const isToggling = togglingId === m.my_assignment_id
  return (
    <div
      className="flex items-start gap-4 px-5 py-4 hover:bg-brand-bg-soft transition-colors group border-b border-brand-line last:border-0"
    >
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <Link
            to={`/matters/${m.id}`}
            className="inline-flex min-h-[44px] min-w-[44px] max-w-full items-center truncate rounded-sm font-sans text-[14px] font-semibold text-brand-ink hover:text-brand-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
          >
            {m.matter_name}
          </Link>
          <StatusBadge status={m.status} />
          <RiskBadge level={m.risk_level} />
          <DeadlineBadge label={m.overdue_deadline_label} />
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[12px] text-brand-muted font-sans">
          {m.client_name && <span>Client: <span className="text-brand-ink-2 font-medium">{m.client_name}</span></span>}
          {m.attorney_of_record_name && <span>Attorney: <span className="text-brand-ink-2 font-medium">{m.attorney_of_record_name}</span></span>}
          {m.practice_area && <span className="text-brand-accent font-medium">{m.practice_area}</span>}
          <span className="capitalize text-brand-muted">Role: <span className="text-brand-ink-2">{m.my_role?.replace(/_/g, ' ')}</span></span>
        </div>
        <div className="mt-2">
          <CloudFolderLinks cloudFolder={m.cloud_folder} />
        </div>
        {m.active_workers?.length > 0 && (
          <div className="mt-1 flex items-center gap-1 text-[11px] text-brand-muted font-sans">
            <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
            <span>{m.active_workers.join(', ')} {m.active_workers.length === 1 ? 'is' : 'are'} working on this</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={() => onToggleActive(m.my_assignment_id, m.id, !m.is_active_working)}
          disabled={isToggling}
          className={`flex min-h-[44px] min-w-[44px] items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-semibold border transition-all ${
            m.is_active_working
              ? 'bg-green-50 text-green-700 border-green-200 hover:bg-green-100'
              : 'bg-brand-bg-soft text-brand-muted border-brand-line hover:text-brand-ink hover:border-brand-line-2'
          } ${isToggling ? 'opacity-50 cursor-wait' : ''}`}
        >
          {m.is_active_working
            ? <><Icon d={Icons.activity} size={12} className="text-green-600" /> Active</>
            : <><Icon d={Icons.activity} size={12} /> Set Active</>
          }
        </button>
        <span className="text-brand-accent font-sans text-sm font-semibold opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
      </div>
    </div>
  )
}

export function MatterPortfolioRow({ matter: m }) {
  return (
    <tr className="group transition-colors hover:bg-brand-bg-soft">
      <td className="max-w-xs px-5 py-0 pl-6">
        <Link
          to={`/matters/${m.id}`}
          className="flex min-h-[44px] min-w-[44px] items-center truncate rounded-sm font-sans text-[14px] font-semibold text-brand-ink hover:text-brand-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
        >
          {m.matter_name || '—'}
        </Link>
        {m.description && (
          <div className="mt-0.5 truncate font-sans text-[12px] text-brand-muted">{m.description}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-5 py-4 font-sans text-[13px] text-brand-ink-2">
        {m.client_name || <span className="text-brand-muted">—</span>}
      </td>
      <td className="whitespace-nowrap px-5 py-4 font-sans text-[13px] text-brand-ink-2">
        {m.attorney_of_record_name || <span className="text-brand-muted">—</span>}
      </td>
      <td className="whitespace-nowrap px-5 py-4 font-sans text-[13px] text-brand-ink-2">
        {m.practice_area || <span className="text-brand-muted">—</span>}
      </td>
      <td className="min-w-[180px] px-5 py-4">
        <CloudFolderLinks cloudFolder={m.cloud_folder} compact />
        {!hasCloudFolderLinks(m.cloud_folder) && (
          <span className="font-sans text-[13px] text-brand-muted">—</span>
        )}
      </td>
      <td className="px-5 py-4"><RiskBadge level={m.risk_level} /></td>
      <td className="px-5 py-4"><StatusBadge status={m.status} /></td>
      <td className="whitespace-nowrap px-5 py-4 font-sans text-[13px] text-brand-muted">
        {m.created_at ? (() => { try { return format(parseISO(m.created_at), 'MMM d, yyyy') } catch { return '—' } })() : '—'}
      </td>
      <td className="px-5 py-4 pr-6 text-right">
        <span className="font-sans text-sm font-semibold text-brand-accent opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">View →</span>
      </td>
    </tr>
  )
}

export default function MatterPortfolioPage() {
  const navigate = useNavigate()
  const [myMatters, setMyMatters] = useState([])
  const [myLoading, setMyLoading] = useState(true)
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [practiceFilter, setPracticeFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [togglingId, setTogglingId] = useState(null)
  const [viewMode, setViewMode] = useState('board') // 'board' | 'list'

  const loadMyMatters = () => {
    setMyLoading(true)
    getMyMatters()
      .then(data => setMyMatters(Array.isArray(data) ? data : []))
      .catch(() => {})
      .finally(() => setMyLoading(false))
  }

  const loadMatters = () => {
    setLoading(true)
    getMattersV2({ page_size: 100 })
      .then(data => setMatters(data.items || []))
      .catch(err => { setError('Failed to load matters.'); reportError(err) })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadMyMatters()
    loadMatters()
  }, [])

  const handleToggleActive = async (assignmentId, matterId, active) => {
    setTogglingId(assignmentId)
    try {
      await setAssignmentActive(matterId, assignmentId, active)
      setMyMatters(prev => prev.map(m =>
        m.my_assignment_id === assignmentId ? { ...m, is_active_working: active } : m
      ))
    } catch { /* silent */ }
    finally { setTogglingId(null) }
  }

  const practiceAreas = useMemo(() => {
    const set = new Set(matters.map(m => m.practice_area).filter(Boolean))
    return [...set].sort()
  }, [matters])

  const stats = useMemo(() => ({
    total: matters.length,
    open: matters.filter(m => m.status === 'open').length,
    active: matters.filter(m => m.status === 'active').length,
    pending: matters.filter(m => m.status === 'pending').length,
    closed: matters.filter(m => m.status === 'closed').length,
    critical: matters.filter(m => m.risk_level?.toLowerCase() === 'critical').length,
  }), [matters])

  const filtered = useMemo(() => matters.filter(m => {
    if (statusFilter !== 'all' && m.status?.toLowerCase() !== statusFilter) return false
    if (practiceFilter !== 'all' && m.practice_area !== practiceFilter) return false
    if (search) {
      const q = search.toLowerCase()
      return (
        m.matter_name?.toLowerCase().includes(q) ||
        m.client_name?.toLowerCase().includes(q) ||
        m.attorney_of_record_name?.toLowerCase().includes(q) ||
        m.practice_area?.toLowerCase().includes(q) ||
        m.description?.toLowerCase().includes(q)
      )
    }
    return true
  }), [matters, statusFilter, practiceFilter, search])

  // Board columns (from myMatters)
  const boardColumns = useMemo(() => {
    const active = myMatters.filter(m => !['closed', 'settled', 'dismissed'].includes(m.status))
    const needsActionList = active.filter(m => needsAction(m))
    const upcomingList = active.filter(m => !needsAction(m) && dueTomorrow(m))
    const skipIds = new Set([...needsActionList, ...upcomingList].map(m => m.id))
    const activeList = active.filter(m => !skipIds.has(m.id) && (m.status === 'active' || m.is_active_working))
    const activeIds = new Set(activeList.map(m => m.id))
    const watchingList = active.filter(m => !skipIds.has(m.id) && !activeIds.has(m.id))
    return { needsAction: needsActionList, upcoming: upcomingList, active: activeList, watching: watchingList }
  }, [myMatters])

  return (
    <div>
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-4 md:px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <Icon d={Icons.briefcase} size={18} className="text-brand-accent" />
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Matters</span>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
        >
          <Icon d={Icons.plus} size={15} />
          New Matter
        </button>
      </div>

      <div className="max-w-[1400px] mx-auto px-4 md:px-8 py-6 md:py-10">

        {/* ── My Matters ─────────────────────────────────────────────────────── */}
        <div className="mb-12">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="font-serif font-bold text-2xl text-brand-ink">My Matters</h2>
              {boardColumns.needsAction.length > 0 && (
                <p className="text-[13px] text-brand-rose font-sans mt-0.5 font-medium">
                  {boardColumns.needsAction.length} matter{boardColumns.needsAction.length !== 1 ? 's' : ''} need attention
                  {boardColumns.upcoming.length > 0 && (
                    <span className="text-brand-amber ml-2">
                      · {boardColumns.upcoming.length} due tomorrow
                    </span>
                  )}
                </p>
              )}
              {boardColumns.needsAction.length === 0 && boardColumns.upcoming.length > 0 && (
                <p className="text-[13px] text-brand-amber font-sans mt-0.5 font-medium">
                  {boardColumns.upcoming.length} matter{boardColumns.upcoming.length !== 1 ? 's' : ''} due tomorrow
                </p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={() => navigate('/calendar')}
                className="flex items-center gap-1.5 px-3 py-2 text-[12px] font-semibold text-brand-muted border border-brand-line rounded-lg hover:text-brand-ink hover:border-brand-line-2 transition-colors bg-brand-surface"
              >
                <Icon d={Icons.clock} size={13} />
                Deadline Calendar
              </button>
              <span className="text-[13px] text-brand-muted font-sans">
                {myMatters.length} assigned to you
              </span>
              {/* View toggle */}
              <div className="flex rounded-xl border border-brand-line overflow-hidden text-[12px] font-semibold font-sans bg-brand-surface">
                <button
                  onClick={() => setViewMode('board')}
                  className={`flex items-center gap-1.5 px-3 py-2 transition-colors ${viewMode === 'board' ? 'bg-brand-ink text-white' : 'text-brand-muted hover:text-brand-ink'}`}
                  title="Board view"
                >
                  <Icon d={Icons.grid} size={13} /> Board
                </button>
                <button
                  onClick={() => setViewMode('list')}
                  className={`flex items-center gap-1.5 px-3 py-2 transition-colors ${viewMode === 'list' ? 'bg-brand-ink text-white' : 'text-brand-muted hover:text-brand-ink'}`}
                  title="List view"
                >
                  <Icon d={Icons.list} size={13} /> List
                </button>
              </div>
            </div>
          </div>

          {myLoading ? (
            <div className="bg-brand-surface border border-brand-line rounded-xl">
              <Spinner />
            </div>
          ) : myMatters.length === 0 ? (
            <EmptyState
              visual={<Icon d={Icons.briefcase} size={22} />}
              title="No assigned matters"
              actionLabel="Open New Matter"
              onAction={() => setShowCreate(true)}
            >
              Matters assigned to you will appear here with deadlines, risk level, and active-work status.
            </EmptyState>
          ) : viewMode === 'board' ? (
            /* Board view */
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {[
                {
                  title: 'Needs Action',
                  items: boardColumns.needsAction,
                  color: 'border-brand-rose/30',
                  headerColor: 'text-brand-rose',
                  showAlert: true,
                  empty: 'No matters need attention.',
                },
                {
                  title: 'Upcoming',
                  items: boardColumns.upcoming,
                  color: 'border-brand-amber/30',
                  headerColor: 'text-brand-amber',
                  showAlert: false,
                  empty: 'No upcoming deadlines.',
                },
                {
                  title: 'Active',
                  items: boardColumns.active,
                  color: 'border-brand-green/20',
                  headerColor: 'text-brand-green',
                  showAlert: false,
                  empty: 'No actively worked matters.',
                },
                {
                  title: 'Watching',
                  items: boardColumns.watching,
                  color: 'border-brand-line',
                  headerColor: 'text-brand-ink',
                  showAlert: false,
                  empty: 'Nothing in the watch queue.',
                },
              ].map(col => (
                <div key={col.title} className={`bg-brand-bg-soft border ${col.color} rounded-2xl`}>
                  <div className="px-4 pt-4 pb-3 border-b border-brand-line/50 flex items-center justify-between">
                    <h3 className={`font-serif font-bold text-[15px] ${col.headerColor}`}>{col.title}</h3>
                    <span className="text-[12px] text-brand-muted font-sans">{col.items.length}</span>
                  </div>
                  <div className="p-3 space-y-2 min-h-[120px]">
                    {col.items.length === 0 ? (
                      <p className="text-brand-muted text-[12px] font-sans text-center py-6">{col.empty}</p>
                    ) : (
                      col.items.map(m => (
                        <MatterCard
                          key={m.id}
                          m={m}
                          onToggleActive={handleToggleActive}
                          togglingId={togglingId}
                          showAlert={col.showAlert}
                        />
                      ))
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* List view */
            <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
              {myMatters.map(m => (
                <MyMatterRow
                  key={m.id}
                  m={m}
                  onToggleActive={handleToggleActive}
                  togglingId={togglingId}
                />
              ))}
            </div>
          )}
        </div>

        {/* ── Portfolio Header ────────────────────────────────────────────────── */}
        <div className="mb-6">
          <h2 className="font-serif font-bold text-2xl text-brand-ink mb-1">All Matters</h2>
          <p className="text-brand-ink-2 text-[14px] font-sans">
            {matters.length} matter{matters.length !== 1 ? 's' : ''} in portfolio
          </p>
        </div>

        {error && (
          <AlertBanner
            type="error"
            title="Matters could not be loaded"
            actionLabel="Retry"
            onAction={loadMatters}
            className="mb-6"
          >
            {error}
          </AlertBanner>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-8">
          {[
            { label: 'Total', value: stats.total, dot: 'bg-brand-ink' },
            { label: 'Open', value: stats.open, dot: 'bg-blue-500' },
            { label: 'Active', value: stats.active, dot: 'bg-green-500' },
            { label: 'Pending', value: stats.pending, dot: 'bg-amber-500' },
            { label: 'Closed', value: stats.closed, dot: 'bg-gray-400' },
            { label: 'Critical Risk', value: stats.critical, dot: 'bg-red-500' },
          ].map((s, i) => (
            <div key={i} className="bg-brand-surface border border-brand-line rounded-2xl p-5 hover:border-brand-line-2 transition-colors">
              <div className="flex items-center gap-2 mb-3">
                <div className={`w-2 h-2 rounded-full ${s.dot}`} />
              </div>
              <p className="text-3xl font-bold font-serif text-brand-ink tracking-tight mb-1">{s.value}</p>
              <p className="text-xs text-brand-ink-2 font-sans font-medium">{s.label}</p>
            </div>
          ))}
        </div>

        {/* Toolbar */}
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 mb-6 flex flex-wrap gap-4 items-center shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1">
              <Icon d={Icons.filter} size={14} className="text-brand-muted" />
              <select
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
                className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
              >
                {STATUS_OPTIONS.map(s => (
                  <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>

            {practiceAreas.length > 0 && (
              <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1">
                <Icon d={Icons.filter} size={14} className="text-brand-muted" />
                <select
                  value={practiceFilter}
                  onChange={e => setPracticeFilter(e.target.value)}
                  className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
                >
                  <option value="all">All Areas</option>
                  {practiceAreas.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
            )}
          </div>

          <div className="flex-1 min-w-64 relative">
            <Icon d={Icons.search} size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search by name, client, attorney, description..."
              className="w-full bg-brand-surface border border-brand-line rounded-lg pl-11 pr-4 py-2.5 text-sm font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted transition-all"
            />
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <TableSkeleton rows={8} columns={6} ariaLabel="Loading matters" />
        ) : filtered.length === 0 ? (
          <EmptyState
            visual={<Icon d={Icons.briefcase} size={24} />}
            title={search || statusFilter !== 'all' || practiceFilter !== 'all' ? 'No matters match these filters' : 'No matters yet'}
            actionLabel="Open New Matter"
            onAction={() => setShowCreate(true)}
            secondaryActionLabel={search || statusFilter !== 'all' || practiceFilter !== 'all' ? 'Clear Filters' : undefined}
            onSecondaryAction={() => {
              setSearch('')
              setStatusFilter('all')
              setPracticeFilter('all')
            }}
            className="py-14"
          >
            {search || statusFilter !== 'all' || practiceFilter !== 'all'
              ? 'Try clearing filters or searching by client, attorney, practice area, or matter name.'
              : 'Create the first matter to begin tracking status, risk, deadlines, and cloud folders.'}
          </EmptyState>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {['Matter', 'Client', 'Attorney', 'Practice Area', 'Cloud Folder', 'Risk', 'Status', 'Opened', ''].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 8 ? 'pr-6' : ''}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map(m => (
                    <MatterPortfolioRow
                      key={m.id}
                      matter={m}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <NewMatterModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onImportComplete={() => { loadMatters(); loadMyMatters() }}
        onCreated={m => {
          setMatters(prev => [m, ...prev])
          navigate(`/matters/${m.id}`)
        }}
      />
    </div>
  )
}
