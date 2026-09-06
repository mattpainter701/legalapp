import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../App'
import { format, parseISO, differenceInDays } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import {
  getMatterV2, updateMatterV2, getMatterTimeline, addMatterNote,
  getMatterBudgetV2, getMatterAssignments, addMatterAssignment,
  removeMatterAssignment, updateMatterMemory,
  getPlugins, getCommunications, createCommunication,
  setAssignmentActive, getMatterTimeEntries, getConversations, createConversation,
  getTasks, updateTask, getMatterDashboard, getMatterCloudFiles,
  createMatterPortalInvite, listMatterPortalInvites, revokeMatterPortalInvite,
  getMatterDocuments, createSignatureRequest, listSignatureRequests,
  sendSignatureRequest, resendSignatureRequest, voidSignatureRequest, getMatterDocumentDownloadUrl,
  syncMatterCloudFolder, listTrustAccounts,
  getContacts, getAdminUsers,
} from '../api'
import MatterDocumentsTab from '../components/MatterDocumentsTab'
import MatterCorrespondenceTab from '../components/MatterCorrespondenceTab'
import MatterPartiesTab from '../components/MatterPartiesTab'
import MatterSmbSharesTab from '../components/MatterSmbSharesTab'
import AddTaskModal from '../components/AddTaskModal'
import ComposeEmailModal from '../components/ComposeEmailModal'
import UserSearchInput from '../components/UserSearchInput'
import ContactPicker from '../components/ContactPicker'
import MatterExpensesPanel from '../components/MatterExpensesPanel'
import MatterWorkflowPanel from '../components/MatterWorkflowPanel'

// ── Icons ─────────────────────────────────────────────────────────────────────
function Icon({ d, size = 18, className = '' }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className}><path d={d} /></svg>
}
const Icons = {
  back: 'M19 12H5M12 5l-7 7 7 7',
  arrowRight: 'M5 12h14M12 5l7 7-7 7',
  edit: 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
  check: 'M20 6L9 17l-5-5',
  x: 'M18 6L6 18M6 6l12 12',
  clock: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zm0-14v4l3 3',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
  brain: 'M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.46 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.46 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z',
  plus: 'M12 5v14M5 12h14',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  briefcase: 'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
  save: 'M19 21H5a2 2 0 0 0-2-2V5a2 2 0 0 0 2-2h11l5 5v11a2 2 0 0 0-2 2zM17 21v-8H7v8M7 3v5h8',
  parties: 'M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z',
  mail: 'M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6',
  dollar: 'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  messageSquare: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  folder: 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  send: 'M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z',
  refresh: 'M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6',
  chevronDown: 'M6 9l6 6 6-6',
  chevronRight: 'M9 18l6-6-6-6',
  checkCircle: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4L12 14.01l-3-3',
}

// ── Small UI pieces ───────────────────────────────────────────────────────────
export function MatterConversationLink({ conversation: conv, cloudConnected = false }) {
  return (
    <Link
      to={`/chat?conv=${conv.id}`}
      className="flex items-center gap-4 p-4 border border-brand-line rounded-xl bg-brand-bg-soft/40 hover:border-brand-accent/30 hover:bg-brand-accent/5 cursor-pointer transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
    >
      <Icon d={Icons.messageSquare} size={18} className="text-brand-muted group-hover:text-brand-accent shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-[14px] font-semibold text-brand-ink font-sans truncate">{conv.title || 'Untitled conversation'}</div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[12px] text-brand-muted font-sans">
            {conv.updated_at ? format(parseISO(conv.updated_at), 'MMM d, yyyy') : ''}
          </span>
          {cloudConnected && (
            <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-brand-accent bg-brand-accent/10 px-1.5 py-0.5 rounded">
              <Icon d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" size={10} /> Cloud
            </span>
          )}
        </div>
      </div>
      <Icon d={Icons.arrowRight} size={14} className="text-brand-muted group-hover:text-brand-accent shrink-0" />
    </Link>
  )
}

const STATUS_COLORS = {
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  pending: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  settled: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  dismissed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}
function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return <span className={`inline-flex items-center px-3 py-1 rounded-full text-[13px] font-semibold capitalize font-sans border ${cls}`}>{status || '—'}</span>
}
function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    high: 'bg-orange-100 text-orange-800 border-orange-200',
    medium: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    low: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  }[level?.toLowerCase()] || null
  if (!cfg) return null
  return <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide font-sans border ${cfg}`}>{level}</span>
}
function Field({ label, children }) {
  return (
    <div className="py-3 border-b border-brand-line/50 last:border-0">
      <dt className="text-[11px] font-bold text-brand-muted font-sans uppercase tracking-widest mb-1">{label}</dt>
      <dd className="text-[14px] font-sans text-brand-ink-2">{children || <span className="text-brand-line-2">—</span>}</dd>
    </div>
  )
}
function cloudStorageLinks(cloudFolder, compact = false) {
  const primaryLinks = [
    { key: 'onedrive', label: 'OneDrive', className: 'bg-blue-50 border-blue-200 text-blue-700 hover:bg-blue-100' },
    { key: 'google_drive', label: 'Google Drive', className: 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100' },
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
        className: 'bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100',
        url: folder.url,
      }
    })

  return [...primaryLinks, ...contextLinks]
}
function hasCloudStorageLinks(cloudFolder) {
  return cloudStorageLinks(cloudFolder, true).length > 0
}
function CloudStorageLinks({ cloudFolder, compact = false }) {
  const links = cloudStorageLinks(cloudFolder, compact)
  if (links.length === 0) return null
  return (
    <div className="flex flex-wrap gap-2">
      {links.map((link) => (
        <a
          key={link.key}
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          title={link.title}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 border text-xs font-medium rounded-lg transition-colors ${link.className}`}
        >
          <Icon d={Icons.folder} size={12} />
          {link.label}
        </a>
      ))}
    </div>
  )
}
const inputCls = "w-full border border-brand-line rounded-lg px-3 py-2.5 text-base md:text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"
const labelCls = "block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5"

const RISK_OPTIONS = ['critical', 'high', 'medium', 'low']
const STATUS_OPTIONS = ['open', 'active', 'pending', 'threatened', 'closed', 'settled', 'dismissed']
const NOTE_TYPES = ['internal', 'email', 'client', 'court']

// ── Timeline event badge ──────────────────────────────────────────────────────
const EVENT_COLORS = {
  intake: 'bg-blue-100 text-blue-800 border-blue-200',
  filing: 'bg-blue-100 text-blue-800 border-blue-200',
  hearing: 'bg-purple-100 text-purple-800 border-purple-200',
  note: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
  settlement_discussion: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  court_order: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  discovery: 'bg-orange-100 text-orange-800 border-orange-200',
}
function EntryBadge({ type }) {
  const cls = EVENT_COLORS[type] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}>{type?.replace(/_/g, ' ') || 'event'}</span>
}

// Task type badge
const TASK_TYPE_COLORS = {
  deadline: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  hearing: 'bg-purple-100 text-purple-800 border-purple-200',
  filing: 'bg-blue-100 text-blue-800 border-blue-200',
  deposition: 'bg-orange-100 text-orange-800 border-orange-200',
  review: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
  call: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  follow_up: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  general: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}
function TaskTypeBadge({ type }) {
  const cls = TASK_TYPE_COLORS[type] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}>{type?.replace(/_/g, ' ') || 'task'}</span>
}

function DueDateLabel({ dueDate }) {
  if (!dueDate) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const due = new Date(dueDate)
  due.setHours(0, 0, 0, 0)
  const diff = differenceInDays(due, today)
  if (diff < 0) return <span className="text-brand-rose text-[12px] font-semibold font-sans">{Math.abs(diff)}d overdue</span>
  if (diff === 0) return <span className="text-brand-amber text-[12px] font-semibold font-sans">Due today</span>
  if (diff <= 3) return <span className="text-brand-amber text-[12px] font-sans">Due in {diff}d</span>
  return <span className="text-brand-muted text-[12px] font-sans">{format(due, 'MMM d')}</span>
}

const KEY_DATE_TYPES = new Set(['hearing', 'filing', 'deposition', 'deadline'])

// ── Main Component ────────────────────────────────────────────────────────────
export default function MatterDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [matter, setMatter] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('dashboard')
  const noteRequest = useRef(null)
  const noteBusy = useRef(false)
  const [noteNotice, setNoteNotice] = useState(null)
  const [noteConflict, setNoteConflict] = useState(false)

  // Edit state
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // Budget
  const [budget, setBudget] = useState(null)

  // Trust accounts
  const [trustAccounts, setTrustAccounts] = useState([])

  // Dashboard
  const [dashboard, setDashboard] = useState(null)
  const [tasks, setTasks] = useState([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [tasksError, setTasksError] = useState(false)
  const [showDetailsPanel, setShowDetailsPanel] = useState(false)
  const [showAddTask, setShowAddTask] = useState(false)
  const [showCompose, setShowCompose] = useState(false)

  // Activity tab (merges timeline + communications)
  const [timeline, setTimeline] = useState([])
  const [communications, setCommunications] = useState([])
  const [activityLoading, setActivityLoading] = useState(false)
  const [activityFilter, setActivityFilter] = useState('all') // 'all' | 'timeline' | 'comms'
  const [showAddNote, setShowAddNote] = useState(false)
  const [newNote, setNewNote] = useState({ note_type: 'internal', title: '', content: '' })
  const [addingNote, setAddingNote] = useState(false)
  const noteMatterId = useRef(id)
  noteMatterId.current = id
  useEffect(() => {
    noteRequest.current = null
    noteBusy.current = false
    setAddingNote(false)
    setNoteConflict(false)
    setNoteNotice(null)
    setShowAddNote(false)
    setNewNote({ note_type: 'internal', title: '', content: '' })
  }, [id])
  const [showLogComm, setShowLogComm] = useState(false)
  const [newComm, setNewComm] = useState({ direction: 'outbound', channel: 'email', subject: '', body: '' })
  const [loggingComm, setLoggingComm] = useState(false)

  // Team
  const [assignments, setAssignments] = useState([])
  const [pluginOptions, setPluginOptions] = useState([])
  const [addingUser, setAddingUser] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')
  const [selectedUserName, setSelectedUserName] = useState('')
  const [selectedRole, setSelectedRole] = useState('associate')

  // Cloud files
  const [cloudFiles, setCloudFiles] = useState(null) // null = not loaded yet
  const [cloudSyncing, setCloudSyncing] = useState(false)
  const [cloudSyncError, setCloudSyncError] = useState(null)

  // Billing
  const [timeEntries, setTimeEntries] = useState([])
  const [billingLoading, setBillingLoading] = useState(false)

  // People section inline editing
  const [editingPeople, setEditingPeople] = useState(false)
  const [peopleData, setPeopleData] = useState({})
  const [savingPeople, setSavingPeople] = useState(false)
  const [peopleError, setPeopleError] = useState(null)
  const [contactsList, setContactsList] = useState([])
  const [usersList, setUsersList] = useState([])

  // Chat
  const [matterConvs, setMatterConvs] = useState([])
  const [convLoading, setConvLoading] = useState(false)
  const [startingConv, setStartingConv] = useState(false)

  // Settings (memory)
  const [memoryContent, setMemoryContent] = useState('')
  const [memorySaving, setMemorySaving] = useState(false)
  const [memorySaved, setMemorySaved] = useState(false)
  const [settingsSection, setSettingsSection] = useState('memory')

  const loadMatter = useCallback(async () => {
    try {
      const data = await getMatterV2(id)
      setMatter(data)
      setEditData(data)
      setMemoryContent(data.memory_content || '')
    } catch {
      setError('Failed to load matter.')
    } finally {
      setLoading(false)
    }
  }, [id])

  const loadDashboard = useCallback(async () => {
    setTasksLoading(true)
    try {
      const [dashData, taskData] = await Promise.all([
        getMatterDashboard(id).catch(() => null),
        getTasks({ matter_id: id, status: 'pending' }).then(data => { setTasksError(false); return data }).catch(() => { setTasksError(true); return { items: [] } }),
      ])
      setDashboard(dashData)
      const taskList = Array.isArray(taskData) ? taskData : taskData.items || []
      setTasks(taskList)
    } finally {
      setTasksLoading(false)
    }
  }, [id])

  const refreshBillingSummary = useCallback(async () => {
    await Promise.all([
      getMatterBudgetV2(id).then(setBudget).catch(() => {}),
      getMatterDashboard(id).then(setDashboard).catch(() => {}),
    ])
  }, [id])

  const loadCloudFiles = useCallback(async () => {
    try {
      const data = await getMatterCloudFiles(id)
      setCloudFiles(data)
    } catch {
      setCloudFiles({ connected: false, files: [] })
    }
  }, [id])

  useEffect(() => {
    loadMatter()
    getMatterBudgetV2(id).then(setBudget).catch(() => {})
    listTrustAccounts({ matter_id: id }).then(data => setTrustAccounts(data.items || [])).catch(() => {})
    getPlugins().then(data => {
      const list = Array.isArray(data) ? data : data.plugins || []
      setPluginOptions(list.filter(p => p.supports_matter_assignment !== false))
    }).catch(() => {})
    loadCloudFiles()
  }, [id, loadMatter, loadCloudFiles])

  useEffect(() => {
    if (activeTab === 'dashboard') {
      loadDashboard()
    }
    if (activeTab === 'activity') {
      setActivityLoading(true)
      Promise.all([
        getMatterTimeline(id).catch(() => []),
        getCommunications({ matter_id: id }).catch(() => []),
      ]).then(([tl, comms]) => {
        setTimeline(Array.isArray(tl) ? tl : [])
        setCommunications(Array.isArray(comms) ? comms : comms.items || [])
      }).finally(() => setActivityLoading(false))
    }
    if (activeTab === 'team') {
      getMatterAssignments(id).then(setAssignments).catch(() => {})
    }
    if (activeTab === 'billing') {
      setBillingLoading(true)
      getMatterTimeEntries(id).then(data => {
        setTimeEntries(Array.isArray(data) ? data : data.items || data.time_entries || [])
      }).catch(() => {}).finally(() => setBillingLoading(false))
    }
    if (activeTab === 'chat') {
      setConvLoading(true)
      getConversations({ matter_id: id }).then(data => {
        setMatterConvs(Array.isArray(data) ? data : [])
      }).catch(() => {}).finally(() => setConvLoading(false))
    }
  }, [activeTab, id, loadDashboard])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMatterV2(id, editData)
      setMatter(updated)
      setEditData(updated)
      setEditing(false)
      getMatterBudgetV2(id).then(setBudget).catch(() => {})
    } catch {
      setSaveError('Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  const handleMatterCloudSync = async () => {
    setCloudSyncing(true)
    setCloudSyncError(null)
    try {
      const result = await syncMatterCloudFolder(id)
      setMatter(prev => prev ? { ...prev, cloud_folder: result.providers || {} } : prev)
      setEditData(prev => ({ ...prev, cloud_folder: result.providers || {} }))
      setCloudFiles({ connected: result.connected, files: result.files || [] })
    } catch (err) {
      setCloudSyncError(err?.response?.data?.detail || 'Cloud folder sync failed.')
    } finally {
      setCloudSyncing(false)
    }
  }

  const handleAddNote = async () => {
    if (!newNote.title.trim() || noteBusy.current || noteConflict) return
    noteBusy.current = true
    setAddingNote(true)
    setNoteNotice(null)
    // Retry the same payload and identity after an uncertain network response.
    noteRequest.current ||= { ...newNote, request_id: crypto.randomUUID() }
    try {
      await addMatterNote(id, noteRequest.current)
      if (noteMatterId.current !== id) return
      noteRequest.current = null
      setNewNote({ note_type: 'internal', title: '', content: '' })
      setShowAddNote(false)
      setNoteNotice({ success: true, text: 'Note saved.' })
      try {
        const tl = await getMatterTimeline(id)
        if (noteMatterId.current !== id) return
        setTimeline(Array.isArray(tl) ? tl : [])
      } catch {
        setNoteNotice({ success: true, text: 'Note saved. Activity could not refresh; reopen Activity when connected.' })
      }
    } catch (error) {
      if (noteMatterId.current !== id) return
      const status = error?.response?.status
      if ([400, 403, 422].includes(status)) noteRequest.current = null
      setNoteConflict(status === 409)
      setNoteNotice({ success: false, text: status === 409
        ? 'This request refers to a saved note that changed or was deleted. Check Activity before deliberately starting a new note.'
        : status === 403
        ? 'You do not have permission to save this note. Your text is still here.'
        : noteRequest.current
          ? 'Save not confirmed. Your text is still here. Retry Save Note when connected; the same request will not create a duplicate.'
          : 'Note was not saved. Check your entry and try again. Your text is still here.' })
    } finally {
      if (noteMatterId.current === id) {
        noteBusy.current = false
        setAddingNote(false)
      }
    }
  }

  const handleSavePeople = async () => {
    setSavingPeople(true)
    setPeopleError(null)
    try {
      const updated = await updateMatterV2(id, peopleData)
      setMatter(updated)
      setEditData(updated)
      setEditingPeople(false)
      getMatterBudgetV2(id).then(setBudget).catch(() => {})
    } catch {
      setPeopleError('Failed to save changes.')
    } finally {
      setSavingPeople(false)
    }
  }

  const startEditingPeople = async () => {
    setPeopleData({
      client_contact_id: matter.client_contact_id || '',
      attorney_of_record_id: matter.attorney_of_record_id || '',
      partner_attorney_id: matter.partner_attorney_id || '',
      budget_amount: matter.budget_amount || null,
      budget_currency: matter.budget_currency || 'USD',
      billing_method: matter.billing_method || 'hourly',
      billing_cycle: matter.billing_cycle || 'monthly',
      hourly_rate: matter.hourly_rate || null,
    })
    setPeopleError(null)
    // Load contacts and users for pickers
    getContacts({ limit: 200, active_only: true }).then(data => {
      const list = Array.isArray(data) ? data : data.items || []
      setContactsList(list)
    }).catch(() => {})
    getAdminUsers().then(data => {
      const list = Array.isArray(data) ? data : data.users || []
      setUsersList(list)
    }).catch(() => {})
    setEditingPeople(true)
  }

  const handleAddAssignment = async () => {
    if (!selectedUserId) return
    setAddingUser(true)
    try {
      const a = await addMatterAssignment(id, { user_id: selectedUserId, role: selectedRole })
      setAssignments(prev => [...prev, a])
      setSelectedUserId('')
      setSelectedUserName('')
    } catch { /* silent */ }
    finally { setAddingUser(false) }
  }

  const handleRemoveAssignment = async (aid) => {
    try {
      await removeMatterAssignment(id, aid)
      setAssignments(prev => prev.filter(a => a.id !== aid))
    } catch { /* silent */ }
  }

  const handleToggleActive = async (assignmentId, currentlyActive) => {
    try {
      await setAssignmentActive(id, assignmentId, !currentlyActive)
      setAssignments(prev => prev.map(a => a.id === assignmentId ? { ...a, is_active_working: !currentlyActive } : a))
    } catch { /* silent */ }
  }

  const handleLogComm = async () => {
    if (!newComm.subject.trim()) return
    setLoggingComm(true)
    try {
      await createCommunication({ ...newComm, matter_id: id })
      setNewComm({ direction: 'outbound', channel: 'email', subject: '', body: '' })
      setShowLogComm(false)
      const comms = await getCommunications({ matter_id: id }).catch(() => [])
      setCommunications(Array.isArray(comms) ? comms : comms.items || [])
    } catch { /* silent */ } finally { setLoggingComm(false) }
  }

  const handleStartChat = async () => {
    setStartingConv(true)
    try {
      const conv = await createConversation({ matter_id: id, title: `Chat: ${matter.matter_name}` })
      navigate(`/chat?conv=${conv.id}`)
    } catch { /* silent */ } finally { setStartingConv(false) }
  }

  const handleSaveMemory = async () => {
    setMemorySaving(true)
    try {
      await updateMatterMemory(id, memoryContent)
      setMatter(prev => ({ ...prev, memory_content: memoryContent }))
      setMemorySaved(true)
      setTimeout(() => setMemorySaved(false), 2000)
    } catch { /* silent */ }
    finally { setMemorySaving(false) }
  }

  const handleTaskComplete = async (taskId) => {
    try {
      await updateTask(taskId, { status: 'completed' })
      setTasks(prev => prev.filter(t => t.id !== taskId))
      setDashboard(prev => prev ? { ...prev, open_tasks: Math.max(0, prev.open_tasks - 1) } : prev)
    } catch { /* silent */ }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !matter) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="text-center bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Icon d={Icons.briefcase} size={32} className="mx-auto text-brand-rose mb-4" />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Matter not found.'}</p>
          <button onClick={() => navigate('/matters')} className="bg-brand-ink text-white px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 w-full">
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const dm = editing ? editData : matter

  const tabs = [
    { key: 'dashboard', label: 'Dashboard', icon: Icons.activity },
    { key: 'activity', label: 'Activity', icon: Icons.clock },
    { key: 'team', label: 'Team', icon: Icons.users },
    { key: 'workflow', label: 'Workflow', icon: Icons.checkCircle },
    { key: 'documents', label: 'Documents', icon: Icons.file },
    { key: 'correspondence', label: 'Correspondence', icon: Icons.mail },
    { key: 'portal', label: 'Client Portal', icon: Icons.users },
    { key: 'billing', label: 'Billing', icon: Icons.dollar },
    { key: 'chat', label: 'Chat', icon: Icons.messageSquare },
    { key: 'settings', label: 'Settings', icon: Icons.settings },
  ]

  const assignedIds = new Set(assignments.map(a => a.user_id))
  const pluginLabel = (pluginName) => {
    if (!pluginName) return null
    const found = pluginOptions.find(p => (p.plugin_name || p.id) === pluginName)
    return found?.display_name || pluginName
  }
  const pluginRoute = (pluginName) => {
    if (!pluginName) return null
    const found = pluginOptions.find(p => (p.plugin_name || p.id) === pluginName)
    return found?.primary_route || `/plugins/${pluginName}`
  }

  // Split tasks for dashboard
  const keyDateTasks = tasks.filter(t => KEY_DATE_TYPES.has(t.task_type))
    .sort((a, b) => {
      if (!a.due_date) return 1
      if (!b.due_date) return -1
      return new Date(a.due_date) - new Date(b.due_date)
    })
  const todoTasks = tasks.filter(t => !KEY_DATE_TYPES.has(t.task_type))

  // Activity feed (merged + sorted)
  const activityFeed = (() => {
    const events = timeline.map(e => ({ ...e, _kind: 'timeline' }))
    const comms = communications.map(c => ({ ...c, _kind: 'comm', created_at: c.occurred_at || c.created_at }))
    return [...events, ...comms].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  })()
  const filteredActivity = activityFilter === 'all' ? activityFeed
    : activityFilter === 'timeline' ? activityFeed.filter(e => e._kind === 'timeline')
    : activityFeed.filter(e => e._kind === 'comm')

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Topbar */}
      <div className="bg-brand-surface border-b border-brand-line px-4 md:px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-2 md:gap-4 min-w-0 flex-1">
          <button aria-label="Matter Portfolio" onClick={() => navigate('/matters')} className="min-h-11 min-w-11 flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink text-sm font-sans font-medium transition-colors flex-shrink-0">
            <Icon d={Icons.back} size={16} /> <span className="hidden sm:inline">Matter Portfolio</span>
          </button>
          <div className="h-4 w-px bg-brand-line flex-shrink-0" />
          <span className="font-serif font-bold text-base md:text-lg text-brand-ink tracking-tight truncate">{matter.matter_name}</span>
        </div>
        <div className="flex gap-2 md:gap-3 flex-shrink-0">
          {editing ? (
            <>
              <button onClick={() => { setEditing(false); setEditData(matter) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-2">
                <Icon d={Icons.x} size={15} /> Cancel
              </button>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 flex items-center gap-2">
                {saving ? 'Saving…' : <><Icon d={Icons.check} size={15} /> Save</>}
              </button>
            </>
          ) : (
            <button onClick={() => { setActiveTab('settings'); setEditing(true); getContacts({ limit: 200, active_only: true }).then(data => { const list = Array.isArray(data) ? data : data.items || []; setContactsList(list) }).catch(() => {}); getAdminUsers().then(data => { const list = Array.isArray(data) ? data : data.users || []; setUsersList(list) }).catch(() => {}) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-2">
              <Icon d={Icons.edit} size={15} /> Edit
            </button>
          )}
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-4 md:px-8 py-6 md:py-10">
        {/* Hero */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-8">
          <div className="flex-1 min-w-0">
            <h1 className="font-serif text-2xl md:text-4xl break-words font-bold text-brand-ink tracking-tight mb-3 leading-tight">{matter.matter_name}</h1>
            {matter.description && (
              <p className="text-brand-ink-2 font-sans text-[15px] mb-4 leading-relaxed max-w-2xl">{matter.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge status={matter.status} />
              <RiskBadge level={matter.risk_level} />
              {matter.practice_area && (
                <span className="text-[12px] font-sans font-semibold text-brand-accent bg-brand-accent/10 px-2.5 py-1 rounded-lg border border-brand-accent/20">{matter.practice_area}</span>
              )}
              {matter.case_number && (
                <span className="text-[12px] font-sans text-brand-muted">#{matter.case_number}</span>
              )}
            </div>
            {hasCloudStorageLinks(matter.cloud_folder) && (
              <div className="flex flex-wrap items-center gap-2 mt-4">
                <span className="text-[11px] font-bold uppercase tracking-widest text-brand-muted font-sans">Cloud Folder</span>
                <CloudStorageLinks cloudFolder={matter.cloud_folder} compact />
              </div>
            )}
          </div>

          {/* Trust Balance card */}
          <div className="hidden md:block bg-brand-surface border border-brand-line rounded-2xl p-5 text-right min-w-[180px] shadow-sm">
            <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Trust Balance</div>
            {trustAccounts.length > 0 ? (
              <>
                <div className="text-[26px] font-serif font-bold text-brand-ink">
                  ${trustAccounts.reduce((sum, a) => sum + Number(a.current_balance || 0), 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <button
                  onClick={() => navigate(trustAccounts.length === 1 ? `/trust/${trustAccounts[0].id}` : `/trust?matter_id=${id}`)}
                  className="text-[12px] text-brand-accent font-sans hover:underline mt-1"
                >
                  {trustAccounts.length === 1 ? 'View trust account' : `View ${trustAccounts.length} trust accounts`}
                </button>
              </>
            ) : (
              <div className="text-[13px] text-brand-muted font-sans">No trust account</div>
            )}
          </div>

          {/* Budget card */}
          {budget && (
            <div className="hidden md:block bg-brand-surface border border-brand-line rounded-2xl p-5 text-right min-w-[180px] shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Budget used</div>
              {budget.budget_amount ? (
                <>
                  <div className="text-[26px] font-serif font-bold text-brand-ink">{budget.utilization_pct ?? 0}%</div>
                  <div className="text-[12px] text-brand-muted font-sans mb-2">
                    ${Number(budget.total_billed).toLocaleString()} used / ${Number(budget.budget_amount).toLocaleString()} budget
                  </div>
                  {(budget.billable_time_amount != null || budget.billable_expense_amount != null || budget.remaining != null) && (
                    <div className="mb-2 space-y-0.5 text-[11px] text-brand-muted">
                      {budget.billable_time_amount != null && <div>Time: ${Number(budget.billable_time_amount).toLocaleString()}</div>}
                      {budget.billable_expense_amount != null && <div>Client expenses: ${Number(budget.billable_expense_amount).toLocaleString()}</div>}
                      {budget.remaining != null && (
                        <div>
                          {Number(budget.remaining) >= 0 ? 'Remaining' : 'Over budget'}: ${Math.abs(Number(budget.remaining)).toLocaleString()}
                        </div>
                      )}
                    </div>
                  )}
                  <div className="text-[10px] text-brand-muted">Billable time and client expenses count; internal expenses do not.</div>
                  <div className="h-1.5 rounded-full bg-brand-line overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${(budget.utilization_pct ?? 0) > 90 ? 'bg-brand-rose' : (budget.utilization_pct ?? 0) > 70 ? 'bg-brand-amber' : 'bg-brand-green'}`}
                      style={{ width: `${Math.min(budget.utilization_pct ?? 0, 100)}%` }}
                    />
                  </div>
                </>
              ) : (
                <div className="text-[13px] text-brand-muted font-sans">No budget set</div>
              )}
            </div>
          )}
        </div>

        {saveError && <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-6 text-brand-rose text-sm font-sans">{saveError}</div>}

        <nav aria-label="Mobile matter casework" className="md:hidden mb-6 rounded-2xl border border-brand-line bg-brand-surface p-3">
          <p className="mb-2 text-sm text-brand-muted">Stage: {matter.stage || 'Not set'}</p>
          {activeTab === 'dashboard' && <div className="mb-3 text-sm">
            {tasksLoading ? 'Loading open tasks…' : tasksError ? 'Open tasks could not load. Use Manage tasks to retry.' : tasks.length
              ? <Link className="flex min-h-11 items-center font-semibold text-brand-accent break-words" to={`/tasks/${tasks[0].id}`}>Open task: {tasks[0].title}</Link>
              : 'No pending tasks. Manage tasks also includes reviews and waiting work.'}
          </div>}
          <label htmlFor="mobile-matter-section" className="block text-sm font-semibold mb-2">Matter section</label>
          <select id="mobile-matter-section" value={activeTab} onChange={event => setActiveTab(event.target.value)} className="w-full min-h-11 rounded-lg border border-brand-line bg-brand-surface px-3 text-base">
            {tabs.map(tab => <option key={tab.key} value={tab.key}>{tab.label}</option>)}
          </select>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {[
              ['Quick note', () => { setActiveTab('activity'); setShowAddNote(true) }],
              ['Read documents', () => setActiveTab('documents')],
              ['Manage tasks', () => navigate(`/tasks?matter_id=${id}`)],
              ['Review work', () => setActiveTab('workflow')],
              ['Contact client', () => setShowCompose(true)],
              ['Recent activity', () => setActiveTab('activity')],
            ].map(([label, action]) => <button key={label} type="button" onClick={action} className="min-h-11 rounded-lg border border-brand-line px-2 py-2 text-sm font-semibold text-brand-ink">{label}</button>)}
          </div>
        </nav>
        {noteNotice && <p role={noteNotice.success ? 'status' : 'alert'} className={`mb-4 rounded-xl border p-4 text-sm ${noteNotice.success ? 'border-brand-green text-brand-ink' : 'border-brand-rose text-brand-rose'}`}>{noteNotice.text}</p>}
        {noteConflict && <button type="button" className="min-h-11 mb-4 rounded-lg border border-brand-line px-4 text-sm" onClick={() => { noteRequest.current = null; setNoteConflict(false); setNoteNotice(null); setNewNote({ note_type: 'internal', title: '', content: '' }); setActiveTab('activity'); setShowAddNote(true) }}>Start a new blank note</button>}
        {/* Tabs */}
        <div className="hidden md:flex gap-1 mb-8 border-b border-brand-line overflow-x-auto scrollbar-none pb-px">
          {tabs.map(({ key, label, icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-1.5 px-3 md:px-5 py-3 text-[13px] font-sans font-semibold transition-colors border-b-2 -mb-px whitespace-nowrap flex-shrink-0 ${activeTab === key ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}
            >
              <Icon d={icon} size={14} />
              {label}
            </button>
          ))}
        </div>

        {/* ── Dashboard Tab ─────────────────────────────────────────────────────── */}
        {activeTab === 'dashboard' && (
          <div className="space-y-6">
            {/* Stats bar */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                {
                  label: 'Open Tasks',
                  value: dashboard ? dashboard.open_tasks : '—',
                  sub: dashboard?.overdue_tasks > 0 ? `${dashboard.overdue_tasks} overdue` : null,
                  alert: dashboard?.overdue_tasks > 0,
                },
                {
                  label: 'Budget Used',
                  value: dashboard?.utilization_pct != null ? `${dashboard.utilization_pct}%` : (budget?.budget_amount ? `${budget.utilization_pct ?? 0}%` : '—'),
                  sub: dashboard?.budget_amount ? `$${Number(dashboard.total_billed).toLocaleString()} budget used` : null,
                  alert: (dashboard?.utilization_pct ?? budget?.utilization_pct ?? 0) >= 85,
                },
                {
                  label: 'Active Workers',
                  value: dashboard ? (dashboard.active_workers?.length ?? 0) : (matter.assignments?.filter(a => a.is_active_working).length ?? 0),
                  sub: dashboard?.active_workers?.length ? dashboard.active_workers.slice(0, 2).join(', ') : null,
                  alert: false,
                },
                {
                  label: 'Last Activity',
                  value: dashboard?.last_activity_at
                    ? (() => { try { return format(parseISO(dashboard.last_activity_at), 'MMM d') } catch { return '—' } })()
                    : '—',
                  sub: dashboard?.last_activity_at
                    ? (() => { try { const d = differenceInDays(new Date(), parseISO(dashboard.last_activity_at)); return d === 0 ? 'Today' : `${d}d ago` } catch { return null } })()
                    : null,
                  alert: dashboard?.last_activity_at
                    ? differenceInDays(new Date(), parseISO(dashboard.last_activity_at)) > 14
                    : false,
                },
              ].map((s, i) => (
                <div key={i} className={`bg-brand-surface border rounded-2xl p-5 shadow-sm ${s.alert ? 'border-brand-rose/30' : 'border-brand-line'}`}>
                  <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">{s.label}</div>
                  <div className={`text-[28px] font-serif font-bold ${s.alert ? 'text-brand-rose' : 'text-brand-ink'}`}>{s.value}</div>
                  {s.sub && <div className={`text-[12px] font-sans mt-1 truncate ${s.alert ? 'text-brand-rose' : 'text-brand-muted'}`}>{s.sub}</div>}
                </div>
              ))}
            </div>

            {/* Quick Actions */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-4">Quick Actions</div>
              <div className="flex flex-wrap gap-3">
                {[
                  { label: 'Log Time', icon: Icons.clock, action: () => navigate(`/time-tracking?matter_id=${id}`) },
                  { label: 'Email Client', icon: Icons.mail, action: () => setShowCompose(true) },
                  { label: 'Add Task', icon: Icons.plus, action: () => setShowAddTask(true) },
                  { label: 'Start Chat', icon: Icons.messageSquare, action: handleStartChat },
                  { label: cloudSyncing ? 'Syncing Cloud' : 'Sync Cloud', icon: Icons.refresh, action: handleMatterCloudSync },
                  { label: 'Add Note', icon: Icons.edit, action: () => { setActiveTab('activity'); setTimeout(() => setShowAddNote(true), 50) } },
                ].map((a, i) => (
                  <button
                    key={i}
                    onClick={a.action}
                    disabled={a.label.includes('Syncing')}
                    className="flex items-center gap-2 px-4 py-2.5 bg-brand-bg-soft border border-brand-line text-brand-ink text-[13px] font-sans font-semibold rounded-xl hover:bg-brand-surface hover:border-brand-line-2 hover:-translate-y-[1px] transition-all shadow-sm"
                  >
                    <Icon d={a.icon} size={15} className={`text-brand-accent ${a.label.includes('Syncing') ? 'animate-spin' : ''}`} />
                    {a.label}
                  </button>
                ))}
              </div>
              {cloudSyncError && (
                <p className="text-brand-rose text-[12px] font-sans mt-3">{cloudSyncError}</p>
              )}
            </div>

            {/* Key Dates + To-Do */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Key Dates (left, 2/3) */}
              <div className="lg:col-span-2 bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
                <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
                  <h3 className="font-serif font-bold text-lg text-brand-ink">Key Dates</h3>
                  <button onClick={() => setShowAddTask(true)} className="flex items-center gap-1.5 text-[12px] font-semibold text-brand-accent hover:underline">
                    <Icon d={Icons.plus} size={13} /> Add
                  </button>
                </div>
                <div className="p-4">
                  {tasksLoading ? (
                    <div className="flex justify-center py-8"><div className="w-5 h-5 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
                  ) : keyDateTasks.length === 0 ? (
                    <div className="text-center py-10">
                      <Icon d={Icons.clock} size={28} className="mx-auto text-brand-line-2 mb-2" />
                      <p className="text-brand-muted text-sm font-sans">No hearings, filings, or deadlines in the next 30 days.</p>
                      <button onClick={() => setShowAddTask(true)} className="mt-3 text-brand-accent text-sm font-semibold hover:underline">Add a date</button>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {keyDateTasks.map(t => (
                        <div key={t.id} className="flex items-center gap-3 p-3 rounded-xl border border-brand-line hover:border-brand-line-2 bg-brand-bg-soft/40 group transition-colors">
                          <button
                            onClick={() => handleTaskComplete(t.id)}
                            className="shrink-0 w-5 h-5 rounded-full border-2 border-brand-line group-hover:border-brand-accent hover:bg-brand-accent/10 transition-colors"
                            title="Mark complete"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-[14px] font-semibold text-brand-ink font-sans truncate">{t.title}</span>
                              <TaskTypeBadge type={t.task_type} />
                            </div>
                            <div className="flex items-center gap-3 mt-0.5">
                              <DueDateLabel dueDate={t.due_date} />
                              {t.priority === 'urgent' && <span className="text-[11px] font-bold text-brand-rose uppercase tracking-wide">Urgent</span>}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* To-Do (right, 1/3) */}
              <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
                <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
                  <h3 className="font-serif font-bold text-lg text-brand-ink">To-Do</h3>
                  <button onClick={() => setShowAddTask(true)} className="flex items-center gap-1.5 text-[12px] font-semibold text-brand-accent hover:underline">
                    <Icon d={Icons.plus} size={13} /> Add
                  </button>
                </div>
                <div className="p-4">
                  {todoTasks.length === 0 ? (
                    <div className="text-center py-8">
                      <Icon d={Icons.checkCircle} size={28} className="mx-auto text-brand-green mb-2" />
                      <p className="text-brand-muted text-sm font-sans">All caught up.</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {todoTasks.slice(0, 8).map(t => (
                        <div key={t.id} className="flex items-start gap-2 p-2.5 rounded-lg hover:bg-brand-bg-soft group transition-colors">
                          <button
                            onClick={() => handleTaskComplete(t.id)}
                            className="shrink-0 mt-0.5 w-4 h-4 rounded border border-brand-line group-hover:border-brand-accent transition-colors"
                            title="Mark complete"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-[13px] font-sans text-brand-ink truncate">{t.title}</div>
                            {t.due_date && <DueDateLabel dueDate={t.due_date} />}
                          </div>
                        </div>
                      ))}
                      {todoTasks.length > 8 && (
                        <p className="text-[12px] text-brand-muted font-sans text-center pt-1">+{todoTasks.length - 8} more</p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Cloud Files */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
              <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Icon d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" size={18} className="text-brand-accent" />
                  <h3 className="font-serif font-bold text-lg text-brand-ink">Cloud Files</h3>
                </div>
                <div className="flex items-center gap-2">
                  {cloudFiles?.connected && (
                    <span className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-widest text-brand-green bg-brand-green/10 px-2.5 py-1 rounded-lg border border-brand-green/20">
                      <span className="w-1.5 h-1.5 rounded-full bg-brand-green inline-block" /> Connected
                    </span>
                  )}
                  <button
                    onClick={handleMatterCloudSync}
                    disabled={cloudSyncing}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink text-[12px] font-sans font-semibold rounded-lg hover:bg-brand-bg-soft disabled:opacity-50 transition-colors"
                  >
                    <Icon d={Icons.refresh} size={12} className={cloudSyncing ? 'animate-spin' : ''} />
                    {cloudSyncing ? 'Syncing…' : 'Sync'}
                  </button>
                </div>
              </div>
              <div className="p-4">
                {cloudSyncError && (
                  <div className="mb-3 px-3 py-2 bg-brand-rose/10 border border-brand-rose/20 rounded-lg text-brand-rose text-[12px] font-sans">
                    {cloudSyncError}
                  </div>
                )}
                {cloudFiles === null ? (
                  <div className="flex justify-center py-6"><div className="w-5 h-5 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
                ) : !cloudFiles.connected ? (
                  <div className="text-center py-6">
                    <Icon d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" size={28} className="mx-auto text-brand-line-2 mb-2" />
                    <p className="text-brand-muted text-sm font-sans mb-3">No cloud integrations connected.</p>
                    <button onClick={() => navigate('/admin')} className="text-brand-accent text-sm font-semibold hover:underline">
                      Connect in Admin → Integrations →
                    </button>
                  </div>
                ) : cloudFiles.files.length === 0 ? (
                  <div className="text-center py-6">
                    <p className="text-brand-muted text-sm font-sans">No cloud files found matching this matter.</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {cloudFiles.files.slice(0, 10).map((f, i) => (
                      <a
                        key={f.id || i}
                        href={f.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-start gap-3 p-3 rounded-xl border border-brand-line hover:border-brand-accent/30 hover:bg-brand-accent/5 transition-colors group"
                      >
                        <div className="shrink-0 w-8 h-8 rounded-lg bg-brand-bg-soft border border-brand-line flex items-center justify-center">
                          <span className="text-[11px] font-bold text-brand-muted uppercase">{f.source?.slice(0, 2) || f.provider?.slice(0, 2) || '?'}</span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-[13px] font-semibold text-brand-ink font-sans truncate group-hover:text-brand-accent">{f.title}</div>
                          {f.snippet && <div className="text-[12px] text-brand-muted font-sans mt-0.5 line-clamp-1">{f.snippet}</div>}
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-[10px] font-bold uppercase tracking-wide text-brand-muted bg-brand-bg-soft px-1.5 py-0.5 rounded border border-brand-line">{f.source || f.provider}</span>
                          </div>
                        </div>
                        <Icon d="M5 12h14M12 5l7 7-7 7" size={14} className="text-brand-muted group-hover:text-brand-accent shrink-0 mt-1 transition-colors" />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Case Details (collapsible) */}
            <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
              <button
                onClick={() => setShowDetailsPanel(v => !v)}
                className="w-full flex items-center justify-between px-6 py-4 text-left"
              >
                <span className="font-serif font-bold text-lg text-brand-ink">Case Details</span>
                <Icon d={showDetailsPanel ? Icons.chevronDown : Icons.chevronRight} size={18} className="text-brand-muted transition-transform" />
              </button>
              {showDetailsPanel && (
                <div className="px-6 pb-6 grid grid-cols-1 md:grid-cols-2 gap-x-8 border-t border-brand-line pt-4">
                  <dl>
                    <Field label="Practice Area">{dm.practice_area}</Field>
                    <Field label="Matter Type">{dm.matter_type}</Field>
                    <Field label="Case Number">{dm.case_number}</Field>
                    <Field label="Stage">{dm.stage}</Field>
                    <Field label="Jurisdiction">{dm.jurisdiction}</Field>
                    <Field label="Court">{dm.court}</Field>
                    <Field label="Judge">{dm.judge}</Field>
                    <Field label="Counterparty">{dm.counterparty}</Field>
                  </dl>
                  <dl>
                    <Field label="Client">
                      {matter.client_name && <span className="font-semibold text-brand-ink">{matter.client_name}</span>}
                    </Field>
                    <Field label="Attorney of Record">
                      {matter.attorney_of_record_name && <span className="font-semibold text-brand-ink">{matter.attorney_of_record_name}</span>}
                    </Field>
                    <Field label="Partner Attorney">
                      {matter.partner_attorney_name && <span className="font-semibold text-brand-ink">{matter.partner_attorney_name}</span>}
                    </Field>
                    <Field label="Billing Method">{dm.billing_method}</Field>
                    <Field label="Billing Cycle">{dm.billing_cycle}</Field>
                    {dm.hourly_rate && <Field label="Hourly Rate">${Number(dm.hourly_rate).toLocaleString()}</Field>}
                    {dm.budget_amount && <Field label="Budget">${Number(dm.budget_amount).toLocaleString()} {dm.budget_currency}</Field>}
                    <Field label="Plugin Workflow">
                      {dm.primary_plugin ? (
                        <button onClick={() => navigate(pluginRoute(dm.primary_plugin))} className="text-brand-accent font-semibold hover:underline">
                          {pluginLabel(dm.primary_plugin)}
                        </button>
                      ) : 'General matter'}
                    </Field>
                  </dl>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Activity Tab (Timeline + Communications) ──────────────────────────── */}
        {activeTab === 'activity' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                    <Icon d={Icons.activity} size={18} className="text-brand-accent" /> Activity
                  </h2>
                  <p className="text-[13px] text-brand-muted font-sans mt-0.5">Timeline events, notes, and communications.</p>
                </div>
                <div className="flex items-center gap-3">
                  {/* Filter */}
                  <div className="flex rounded-lg border border-brand-line overflow-hidden text-[12px] font-semibold font-sans">
                    {[['all', 'All'], ['timeline', 'Timeline'], ['comms', 'Comms']].map(([val, lbl]) => (
                      <button
                        key={val}
                        onClick={() => setActivityFilter(val)}
                        className={`px-3 py-1.5 transition-colors ${activityFilter === val ? 'bg-brand-ink text-white' : 'bg-brand-surface text-brand-muted hover:text-brand-ink'}`}
                      >
                        {lbl}
                      </button>
                    ))}
                  </div>
                  <button onClick={() => setShowAddNote(v => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft shadow-sm">
                    <Icon d={Icons.plus} size={15} /> Add Note
                  </button>
                  <button onClick={() => setShowLogComm(v => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft shadow-sm">
                    <Icon d={Icons.mail} size={15} /> Log Comm
                  </button>
                </div>
              </div>
            </div>

            {/* Add Note form */}
            {showAddNote && (
              <div className="p-6 bg-brand-bg border-b border-brand-line">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                  <div>
                    <label htmlFor="matterdetailpage-note-type" className={labelCls}>Note Type</label>
                    <select id="matterdetailpage-note-type" disabled={addingNote || !!noteRequest.current} value={newNote.note_type} onChange={e => setNewNote(p => ({ ...p, note_type: e.target.value }))} className={inputCls}>
                      {NOTE_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-title" className={labelCls}>Title</label>
                    <input id="matterdetailpage-title" type="text" disabled={addingNote || !!noteRequest.current} value={newNote.title} onChange={e => setNewNote(p => ({ ...p, title: e.target.value }))} placeholder="Note title..." className={inputCls} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="matterdetailpage-content" className={labelCls}>Content</label>
                    <textarea id="matterdetailpage-content" disabled={addingNote || !!noteRequest.current} value={newNote.content} onChange={e => setNewNote(p => ({ ...p, content: e.target.value }))} rows={3} placeholder="Note content..." className={`${inputCls} resize-none`} />
                  </div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={() => setShowAddNote(false)} className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink">Cancel</button>
                  <button onClick={handleAddNote} disabled={addingNote || noteConflict || !newNote.title.trim()} className="min-h-11 px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50">
                    {addingNote ? 'Saving…' : 'Save Note'}
                  </button>
                </div>
              </div>
            )}

            {/* Log Communication form */}
            {showLogComm && (
              <div className="p-6 bg-brand-bg border-b border-brand-line">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                  <div>
                    <label htmlFor="matterdetailpage-direction" className={labelCls}>Direction</label>
                    <select id="matterdetailpage-direction" value={newComm.direction} onChange={e => setNewComm(p => ({ ...p, direction: e.target.value }))} className={inputCls}>
                      <option value="outbound">Outbound</option>
                      <option value="inbound">Inbound</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-channel" className={labelCls}>Channel</label>
                    <select id="matterdetailpage-channel" value={newComm.channel} onChange={e => setNewComm(p => ({ ...p, channel: e.target.value }))} className={inputCls}>
                      {['email', 'call', 'letter', 'meeting', 'sms', 'portal', 'other'].map(c => (
                        <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-subject" className={labelCls}>Subject</label>
                    <input id="matterdetailpage-subject" type="text" value={newComm.subject} onChange={e => setNewComm(p => ({ ...p, subject: e.target.value }))} placeholder="Subject…" className={inputCls} />
                  </div>
                  <div className="md:col-span-3">
                    <label htmlFor="matterdetailpage-notes-body" className={labelCls}>Notes / Body</label>
                    <textarea id="matterdetailpage-notes-body" value={newComm.body} onChange={e => setNewComm(p => ({ ...p, body: e.target.value }))} rows={3} placeholder="Optional notes…" className={`${inputCls} resize-none`} />
                  </div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={() => setShowLogComm(false)} className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink">Cancel</button>
                  <button onClick={handleLogComm} disabled={loggingComm || !newComm.subject.trim()} className="min-h-11 px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50">
                    {loggingComm ? 'Saving…' : 'Log Communication'}
                  </button>
                </div>
              </div>
            )}

            <div className="p-6">
              {activityLoading ? (
                <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
              ) : filteredActivity.length === 0 ? (
                <div className="text-center py-16">
                  <Icon d={Icons.activity} size={32} className="mx-auto text-brand-line-2 mb-3" />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No activity yet</p>
                  <p className="text-brand-muted text-sm font-sans">Notes, events, and communications will appear here.</p>
                </div>
              ) : (
                <div className="relative border-l-2 border-brand-line ml-4 space-y-8 pb-4">
                  {filteredActivity.map((ev, i) => {
                    if (ev._kind === 'comm') {
                      return (
                        <div key={ev.id || `c-${i}`} className="relative pl-6">
                          <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-accent/60 rounded-full -left-[9px] top-1" />
                          <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                            <div className="flex flex-wrap items-center gap-3 mb-2">
                              <span className={`shrink-0 px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wide border ${ev.direction === 'inbound' ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-brand-green/10 text-brand-green border-brand-green/20'}`}>{ev.direction}</span>
                              <span className="text-[12px] font-semibold text-brand-muted uppercase tracking-wide">{ev.channel}</span>
                              <span className="text-[12px] text-brand-muted font-sans">
                                {ev.occurred_at ? format(parseISO(ev.occurred_at), 'MMM d, yyyy h:mm a') : ''}
                              </span>
                            </div>
                            <div className="text-[14px] font-semibold text-brand-ink font-sans">{ev.subject}</div>
                            {ev.body && <div className="text-[13px] text-brand-muted font-sans mt-1 line-clamp-2">{ev.body}</div>}
                          </div>
                        </div>
                      )
                    }
                    return (
                      <div key={ev.id || i} className="relative pl-6">
                        <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1" />
                        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                          <div className="flex flex-wrap items-center gap-3 mb-2">
                            <EntryBadge type={ev.entry_type === 'note' ? ev.metadata?.note_type || 'note' : ev.metadata?.event_type || ev.entry_type} />
                            <span className="text-[12px] text-brand-muted font-sans">
                              {ev.created_at ? format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') : ''}
                            </span>
                            {ev.created_by_name && <span className="text-[12px] text-brand-muted font-sans">· {ev.created_by_name}</span>}
                          </div>
                          <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-1.5">{ev.title}</h4>
                          {ev.content && <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose prose-sm max-w-none"><ReactMarkdown>{ev.content}</ReactMarkdown></div>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Team Tab ─────────────────────────────────────────────────────────── */}
        {activeTab === 'team' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink">Team Assignments</h2>
              <p className="text-[13px] text-brand-muted font-sans mt-0.5">Users assigned for visibility and tracking. Green dot = actively working now.</p>
            </div>
            <div className="p-6 space-y-6">
              {assignments.length === 0 ? (
                <p className="text-brand-muted text-sm font-sans text-center py-6">No team members assigned.</p>
              ) : (
                <div className="space-y-2">
                  {assignments.map(a => (
                    <div key={a.id} className="flex items-center justify-between bg-brand-bg-soft rounded-xl px-4 py-3 border border-brand-line">
                      <div className="flex items-center gap-3">
                        <div className="relative w-8 h-8 rounded-full bg-brand-accent/10 flex items-center justify-center shrink-0">
                          <Icon d={Icons.user} size={15} className="text-brand-accent" />
                          {a.is_active_working && (
                            <span className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-brand-green rounded-full border-2 border-brand-surface" />
                          )}
                        </div>
                        <div>
                          <div className="text-[14px] font-semibold text-brand-ink font-sans">{a.user_name}</div>
                          <div className="text-[12px] text-brand-muted font-sans capitalize">{a.role?.replace(/_/g, ' ')}</div>
                          {a.is_active_working && (
                            <div className="text-[11px] text-brand-green font-semibold mt-0.5">Actively working</div>
                          )}
                        </div>
                        {a.is_primary && (
                          <span className="text-[11px] font-bold text-brand-accent bg-brand-accent/10 px-2 py-0.5 rounded border border-brand-accent/20">Lead</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleToggleActive(a.id, a.is_active_working)}
                          className={`text-[11px] font-semibold px-2.5 py-1 rounded border transition-colors ${
                            a.is_active_working
                              ? 'bg-brand-green/10 text-brand-green border-brand-green/30 hover:bg-brand-green/20'
                              : 'bg-brand-bg text-brand-muted border-brand-line hover:text-brand-green hover:border-brand-green/30'
                          }`}
                        >
                          {a.is_active_working ? 'Active' : 'Set Active'}
                        </button>
                        <button
                          onClick={() => handleRemoveAssignment(a.id)}
                          className="text-brand-muted hover:text-brand-rose transition-colors p-1.5 rounded-lg hover:bg-brand-rose/10"
                        >
                          <Icon d={Icons.trash} size={15} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="border-t border-brand-line pt-5">
                <h3 className="text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-3">Add Team Member</h3>
                {selectedUserId ? (
                  <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                    <div className="flex-1 flex items-center gap-2 px-3 py-2.5 bg-brand-accent/10 border border-brand-accent/30 rounded-lg">
                      <div className="w-6 h-6 rounded-full bg-brand-accent/20 flex items-center justify-center text-[10px] font-bold text-brand-accent shrink-0 uppercase">
                        {(selectedUserName || '?').slice(0, 2)}
                      </div>
                      <span className="text-[13px] font-semibold text-brand-ink font-sans flex-1 truncate">{selectedUserName}</span>
                      <button onClick={() => { setSelectedUserId(''); setSelectedUserName('') }} className="text-brand-muted hover:text-brand-rose transition-colors text-xs">✕</button>
                    </div>
                    <div className="w-40">
                      <label htmlFor="matterdetailpage-role" className={labelCls}>Role</label>
                      <select id="matterdetailpage-role" value={selectedRole} onChange={e => setSelectedRole(e.target.value)} className={inputCls}>
                        {['lead_attorney', 'associate', 'paralegal', 'of_counsel', 'billing'].map(r => (
                          <option key={r} value={r}>{r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
                        ))}
                      </select>
                    </div>
                    <button
                      onClick={handleAddAssignment}
                      disabled={addingUser}
                      className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 transition-all whitespace-nowrap"
                    >
                      {addingUser ? 'Adding…' : 'Add to Team'}
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                    <div className="flex-1">
                      <label htmlFor="matter-team-user-search" className={labelCls}>Search for User</label>
                      <UserSearchInput
                        inputId="matter-team-user-search"
                        excludeIds={[...assignedIds]}
                        onSelect={u => { setSelectedUserId(u.id); setSelectedUserName(u.full_name || u.email) }}
                        placeholder="Type a name or email to search…"
                      />
                    </div>
                    <div className="w-40">
                      <label htmlFor="matterdetailpage-role-2" className={labelCls}>Role</label>
                      <select id="matterdetailpage-role-2" value={selectedRole} onChange={e => setSelectedRole(e.target.value)} className={inputCls}>
                        {['lead_attorney', 'associate', 'paralegal', 'of_counsel', 'billing'].map(r => (
                          <option key={r} value={r}>{r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
                        ))}
                      </select>
                    </div>
                    <button
                      disabled
                      className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg opacity-30 cursor-not-allowed whitespace-nowrap"
                    >
                      Add to Team
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'workflow' && (
          <MatterWorkflowPanel
            matterId={id}
            user={user}
            onWorkflowApplied={() => {
              loadMatter()
              loadDashboard()
            }}
          />
        )}

        {/* ── Correspondence Tab (archived emails) ─────────────────────────────── */}
        {activeTab === 'correspondence' && (
          <MatterCorrespondenceTab matterId={id} matter={matter} />
        )}

        {/* ── Documents Tab (includes Parties) ─────────────────────────────────── */}
        {activeTab === 'documents' && (
          <div className="space-y-8">
            <MatterDocumentsTab
              matterId={id}
              onReviseDocument={(document) => navigate(`/matters/${id}/documents/${document.id}/revise`)}
              onCloudFolderChange={(providers) => {
                setMatter(prev => prev ? { ...prev, cloud_folder: providers || {} } : prev)
                setEditData(prev => ({ ...prev, cloud_folder: providers || {} }))
                loadCloudFiles()
              }}
            />
            <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
              <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                  <Icon d={Icons.parties} size={18} className="text-brand-accent" /> Parties
                </h2>
                <p className="text-[13px] text-brand-muted font-sans mt-0.5">Opposing counsel, plaintiffs, defendants, and other parties.</p>
              </div>
              <MatterPartiesTab matterId={id} embedded />
            </div>
          </div>
        )}

        {/* ── Client Portal Tab ────────────────────────────────────────────────── */}
        {activeTab === 'portal' && (
          <ClientPortalTab matterId={id} matter={matter} />
        )}

        {/* ── Billing Tab ──────────────────────────────────────────────────────── */}
        {activeTab === 'billing' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex items-center justify-between">
              <div>
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                  <Icon d={Icons.dollar} size={18} className="text-brand-accent" /> Billing
                </h2>
                <p className="text-[13px] text-brand-muted font-sans mt-0.5">Time, client costs, and internal spend for this matter.</p>
              </div>
              <button onClick={() => navigate(`/time-tracking?matter_id=${id}`)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft shadow-sm">
                <Icon d={Icons.plus} size={15} /> Log Time
              </button>
            </div>
            <div className="p-6">
              {billingLoading ? (
                <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
              ) : timeEntries.length === 0 ? (
                <div className="text-center py-16">
                  <Icon d={Icons.dollar} size={32} className="mx-auto text-brand-line-2 mb-3" />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No time entries</p>
                  <p className="text-brand-muted text-sm font-sans mb-4">Log time against this matter from the Time Tracking section.</p>
                  <button onClick={() => navigate(`/time-tracking?matter_id=${id}`)} className="px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2">
                    Go to Time Tracking
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 text-center">
                      <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1">Total Hours</div>
                      <div className="text-2xl font-serif font-bold text-brand-ink">
                        {timeEntries.reduce((s, e) => s + (parseFloat(e.hours) || 0), 0).toFixed(1)}
                      </div>
                    </div>
                    <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 text-center">
                      <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1">Time Value</div>
                      <div className="text-2xl font-serif font-bold text-brand-ink">
                        ${timeEntries.reduce((s, e) => s + (parseFloat(e.amount) || 0), 0).toLocaleString()}
                      </div>
                    </div>
                    <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 text-center">
                      <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1">Entries</div>
                      <div className="text-2xl font-serif font-bold text-brand-ink">{timeEntries.length}</div>
                    </div>
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-brand-line">
                    <table className="w-full text-[13px] font-sans">
                      <thead className="bg-brand-bg-soft border-b border-brand-line">
                        <tr>
                          {['Date', 'Description', 'User', 'Hours', 'Amount', 'Status'].map(h => (
                            <th key={h} className="text-left px-4 py-3 text-[11px] font-bold text-brand-muted uppercase tracking-widest">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-brand-line/50">
                        {timeEntries.map(e => (
                          <tr key={e.id} className="hover:bg-brand-bg-soft/40 transition-colors">
                            <td className="px-4 py-3 text-brand-muted whitespace-nowrap">{e.date ? format(parseISO(e.date), 'MMM d, yyyy') : '—'}</td>
                            <td className="px-4 py-3 text-brand-ink max-w-xs truncate">{e.description || '—'}</td>
                            <td className="px-4 py-3 text-brand-muted">{e.user_name || '—'}</td>
                            <td className="px-4 py-3 text-brand-ink font-mono">{e.hours}</td>
                            <td className="px-4 py-3 text-brand-ink font-mono">{e.amount ? `$${Number(e.amount).toLocaleString()}` : '—'}</td>
                            <td className="px-4 py-3">
                              <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase border ${
                                e.status === 'invoiced' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' :
                                e.status === 'approved' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                                'bg-brand-bg-soft text-brand-muted border-brand-line'
                              }`}>{e.status || 'draft'}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
            <MatterExpensesPanel
              matterId={id}
              onOpenInbox={() => setActiveTab('correspondence')}
              onExpensesChanged={refreshBillingSummary}
            />
          </div>
        )}

        {/* ── Chat Tab ─────────────────────────────────────────────────────────── */}
        {activeTab === 'chat' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex items-center justify-between">
              <div>
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                  <Icon d={Icons.messageSquare} size={18} className="text-brand-accent" /> Matter Chat
                </h2>
                <p className="text-[13px] text-brand-muted font-sans mt-0.5">Chats here use your professional profile together with this matter's AI Context and documents.</p>
              </div>
              <button
                onClick={handleStartChat}
                disabled={startingConv}
                className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 transition-colors shadow-sm disabled:opacity-50"
              >
                <Icon d={Icons.plus} size={15} /> {startingConv ? 'Starting…' : 'Start New Chat'}
              </button>
            </div>
            <div className="mx-6 mt-4 flex flex-col gap-3 rounded-xl border border-brand-accent/20 bg-brand-accent/5 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-semibold text-brand-ink font-sans"><Icon d={Icons.brain} size={16} className="text-brand-accent" /> AI Context</p>
                <p className="mt-1 text-[13px] text-brand-muted font-sans">Add the case facts, goals, and preferences you want AI to keep in mind for this matter.</p>
              </div>
              <button onClick={() => { setActiveTab('settings'); setSettingsSection('memory') }} className="shrink-0 rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm font-semibold text-brand-ink hover:bg-brand-bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent">
                Edit context
              </button>
            </div>
            {cloudFiles?.connected && (
              <div className="mx-6 mt-4 flex items-center gap-2.5 px-4 py-3 bg-brand-accent/5 border border-brand-accent/20 rounded-xl">
                <Icon d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" size={16} className="text-brand-accent shrink-0" />
                <p className="text-[13px] text-brand-ink-2 font-sans">
                  <span className="font-semibold text-brand-ink">Cloud context active</span> — AI chats here have access to matter files from your connected OneDrive, SharePoint, and Google Drive.
                </p>
              </div>
            )}
            <div className="p-6">
              {convLoading ? (
                <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
              ) : matterConvs.length === 0 ? (
                <div className="text-center py-16">
                  <Icon d={Icons.messageSquare} size={32} className="mx-auto text-brand-line-2 mb-3" />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No conversations yet</p>
                  <p className="text-brand-muted text-sm font-sans mb-4">Start an AI chat with this matter loaded as context.</p>
                  <button onClick={handleStartChat} disabled={startingConv} className="min-h-11 px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50">
                    {startingConv ? 'Starting…' : 'Start Chat About This Matter'}
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  {matterConvs.map(conv => (
                    <MatterConversationLink
                      key={conv.id}
                      conversation={conv}
                      cloudConnected={cloudFiles?.connected}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Settings Tab (Plugin + Memory + File Shares + Edit Matter) ────────── */}
        {activeTab === 'settings' && (
          <div className="space-y-8">
            {/* Section selector */}
            <div className="flex gap-2 border-b border-brand-line pb-0">
              {[['memory', 'AI Context', Icons.brain], ['plugin', 'Plugin Workflow', Icons.briefcase], ['shares', 'File Shares', Icons.folder], ['details', 'Edit Details', Icons.edit]].map(([key, lbl, ico]) => (
                <button
                  key={key}
                  onClick={() => setSettingsSection(key)}
                  className={`flex items-center gap-1.5 px-4 py-2.5 text-[13px] font-semibold font-sans border-b-2 -mb-px transition-colors ${settingsSection === key ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}
                >
                  <Icon d={ico} size={14} />{lbl}
                </button>
              ))}
            </div>

            {settingsSection === 'memory' && (
              <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
                <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
                  <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                    <Icon d={Icons.brain} size={18} className="text-brand-accent" /> AI Context
                  </h2>
                  <p className="text-[13px] text-brand-muted font-sans mt-0.5">Give AI the case details, goals, and working preferences that should guide chats about this matter.</p>
                </div>
                <div className="p-6">
                  <div className="mb-5 rounded-xl border border-brand-line bg-brand-bg-soft/40 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-brand-ink font-sans">Structured matter context</p>
                        <p className="mt-1 text-xs text-brand-muted font-sans">The summary, jurisdiction, our role, and stage are added automatically when available.</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => { setSettingsSection('details'); setEditing(true) }}
                        className="shrink-0 rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm font-semibold text-brand-ink hover:bg-brand-bg-soft"
                      >
                        Edit matter details
                      </button>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {[
                        ['Summary', matter.description],
                        ['Jurisdiction', matter.jurisdiction],
                        ['Our role', matter.role],
                        ['Stage', matter.stage],
                      ].map(([label, value]) => (
                        <span key={label} className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${value ? 'bg-brand-green/10 text-brand-green' : 'bg-brand-amber/10 text-brand-amber'}`}>
                          {label}: {value ? 'Saved' : 'Add details'}
                        </span>
                      ))}
                    </div>
                  </div>
                  <textarea
                    value={memoryContent}
                    onChange={e => setMemoryContent(e.target.value)}
                    rows={20}
                    placeholder={`# ${matter.matter_name}\n\nAdd practical guidance for AI when working on this matter.\n\n## Client and matter overview\n## Current goals and key issues\n## Important facts and dates\n## Preferences or instructions`}
                    className={`${inputCls} resize-y font-mono text-[13px] leading-relaxed`}
                  />
                  <div className="flex items-center justify-end gap-3 mt-4">
                    {memorySaved && <span className="text-brand-green text-sm font-sans font-medium">Saved ✓</span>}
                    <button
                      onClick={handleSaveMemory}
                      disabled={memorySaving}
                      className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm"
                    >
                      <Icon d={Icons.save} size={15} />
                      {memorySaving ? 'Saving…' : 'Save AI Context'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {settingsSection === 'plugin' && (
              <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
                <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
                  <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                    <Icon d={Icons.briefcase} size={18} className="text-brand-accent" /> Plugin Workflow
                  </h2>
                  <p className="text-[13px] text-brand-muted font-sans mt-0.5">The optional add-on workflow attached to this matter.</p>
                </div>
                <div className="p-6">
                  {matter.primary_plugin ? (
                    <div className="border border-brand-line rounded-xl p-5 bg-brand-bg-soft/40">
                      <p className="text-[11px] uppercase tracking-widest font-bold text-brand-muted mb-2">Assigned Workflow</p>
                      <h3 className="font-serif font-bold text-2xl text-brand-ink mb-2">{pluginLabel(matter.primary_plugin)}</h3>
                      <p className="text-sm text-brand-muted font-sans mb-5">Plugin skills run with this matter's memory, timeline, notes, parties, and budget context.</p>
                      <div className="flex flex-wrap gap-3">
                        <button onClick={() => navigate(pluginRoute(matter.primary_plugin))} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2">
                          Open Plugin Workspace
                        </button>
                        <button onClick={() => navigate(`/plugins/${matter.primary_plugin}`)} className="px-5 py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-semibold rounded-xl hover:bg-brand-bg">
                          Configure Workflow
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="border border-brand-line rounded-xl p-6 text-center bg-brand-bg-soft/40">
                      <h3 className="font-serif font-bold text-xl text-brand-ink mb-2">General Matter</h3>
                      <p className="text-sm text-brand-muted font-sans mb-5">No add-on workflow attached.</p>
                      <button onClick={() => setSettingsSection('details')} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2">
                        Assign Plugin Workflow
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {settingsSection === 'shares' && (
              <MatterSmbSharesTab
                matterId={id}
                onCloudFolderChange={(providers) => {
                  setMatter(prev => prev ? { ...prev, cloud_folder: providers || {} } : prev)
                  setEditData(prev => ({ ...prev, cloud_folder: providers || {} }))
                  loadCloudFiles()
                }}
              />
            )}

            {settingsSection === 'details' && editing ? (
              <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
                <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl flex items-center justify-between">
                  <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                    <Icon d={Icons.edit} size={18} className="text-brand-accent" /> Edit Matter Details
                  </h2>
                </div>
                <div className="p-6 space-y-4">
                  <div>
                    <label htmlFor="matterdetailpage-title-2" className={labelCls}>Title</label>
                    <input id="matterdetailpage-title-2" type="text" value={editData.matter_name || ''} onChange={e => setEditData(p => ({ ...p, matter_name: e.target.value }))} className={inputCls} />
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-description" className={labelCls}>Description</label>
                    <textarea id="matterdetailpage-description" value={editData.description || ''} onChange={e => setEditData(p => ({ ...p, description: e.target.value }))} rows={3} className={`${inputCls} resize-none`} />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label htmlFor="matterdetailpage-status" className={labelCls}>Status</label>
                      <select id="matterdetailpage-status" value={editData.status || 'open'} onChange={e => setEditData(p => ({ ...p, status: e.target.value }))} className={inputCls}>
                        {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="matterdetailpage-risk-level" className={labelCls}>Risk Level</label>
                      <select id="matterdetailpage-risk-level" value={editData.risk_level || ''} onChange={e => setEditData(p => ({ ...p, risk_level: e.target.value || null }))} className={inputCls}>
                        <option value="">None</option>
                        {RISK_OPTIONS.map(r => <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-practice-area" className={labelCls}>Practice Area</label>
                    <input id="matterdetailpage-practice-area" type="text" value={editData.practice_area || ''} onChange={e => setEditData(p => ({ ...p, practice_area: e.target.value }))} className={inputCls} />
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-matter-type" className={labelCls}>Matter Type</label>
                    <input id="matterdetailpage-matter-type" type="text" value={editData.matter_type || ''} onChange={e => setEditData(p => ({ ...p, matter_type: e.target.value }))} className={inputCls} />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div>
                      <label htmlFor="matterdetailpage-jurisdiction" className={labelCls}>Jurisdiction</label>
                      <input id="matterdetailpage-jurisdiction" type="text" value={editData.jurisdiction || ''} onChange={e => setEditData(p => ({ ...p, jurisdiction: e.target.value }))} className={inputCls} placeholder="For example, North Dakota" />
                    </div>
                    <div>
                      <label htmlFor="matterdetailpage-our-role" className={labelCls}>Represented Side / Our Role</label>
                      <input id="matterdetailpage-our-role" type="text" value={editData.role || ''} onChange={e => setEditData(p => ({ ...p, role: e.target.value }))} className={inputCls} placeholder="For example, plaintiff's counsel" />
                      <p className="mt-1 text-xs text-brand-muted">Names and caption roles belong in the Parties tab.</p>
                    </div>
                    <div>
                      <label htmlFor="matterdetailpage-stage" className={labelCls}>Current Stage</label>
                      <input id="matterdetailpage-stage" type="text" value={editData.stage || ''} onChange={e => setEditData(p => ({ ...p, stage: e.target.value }))} className={inputCls} placeholder="For example, discovery" />
                    </div>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-plugin-workflow" className={labelCls}>Plugin Workflow</label>
                    <select id="matterdetailpage-plugin-workflow" value={editData.primary_plugin || ''} onChange={e => setEditData(p => ({ ...p, primary_plugin: e.target.value || null }))} className={inputCls}>
                      <option value="">General matter</option>
                      {pluginOptions.map(p => (
                        <option key={p.plugin_name || p.id} value={p.plugin_name || p.id}>
                          {p.display_name || p.plugin_name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-case-number" className={labelCls}>Case Number</label>
                    <input id="matterdetailpage-case-number" type="text" value={editData.case_number || ''} onChange={e => setEditData(p => ({ ...p, case_number: e.target.value }))} className={inputCls} />
                  </div>

                  {/* People */}
                  <hr className="border-brand-line" />
                  <h3 className="font-serif font-bold text-sm text-brand-ink uppercase tracking-wide">People &amp; Billing</h3>

                  <div>
                    <span className={labelCls}>Client</span>
                    <ContactPicker
                      ariaLabel="Client"
                      value={contactsList.find(c => c.id === editData.client_contact_id) || (editData.client_contact_id && matter.client_name ? { id: editData.client_contact_id, display_name: matter.client_name, entity_type: 'person', contact_type: 'client' } : null)}
                      onChange={(contact) => setEditData(p => ({ ...p, client_contact_id: contact?.id || '' }))}
                      placeholder="Search contacts…"
                    />
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-attorney-of-record" className={labelCls}>Attorney of Record</label>
                    <select id="matterdetailpage-attorney-of-record" value={editData.attorney_of_record_id || ''} onChange={e => setEditData(p => ({ ...p, attorney_of_record_id: e.target.value || null }))} className={inputCls}>
                      <option value="">— None —</option>
                      {usersList.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-partner-attorney" className={labelCls}>Partner Attorney</label>
                    <select id="matterdetailpage-partner-attorney" value={editData.partner_attorney_id || ''} onChange={e => setEditData(p => ({ ...p, partner_attorney_id: e.target.value || null }))} className={inputCls}>
                      <option value="">— None —</option>
                      {usersList.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                    </select>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label htmlFor="matterdetailpage-billing-method" className={labelCls}>Billing Method</label>
                      <select id="matterdetailpage-billing-method" value={editData.billing_method || 'hourly'} onChange={e => setEditData(p => ({ ...p, billing_method: e.target.value }))} className={inputCls}>
                        {['hourly', 'flat_fee', 'contingency', 'pro_bono'].map(m => <option key={m} value={m}>{m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>)}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="matterdetailpage-billing-cycle" className={labelCls}>Billing Cycle</label>
                      <select id="matterdetailpage-billing-cycle" value={editData.billing_cycle || 'monthly'} onChange={e => setEditData(p => ({ ...p, billing_cycle: e.target.value }))} className={inputCls}>
                        {['weekly', 'biweekly', 'monthly', 'quarterly', 'on_completion'].map(c => <option key={c} value={c}>{c.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase())}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label htmlFor="matterdetailpage-hourly-rate" className={labelCls}>
                      Hourly Rate
                      <span className="text-[10px] text-brand-muted normal-case font-normal ml-1">(matter override)</span>
                    </label>
                    <input id="matterdetailpage-hourly-rate" type="number" step="0.01" min="0" value={editData.hourly_rate || ''} onChange={e => setEditData(p => ({ ...p, hourly_rate: e.target.value ? parseFloat(e.target.value) : null }))} className={inputCls} placeholder="Use user default rate" />
                  </div>

                  <hr className="border-brand-line" />
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label htmlFor="matterdetailpage-budget-amount" className={labelCls}>Budget Amount</label>
                      <input id="matterdetailpage-budget-amount" type="number" step="0.01" min="0" value={editData.budget_amount || ''} onChange={e => setEditData(p => ({ ...p, budget_amount: e.target.value ? parseFloat(e.target.value) : null }))} className={inputCls} placeholder="0.00" />
                    </div>
                    <div>
                      <label htmlFor="matterdetailpage-currency" className={labelCls}>Currency</label>
                      <select id="matterdetailpage-currency" value={editData.budget_currency || 'USD'} onChange={e => setEditData(p => ({ ...p, budget_currency: e.target.value }))} className={inputCls}>
                        {['USD', 'EUR', 'GBP', 'CAD'].map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                  </div>
                  <div className="flex gap-3 justify-end pt-4 border-t border-brand-line">
                    <button onClick={handleSave} disabled={saving} className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 shadow-sm">
                      <Icon d={Icons.save} size={15} />
                      {saving ? 'Saving…' : 'Save Changes'}
                    </button>
                  </div>
                  {saveError && <p className="text-brand-rose text-sm font-sans">{saveError}</p>}
                </div>
              </div>
            ) : (
              <dl>
                <Field label="Practice Area">{dm.practice_area}</Field>
                <Field label="Matter Type">{dm.matter_type}</Field>
                <Field label="Plugin Workflow">
                  {dm.primary_plugin ? (
                    <button
                      onClick={() => navigate(pluginRoute(dm.primary_plugin))}
                      className="text-brand-accent font-semibold hover:underline"
                    >
                      {pluginLabel(dm.primary_plugin)}
                    </button>
                  ) : 'General matter'}
                </Field>
                <Field label="Case Number">{dm.case_number}</Field>
                <Field label="Stage">{dm.stage}</Field>
                <Field label="Represented Side / Our Role">{dm.role}</Field>
                <Field label="Jurisdiction">{dm.jurisdiction}</Field>
                <Field label="Court">{dm.court}</Field>
                <Field label="Judge">{dm.judge}</Field>
                <Field label="Counterparty Summary">{dm.counterparty}</Field>
                {hasCloudStorageLinks(matter.cloud_folder) && (
                  <Field label="Cloud Storage">
                    <CloudStorageLinks cloudFolder={matter.cloud_folder} />
                  </Field>
                )}
              </dl>
            )}
          </div>
        )}

        <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-5">
            <h2 className="font-serif font-bold text-xl text-brand-ink">People</h2>
            {editingPeople ? (
              <div className="flex gap-2">
                <button onClick={() => setEditingPeople(false)} className="px-3 py-1.5 bg-brand-surface text-brand-ink border border-brand-line text-xs font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-1.5">
                  <Icon d={Icons.x} size={14} /> Cancel
                </button>
                <button onClick={handleSavePeople} disabled={savingPeople} className="px-3 py-1.5 bg-brand-ink text-white text-xs font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 flex items-center gap-1.5">
                  <Icon d={Icons.check} size={14} /> {savingPeople ? 'Saving…' : 'Save'}
                </button>
              </div>
            ) : (
              <button onClick={startEditingPeople} className="px-3 py-1.5 bg-brand-surface text-brand-ink border border-brand-line text-xs font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-1.5">
                <Icon d={Icons.edit} size={14} /> Edit
              </button>
            )}
          </div>

          {editingPeople ? (
            <div className="space-y-4">
              <div>
                <span className={labelCls}>Client</span>
                <ContactPicker
                  ariaLabel="Client"
                  value={contactsList.find(c => c.id === peopleData.client_contact_id) || (peopleData.client_contact_id && matter.client_name ? { id: peopleData.client_contact_id, display_name: matter.client_name, entity_type: 'person', contact_type: 'client' } : null)}
                  onChange={(contact) => setPeopleData(p => ({ ...p, client_contact_id: contact?.id || '' }))}
                  placeholder="Search contacts…"
                />
                {!peopleData.client_contact_id && (
                  <p className="text-[11px] text-brand-muted mt-1">Select the client for this matter. <a href="/contacts" className="text-brand-accent hover:underline">Create a contact</a> if needed.</p>
                )}
              </div>
              <div>
                <label htmlFor="matterdetailpage-attorney-of-record-2" className={labelCls}>Attorney of Record</label>
                <select id="matterdetailpage-attorney-of-record-2" value={peopleData.attorney_of_record_id || ''} onChange={e => setPeopleData(p => ({ ...p, attorney_of_record_id: e.target.value || null }))} className={inputCls}>
                  <option value="">— None —</option>
                  {usersList.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="matterdetailpage-partner-attorney-2" className={labelCls}>Partner Attorney</label>
                <select id="matterdetailpage-partner-attorney-2" value={peopleData.partner_attorney_id || ''} onChange={e => setPeopleData(p => ({ ...p, partner_attorney_id: e.target.value || null }))} className={inputCls}>
                  <option value="">— None —</option>
                  {usersList.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                </select>
              </div>
              <div>
                <p className={labelCls}>Team</p>
                {matter.assignments?.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5 mt-0.5">
                    {matter.assignments.map(a => (
                      <span key={a.id} className={`inline-flex items-center gap-1 border rounded-lg px-2.5 py-1 text-[12px] font-sans ${a.is_active_working ? 'bg-brand-green/10 border-brand-green/30 text-brand-green' : 'bg-brand-bg-soft border-brand-line text-brand-ink-2'}`}>
                        {a.is_active_working && <span className="w-1.5 h-1.5 rounded-full bg-brand-green inline-block" />}
                        {a.user_name}
                        {a.is_primary && <span className="text-[10px] text-brand-accent font-semibold ml-0.5">●</span>}
                      </span>
                    ))}
                  </div>
                ) : <span className="text-[12px] text-brand-muted">Manage team assignments on the Team tab</span>}
              </div>

              <hr className="border-brand-line" />
              <h3 className="font-serif font-bold text-base text-brand-ink">Billing</h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label htmlFor="matterdetailpage-billing-method-2" className={labelCls}>Billing Method</label>
                  <select id="matterdetailpage-billing-method-2" value={peopleData.billing_method || 'hourly'} onChange={e => setPeopleData(p => ({ ...p, billing_method: e.target.value }))} className={inputCls}>
                    {['hourly', 'flat_fee', 'contingency', 'pro_bono'].map(m => <option key={m} value={m}>{m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="matterdetailpage-billing-cycle-2" className={labelCls}>Billing Cycle</label>
                  <select id="matterdetailpage-billing-cycle-2" value={peopleData.billing_cycle || 'monthly'} onChange={e => setPeopleData(p => ({ ...p, billing_cycle: e.target.value }))} className={inputCls}>
                    {['weekly', 'biweekly', 'monthly', 'quarterly', 'on_completion'].map(c => <option key={c} value={c}>{c.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase())}</option>)}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label htmlFor="matterdetailpage-budget-amount-2" className={labelCls}>Budget Amount</label>
                  <input id="matterdetailpage-budget-amount-2" type="number" step="0.01" min="0" value={peopleData.budget_amount || ''} onChange={e => setPeopleData(p => ({ ...p, budget_amount: e.target.value ? parseFloat(e.target.value) : null }))} className={inputCls} placeholder="0.00" />
                </div>
                <div>
                  <label htmlFor="matterdetailpage-currency-2" className={labelCls}>Currency</label>
                  <select id="matterdetailpage-currency-2" value={peopleData.budget_currency || 'USD'} onChange={e => setPeopleData(p => ({ ...p, budget_currency: e.target.value }))} className={inputCls}>
                    {['USD', 'EUR', 'GBP', 'CAD'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label htmlFor="matterdetailpage-hourly-rate-2" className={labelCls}>
                  Hourly Rate
                  <span className="text-[10px] text-brand-muted normal-case font-normal ml-1">(matter override; user rates set by admin)</span>
                </label>
                <input id="matterdetailpage-hourly-rate-2" type="number" step="0.01" min="0" value={peopleData.hourly_rate || ''} onChange={e => setPeopleData(p => ({ ...p, hourly_rate: e.target.value ? parseFloat(e.target.value) : null }))} className={inputCls} placeholder="Use user default rate" />
              </div>
              {peopleError && <p className="text-brand-rose text-sm font-sans">{peopleError}</p>}
            </div>
          ) : (
            <>
              <dl>
                <Field label="Client">
                  {matter.client_name ? (
                    <span className="font-semibold text-brand-ink">{matter.client_name}</span>
                  ) : (
                    <span className="text-brand-muted italic">Not assigned — click Edit to add</span>
                  )}
                </Field>
                <Field label="Attorney of Record">
                  {matter.attorney_of_record_name && (
                    <span className="font-semibold text-brand-ink">{matter.attorney_of_record_name}</span>
                  )}
                </Field>
                <Field label="Partner Attorney">
                  {matter.partner_attorney_name && (
                    <span className="font-semibold text-brand-ink">{matter.partner_attorney_name}</span>
                  )}
                </Field>
                <Field label="Team">
                  {matter.assignments?.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 mt-0.5">
                      {matter.assignments.map(a => (
                        <span key={a.id} className={`inline-flex items-center gap-1 border rounded-lg px-2.5 py-1 text-[12px] font-sans ${a.is_active_working ? 'bg-brand-green/10 border-brand-green/30 text-brand-green' : 'bg-brand-bg-soft border-brand-line text-brand-ink-2'}`}>
                          {a.is_active_working && <span className="w-1.5 h-1.5 rounded-full bg-brand-green inline-block" />}
                          {a.user_name}
                          {a.is_primary && <span className="text-[10px] text-brand-accent font-semibold ml-0.5">●</span>}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </Field>
              </dl>

              <h2 className="font-serif font-bold text-xl text-brand-ink mb-5 mt-8">Billing</h2>
              <dl>
                <Field label="Billing Method">{dm.billing_method}</Field>
                <Field label="Billing Cycle">{dm.billing_cycle}</Field>
                {dm.hourly_rate ? <Field label="Hourly Rate">${Number(dm.hourly_rate).toLocaleString()}</Field> : matter.attorney_of_record_name && <Field label="Hourly Rate"><span className="text-brand-muted italic">Uses user default rate</span></Field>}
                <Field label="Budget">{dm.budget_amount ? `$${Number(dm.budget_amount).toLocaleString()} ${dm.budget_currency}` : <span className="text-brand-muted italic">Not set</span>}</Field>
              </dl>
            </>
          )}
        </div>
      </div>

      {/* ── Timeline Tab ─────────────────────────────────────────────────────── */}
      {activeTab === 'timeline' && (
        <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
          <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
            <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
              <Icon d={Icons.clock} size={18} className="text-brand-accent" /> Timeline
            </h2>
            <button onClick={() => setShowAddNote(v => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-colors shadow-sm">
              <Icon d={Icons.plus} size={15} /> Add Note
            </button>
          </div>

          {showAddNote && (
            <div className="p-6 bg-brand-bg border-b border-brand-line">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                  <label htmlFor="matterdetailpage-note-type-2" className={labelCls}>Note Type</label>
                  <select id="matterdetailpage-note-type-2" disabled={addingNote || !!noteRequest.current} value={newNote.note_type} onChange={e => setNewNote(p => ({ ...p, note_type: e.target.value }))} className={inputCls}>
                    {NOTE_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="matterdetailpage-title-3" className={labelCls}>Title</label>
                  <input id="matterdetailpage-title-3" type="text" disabled={addingNote || !!noteRequest.current} value={newNote.title} onChange={e => setNewNote(p => ({ ...p, title: e.target.value }))} className={inputCls} placeholder="Note title" />
                </div>
              </div>
              <div className="mb-4">
                <label htmlFor="matterdetailpage-content-2" className={labelCls}>Content</label>
                <textarea id="matterdetailpage-content-2" disabled={addingNote || !!noteRequest.current} value={newNote.content} onChange={e => setNewNote(p => ({ ...p, content: e.target.value }))} rows={4} className={`${inputCls} resize-y`} placeholder="Note content..." />
              </div>
              <div className="flex gap-3 justify-end">
                <button onClick={() => { setShowAddNote(false); setNewNote({ note_type: 'note', title: '', content: '' }) }} className="px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft">Cancel</button>
                <button onClick={handleAddNote} disabled={addingNote} className="flex items-center gap-2 px-5 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50">
                  <Icon d={Icons.save} size={15} /> {addingNote ? 'Saving…' : 'Save Note'}
                </button>
              </div>
            </div>
          )}

          <div className="p-6">
            {timeline.length === 0 ? (
              <p className="text-brand-muted text-sm font-sans text-center py-8">No timeline entries recorded for this matter.</p>
            ) : (
              <div className="space-y-4">
                {timeline.map((entry, i) => (
                  <div key={entry.id || i} className="relative pl-8 pb-4 border-l-2 border-brand-line last:border-transparent">
                    <div className="absolute left-[-7px] top-1 w-3 h-3 rounded-full bg-brand-accent border-2 border-brand-surface" />
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-brand-accent bg-brand-accent/10 px-2 py-0.5 rounded">
                        {entry.note_type || entry.entry_type || 'note'}
                      </span>
                      <span className="text-xs text-brand-muted font-sans">
                        {entry.created_at ? format(parseISO(entry.created_at), 'MMM d, yyyy h:mm a') : ''}
                      </span>
                    </div>
                    {entry.title && <h4 className="font-serif font-bold text-brand-ink text-sm mb-1">{entry.title}</h4>}
                    {entry.content && <p className="text-sm text-brand-ink-2 font-sans leading-relaxed">{entry.content}</p>}
                    {entry.author_name && (
                      <p className="text-xs text-brand-muted mt-1.5 font-sans">— {entry.author_name}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Modals */}
      {showAddTask && (
        <AddTaskModal
          matterId={id}
          teamMembers={assignments}
          onCreated={(task) => {
            setTasks(prev => [...prev, task])
            setDashboard(prev => prev ? { ...prev, open_tasks: (prev.open_tasks || 0) + 1 } : prev)
            setShowAddTask(false)
          }}
          onClose={() => setShowAddTask(false)}
        />
      )}

      {showCompose && (
        <ComposeEmailModal
          matterId={id}
          matterName={matter.matter_name}
          caseNumber={matter.case_number}
          clientEmail={matter.client?.email || null}
          onSent={(result) => {
            setShowCompose(false)
            if (activeTab === 'activity') {
              getCommunications({ matter_id: id }).then(data => {
                setCommunications(Array.isArray(data) ? data : data.items || [])
              }).catch(() => {})
            }
          }}
          onClose={() => setShowCompose(false)}
        />
      )}
    </div>
  )
}

// ── Client Portal management (firm side) ────────────────────────────────────
function isInviteExpired(invite) {
  return Boolean(invite?.expires_at) && new Date(invite.expires_at) < new Date()
}

function inviteState(invite) {
  if (invite.revoked) return { label: 'Revoked', tone: 'text-brand-rose' }
  if (isInviteExpired(invite)) return { label: 'Expired', tone: 'text-brand-muted' }
  if (invite.accepted_at) return { label: 'Active', tone: 'text-brand-green' }
  return { label: 'Awaiting first sign-in', tone: 'text-brand-amber' }
}

function formatPortalTimestamp(value) {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function ClientPortalTab({ matterId, matter }) {
  const [invites, setInvites] = useState([])
  const [email, setEmail] = useState(matter?.client?.email || '')
  const [creating, setCreating] = useState(false)
  const [revokeExisting, setRevokeExisting] = useState(false)
  const [lastUrl, setLastUrl] = useState('')
  const [copied, setCopied] = useState(false)
  const [deliveryWarning, setDeliveryWarning] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setErr('')
    listMatterPortalInvites(matterId)
      .then(setInvites)
      .catch(() => setErr('Unable to load portal invitations. Please refresh and try again.'))
  }, [matterId])
  useEffect(() => { load() }, [load])

  const invite = async (e) => {
    e.preventDefault()
    setErr(''); setLastUrl(''); setDeliveryWarning(''); setCopied(false); setCreating(true)
    try {
      const res = await createMatterPortalInvite(matterId, {
        ...(email ? { email } : {}),
        revoke_existing: revokeExisting,
      })
      setLastUrl(res.invite_url || '')
      setDeliveryWarning(res.email_sent === false ? (res.delivery_error || 'Email delivery was not confirmed. Copy and share the invite link manually.') : '')
      load()
    } catch (e2) {
      setErr(e2?.response?.data?.detail || 'Failed to create invite')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (inviteId) => {
    setErr('')
    try {
      await revokeMatterPortalInvite(matterId, inviteId)
      load()
    } catch (e2) {
      setErr(e2?.response?.data?.detail || 'Failed to revoke invite')
    }
  }

  const copyInviteLink = async () => {
    try {
      await navigator.clipboard.writeText(lastUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access can be blocked; the link is on screen to copy by hand.
      setErr('Could not copy automatically — select the link above and copy it.')
    }
  }

  const activeInvites = invites.filter((i) => !i.revoked && !isInviteExpired(i))

  return (
    <div className="space-y-8">
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
        <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
          <Icon d={Icons.users} size={18} className="text-brand-accent" /> Client Portal
        </h2>
        <p className="text-[13px] text-brand-muted font-sans mt-0.5">
          Invite the client to a secure, matter-scoped portal to view status, exchange messages, share documents, and pay invoices.
        </p>
      </div>

      <div className="p-6 space-y-6">
        <form onSubmit={invite} className="flex flex-col sm:flex-row gap-3 sm:items-end">
          <div className="flex-1">
            <label htmlFor="matterdetailpage-client-email" className="block text-xs font-sans font-semibold text-brand-muted mb-1">Client email</label>
            <input id="matterdetailpage-client-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="client@example.com"
              className="w-full border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
            />
          </div>
          <button
            type="submit"
            disabled={creating}
            className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50"
          >
            {creating ? 'Sending…' : 'Send portal invite'}
          </button>
        </form>

        {activeInvites.length > 0 && (
          <label className="flex items-start gap-2 text-xs text-brand-muted font-sans">
            <input
              type="checkbox"
              checked={revokeExisting}
              onChange={(e) => setRevokeExisting(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Revoke the {activeInvites.length} live invitation
              {activeInvites.length === 1 ? '' : 's'} on this matter when sending.
              Use this when re-issuing a link to the same person; leave it off to
              give a second recipient their own access.
            </span>
          </label>
        )}

        {err && <p className="text-sm text-brand-rose">{err}</p>}
        {lastUrl && (
          <div className="bg-brand-green/10 border border-brand-green/20 rounded-lg px-4 py-3 text-sm">
            <div className="flex items-start justify-between gap-3 mb-1">
              <p className="text-brand-ink font-medium">
                {deliveryWarning ? 'Invite created. Shareable link:' : 'Invite sent. Shareable link:'}
              </p>
              <button
                type="button"
                onClick={copyInviteLink}
                className="text-xs font-sans font-semibold text-brand-accent hover:underline shrink-0"
              >
                {copied ? 'Copied' : 'Copy link'}
              </button>
            </div>
            <code className="text-xs text-brand-ink-2 break-all">{lastUrl}</code>
            <p className="text-xs text-brand-muted mt-2">
              This link is shown once and grants access to the matter — share it
              with the client directly, never in a group thread.
            </p>
            {deliveryWarning && <p className="text-xs text-brand-rose mt-2">{deliveryWarning}</p>}
          </div>
        )}

        <div>
          <h3 className="text-sm font-sans font-semibold text-brand-ink mb-2">Invitations</h3>
          {invites.length === 0 ? (
            <p className="text-sm text-brand-muted">No invitations yet.</p>
          ) : (
            <ul className="divide-y divide-brand-line border border-brand-line rounded-lg">
              {invites.map((inv) => {
                const state = inviteState(inv)
                const lastSeen = formatPortalTimestamp(inv.last_seen_at)
                return (
                  <li key={inv.id} className="flex items-start justify-between gap-3 px-4 py-3 text-sm">
                    <div className="min-w-0">
                      <p className="text-brand-ink truncate">{inv.email || '—'}</p>
                      <p className="text-xs">
                        <span className={`font-medium ${state.tone}`}>{state.label}</span>
                        <span className="text-brand-muted">
                          {' · expires '}{new Date(inv.expires_at).toLocaleDateString()}
                        </span>
                      </p>
                      <p className="text-xs text-brand-muted">
                        {lastSeen ? `Last active ${lastSeen}` : 'Never opened'}
                      </p>
                    </div>
                    {!inv.revoked && (
                      <button
                        onClick={() => revoke(inv.id)}
                        className="text-brand-rose hover:underline text-xs font-medium shrink-0"
                      >
                        Revoke
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <p className="text-xs text-brand-muted">
          Tip: mark documents as portal-visible in the Documents tab to share them with the client. Documents the client uploads appear there automatically.
        </p>
      </div>
    </div>

    <SignatureRequestsPanel matterId={matterId} />
    </div>
  )
}

// ── E-signature requests (firm side) ────────────────────────────────────────
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

const SIGNER_ROLE_OPTIONS = [
  { value: 'client', label: 'Client' },
  { value: 'co_client', label: 'Co-client' },
  { value: 'attorney_countersigner', label: 'Attorney countersigner' },
  { value: 'witness', label: 'Witness' },
  { value: 'signer', label: 'Signer' },
]

const newSignerRow = () => ({ name: '', email: '', role: 'client' })

function formatSignerRole(role) {
  const match = SIGNER_ROLE_OPTIONS.find((option) => option.value === role)
  return match ? match.label : (role || 'Signer').replace(/_/g, ' ')
}

function signerStatusLabel(signer) {
  if (signer.status === 'signed') return `Signed ${formatSignatureDate(signer.signed_at)}`
  if (signer.status === 'declined') return `Declined ${formatSignatureDate(signer.declined_at)}`
  return 'Pending signature'
}

function SignatureRequestsPanel({ matterId }) {
  const [requests, setRequests] = useState([])
  const [docs, setDocs] = useState([])
  const [docId, setDocId] = useState('')
  const [signers, setSigners] = useState([newSignerRow()])
  const [expiresOn, setExpiresOn] = useState('')
  const [reminderDays, setReminderDays] = useState('7,1')
  const [enforceSigningOrder, setEnforceSigningOrder] = useState(true)
  const [voidReasonById, setVoidReasonById] = useState({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(() => {
    listSignatureRequests(matterId).then(setRequests).catch(() => {})
    getMatterDocuments(matterId)
      .then((data) => setDocs(Array.isArray(data) ? data : data.items || []))
      .catch(() => {})
  }, [matterId])
  useEffect(() => { load() }, [load])

  const counts = requests.reduce((acc, r) => {
    acc[r.status] = (acc[r.status] || 0) + 1
    return acc
  }, {})

  const updateSigner = (idx, key, value) => {
    setSigners((prev) => prev.map((row, i) => i === idx ? { ...row, [key]: value } : row))
  }

  const addSigner = () => setSigners((prev) => [...prev, newSignerRow()])

  const removeSigner = (idx) => {
    setSigners((prev) => prev.length === 1 ? prev : prev.filter((_, i) => i !== idx))
  }

  const create = async (e) => {
    e.preventDefault()
    setErr('')
    setNotice('')
    if (!docId) { setErr('Choose a document to send for signature.'); return }
    const preparedSigners = signers.map((s, idx) => ({
      name: s.name.trim(),
      email: s.email.trim(),
      role: s.role || 'signer',
      sign_order: idx,
    }))
    if (preparedSigners.some((s) => !s.name || !s.email)) {
      setErr('Each signer needs a name and email.')
      return
    }
    const parsedReminderDays = reminderDays
      .split(/[\s,]+/)
      .map((value) => Number.parseInt(value, 10))
      .filter((value) => Number.isInteger(value) && value > 0)
    setBusy(true)
    try {
      const req = await createSignatureRequest(matterId, {
        document_id: docId,
        signers: preparedSigners,
        expires_at: expiresOn ? new Date(`${expiresOn}T23:59:59`).toISOString() : null,
        reminder_days: parsedReminderDays,
        enforce_signing_order: enforceSigningOrder,
      })
      await sendSignatureRequest(matterId, req.id)
      setSigners([newSignerRow()])
      setDocId('')
      setExpiresOn('')
      setReminderDays('7,1')
      setEnforceSigningOrder(true)
      setNotice('Signature request sent. Signers will see it in their client portal Signatures tab when it is their turn.')
      load()
    } catch (e2) {
      setErr(e2?.response?.data?.detail || 'Failed to create signature request.')
    } finally {
      setBusy(false)
    }
  }

  const voidReq = async (id) => {
    setErr('')
    setNotice('')
    try {
      const reason = (voidReasonById[id] || '').trim()
      await voidSignatureRequest(matterId, id, reason ? { reason } : undefined)
      setNotice('Signature request voided.')
      setVoidReasonById((prev) => ({ ...prev, [id]: '' }))
      load()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to void signature request.')
    }
  }

  const resendReq = async (id) => {
    setErr('')
    setNotice('')
    try {
      await resendSignatureRequest(matterId, id)
      setNotice('Signature invitation resent to the signer or signers who can act now.')
      load()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to resend the signature invitation.')
    }
  }

  const statusBadge = (status) => {
    const styles = {
      completed: 'bg-brand-green/10 text-brand-green border-brand-green/20',
      sent: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
      partially_signed: 'bg-brand-accent/10 text-brand-accent border-brand-accent/20',
      voided: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
      declined: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
      expired: 'bg-brand-bg-soft text-brand-muted border-brand-line',
      draft: 'bg-brand-bg-soft text-brand-muted border-brand-line',
    }
    return styles[status] || styles.draft
  }

  return (
    <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm overflow-hidden">
      <div className="px-6 py-5 border-b border-brand-line bg-gradient-to-r from-brand-bg-soft to-white">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
              <Icon d={Icons.edit} size={18} className="text-brand-accent" /> E-Signature
            </h2>
            <p className="text-[13px] text-brand-muted font-sans mt-0.5">
              Send portal-ready signature requests and track each signer through completion.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-xl border border-brand-line bg-white px-3 py-2"><p className="text-lg font-bold text-brand-ink">{counts.sent || 0}</p><p className="text-[10px] uppercase text-brand-muted">Awaiting</p></div>
            <div className="rounded-xl border border-brand-line bg-white px-3 py-2"><p className="text-lg font-bold text-brand-ink">{counts.partially_signed || 0}</p><p className="text-[10px] uppercase text-brand-muted">Partial</p></div>
            <div className="rounded-xl border border-brand-line bg-white px-3 py-2"><p className="text-lg font-bold text-brand-ink">{counts.completed || 0}</p><p className="text-[10px] uppercase text-brand-muted">Done</p></div>
          </div>
        </div>
      </div>

      <div className="p-6 space-y-6">
        <form onSubmit={create} className="rounded-2xl border border-brand-line bg-white p-4 space-y-4">
          <div>
            <h3 className="text-sm font-sans font-semibold text-brand-ink">New request</h3>
            <p className="text-xs text-brand-muted mt-0.5">Choose a matter document and the portal signers who should sign it.</p>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            <select value={docId} onChange={(e) => setDocId(e.target.value)} className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40">
              <option value="">Select document…</option>
              {docs.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
            </select>
            <input type="date" value={expiresOn} onChange={(e) => setExpiresOn(e.target.value)} className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40" />
            <input value={reminderDays} onChange={(e) => setReminderDays(e.target.value)} placeholder="Reminder days: 7,1" className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40" />
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-brand-muted">
            <input type="checkbox" checked={enforceSigningOrder} onChange={(e) => setEnforceSigningOrder(e.target.checked)} />
            <span>Require signers to complete in listed order</span>
          </label>
          <div className="space-y-2">
            {signers.map((signer, idx) => (
              <div key={idx} className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_190px_auto] gap-2 rounded-xl bg-brand-bg-soft p-3">
                <input value={signer.name} onChange={(e) => updateSigner(idx, 'name', e.target.value)} placeholder={`Signer ${idx + 1} full name`} className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40" />
                <input type="email" value={signer.email} onChange={(e) => updateSigner(idx, 'email', e.target.value)} placeholder="Signer email" className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40" />
                <select value={signer.role} onChange={(e) => updateSigner(idx, 'role', e.target.value)} className="border border-brand-line rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40">
                  {SIGNER_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <button type="button" onClick={() => removeSigner(idx)} disabled={signers.length === 1} className="px-3 py-2 text-xs font-semibold text-brand-rose disabled:text-brand-muted disabled:cursor-not-allowed">Remove</button>
              </div>
            ))}
            <button type="button" onClick={addSigner} className="text-xs font-semibold text-brand-accent hover:text-brand-ink">Add signer</button>
          </div>
          <button type="submit" disabled={busy} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-semibold rounded-lg hover:bg-brand-ink-2 transition-all disabled:opacity-50">
            {busy ? 'Sending…' : 'Send for signature'}
          </button>
        </form>

        {err && <p className="text-sm text-brand-rose">{err}</p>}
        {notice && <p className="text-sm text-brand-green">{notice}</p>}

        <div>
          <h3 className="text-sm font-sans font-semibold text-brand-ink mb-3">Signature queue</h3>
          {requests.length === 0 ? (
            <div className="rounded-xl border border-dashed border-brand-line p-6 text-center">
              <p className="text-sm text-brand-muted">No signature requests yet. Send the first one above.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {requests.map((r) => (
                <div key={r.id} className="border border-brand-line rounded-2xl p-4 bg-white">
                  <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
                    <div>
                      <p className="text-brand-ink font-medium">{r.document_name || 'Document'}</p>
                      <p className="text-xs text-brand-muted mt-1">Sent {formatSignatureDate(r.sent_at)} · Created {formatSignatureDate(r.created_at)}</p>
                      <p className="text-xs text-brand-muted mt-1">
                        Expires {formatSignatureDate(r.expires_at)}
                        {r.enforce_signing_order ? ' · Sequential signing' : ''}
                      </p>
                      {(r.decline_reason || r.void_reason) && (
                        <p className="text-xs text-brand-rose mt-1">{r.decline_reason || r.void_reason}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2.5 py-1 rounded-full border text-xs font-semibold capitalize ${statusBadge(r.status)}`}>{r.status.replace('_', ' ')}</span>
                      {r.signed_document_id && (
                        <a href={getMatterDocumentDownloadUrl(matterId, r.signed_document_id)} className="text-xs font-semibold text-brand-accent hover:text-brand-ink">Download executed copy</a>
                      )}
                      {['sent', 'partially_signed'].includes(r.status) && <button onClick={() => resendReq(r.id)} className="text-brand-accent hover:underline text-xs font-medium">Resend</button>}
                      {!['completed', 'voided', 'declined', 'expired'].includes(r.status) && <button onClick={() => voidReq(r.id)} className="text-brand-rose hover:underline text-xs font-medium">Void</button>}
                    </div>
                  </div>
                  {!['completed', 'voided', 'declined', 'expired'].includes(r.status) && (
                    <input
                      value={voidReasonById[r.id] || ''}
                      onChange={(e) => setVoidReasonById((prev) => ({ ...prev, [r.id]: e.target.value }))}
                      placeholder="Void reason"
                      className="mt-3 w-full border border-brand-line rounded-lg px-3 py-2 text-xs font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                    />
                  )}
                  <div className="mt-3 grid gap-2">
                    {r.signers?.map((s, idx) => (
                      <div key={s.id} className="flex items-center justify-between rounded-lg bg-brand-bg-soft px-3 py-2 text-xs">
                        <span className="text-brand-ink">{idx + 1}. {formatSignerRole(s.role)} · {s.name} · {s.email}</span>
                        <span className="text-right">
                          <span className={s.status === 'signed' ? 'text-brand-green font-semibold' : s.status === 'declined' ? 'text-brand-rose font-semibold' : 'text-brand-amber font-semibold'}>{signerStatusLabel(s)}</span>
                          {s.viewed_at && s.status === 'pending' && <span className="block text-brand-muted">Viewed {formatSignatureDate(s.viewed_at)}</span>}
                          {!s.viewed_at && s.invitation_delivery_status && <span className="block text-brand-muted">Email {s.invitation_delivery_status.replace('_', ' ')}</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
