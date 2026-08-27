import { useState, useEffect, useCallback } from 'react'
import { reportError } from '../utils/reportError'
import { useNavigate } from 'react-router-dom'
import {
  getCalendarEvents,
  syncCalendarDeadlines,
  getCalendarProviders,
  connectCalendarIntegration,
  getZoomStatus,
  connectZoomIntegration,
  createScheduledEvent,
  updateScheduledEvent,
  getMattersV2,
} from '../api'
import {
  ChevronLeft,
  ChevronRight,
  CalendarDays,
  ClipboardList,
  Building2,
  RefreshCw,
  Plus,
  X,
  Video,
  ExternalLink,
  List,
  Clock3,
  GripVertical,
} from 'lucide-react'

// ── helpers ──────────────────────────────────────────────────────────────────

function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

function endOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

function toIso(d) {
  return d.toISOString().slice(0, 10)
}

function formatDisplayDate(isoStr) {
  const [y, m, day] = isoStr.split('-').map(Number)
  const d = new Date(y, m - 1, day)
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
}

function monthLabel(d) {
  return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}

function addDays(d, amount) {
  const next = new Date(d)
  next.setDate(next.getDate() + amount)
  return next
}

function startOfWeek(d) {
  const next = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  next.setDate(next.getDate() - next.getDay())
  return next
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

function dateLabel(d) {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function rangeLabel(view, pivot) {
  if (view === 'day') return pivot.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  if (view === 'week') {
    const start = startOfWeek(pivot)
    const end = addDays(start, 6)
    return start.getMonth() === end.getMonth()
      ? `${start.toLocaleDateString('en-US', { month: 'long' })} ${start.getDate()}–${end.getDate()}, ${end.getFullYear()}`
      : `${dateLabel(start)} – ${dateLabel(end)}`
  }
  return monthLabel(pivot)
}

function viewRange(view, pivot) {
  if (view === 'day') return [pivot, pivot]
  if (view === 'week') {
    const start = startOfWeek(pivot)
    return [start, addDays(start, 6)]
  }
  if (view === 'month') {
    const start = startOfWeek(startOfMonth(pivot))
    return [start, addDays(start, 41)]
  }
  return [startOfMonth(pivot), endOfMonth(new Date(pivot.getFullYear(), pivot.getMonth() + 1, 1))]
}

function eventHour(event) {
  if (!event.start || !String(event.start).includes('T')) return null
  const parsed = new Date(event.start)
  return Number.isNaN(parsed.getTime()) ? null : parsed.getHours() + parsed.getMinutes() / 60
}

function eventDuration(event) {
  const start = new Date(event.start)
  const end = new Date(event.end)
  const duration = end.getTime() - start.getTime()
  return Number.isFinite(duration) && duration > 0 ? duration : 30 * 60 * 1000
}

function movedEventTimes(event, day, hour = null) {
  const original = new Date(event.start)
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour ?? original.getHours(), hour == null ? original.getMinutes() : 0)
  return { start, end: new Date(start.getTime() + eventDuration(event)) }
}

function mapProviderEvents(provider, rows) {
  return (rows || []).map((event) => {
    const date = providerEventDate(event)
    if (!date) return null
    return {
      id: `${provider}-${event.id}`,
      providerEventId: event.id,
      title: event.subject || '(No title)',
      date,
      start: event.start,
      end: event.end,
      event_type: 'external_calendar',
      url: null,
      provider,
      location: event.location || '',
    }
  }).filter(Boolean)
}

function toTimeInput(d) {
  return d.toTimeString().slice(0, 5)
}

function formatEventTime(raw) {
  if (!raw) return null
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

// ── event chip ───────────────────────────────────────────────────────────────

const TYPE_STYLES = {
  task_due: {
    bg: 'bg-blue-50 border-blue-200',
    dot: 'bg-blue-500',
    label: 'bg-blue-100 text-blue-700',
    icon: ClipboardList,
  },
  matter_key_date: {
    bg: 'bg-purple-50 border-purple-200',
    dot: 'bg-purple-500',
    label: 'bg-purple-100 text-purple-700',
    icon: Building2,
  },
  renewal: {
    bg: 'bg-green-50 border-green-200',
    dot: 'bg-green-500',
    label: 'bg-green-100 text-green-700',
    icon: RefreshCw,
  },
  estate_deadline: {
    bg: 'bg-amber-50 border-amber-200',
    dot: 'bg-amber-500',
    label: 'bg-amber-100 text-amber-700',
    icon: CalendarDays,
  },
  external_calendar: {
    bg: 'bg-slate-50 border-slate-200',
    dot: 'bg-slate-500',
    label: 'bg-slate-100 text-slate-700',
    icon: CalendarDays,
  },
  scheduled_event: {
    bg: 'bg-cyan-50 border-cyan-200',
    dot: 'bg-cyan-500',
    label: 'bg-cyan-100 text-cyan-700',
    icon: Video,
  },
}

const TYPE_LABELS = {
  task_due: 'Task',
  matter_key_date: 'Key Date',
  renewal: 'Renewal',
  estate_deadline: 'Estate',
  external_calendar: 'Synced',
  scheduled_event: 'Event',
}

function providerLabel(provider) {
  return provider === 'google' ? 'Google Calendar' : 'Microsoft Calendar'
}

function providerEventDate(evt) {
  const raw = evt.start || evt.end
  if (!raw) return null
  return String(raw).slice(0, 10)
}

function mergeCalendarEvents(internalEvents, providerEvents) {
  const seen = new Set()
  return [...internalEvents, ...providerEvents].filter((event) => {
    const key = event.id || `${event.event_type}-${event.date}-${event.title}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function EventChip({ event, onClick, onDragStart }) {
  const styles = TYPE_STYLES[event.event_type] || TYPE_STYLES.task_due
  const Icon = styles.icon
  const timeLabel = formatEventTime(event.start)
  const meetingLabel = event.meeting_provider === 'teams'
    ? 'Teams'
    : event.meeting_provider === 'zoom'
      ? 'Zoom'
      : null

  return (
    <button
      onClick={onClick}
      draggable={Boolean(onDragStart)}
      onDragStart={onDragStart}
      className={`w-full text-left flex items-start gap-3 px-3 py-2.5 rounded border ${styles.bg} hover:brightness-95 transition-all group ${onDragStart ? 'cursor-grab active:cursor-grabbing' : ''}`}
    >
      <div className={`mt-1 w-2 h-2 rounded-full shrink-0 ${styles.dot}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-brand-ink leading-tight truncate">
            {event.title}
          </span>
          <span className={`shrink-0 text-[10px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${styles.label}`}>
            {TYPE_LABELS[event.event_type] || event.event_type}
          </span>
        </div>
        {event.matter_name && event.event_type !== 'matter_key_date' && (
          <p className="text-xs text-brand-muted mt-0.5 truncate">{event.matter_name}</p>
        )}
        {(timeLabel || meetingLabel) && (
          <p className="text-xs text-brand-muted mt-0.5 truncate">
            {[timeLabel, meetingLabel].filter(Boolean).join(' · ')}
          </p>
        )}
      </div>
      <Icon className="w-3.5 h-3.5 text-brand-muted shrink-0 mt-0.5 opacity-60 group-hover:opacity-100 transition-opacity" />
      {onDragStart && <GripVertical className="w-3.5 h-3.5 text-brand-muted/50 shrink-0 mt-0.5" />}
    </button>
  )
}

function MonthView({ pivotDate, grouped, onEventClick, onSelectDay, onMoveEvent }) {
  const gridStart = startOfWeek(startOfMonth(pivotDate))
  const days = Array.from({ length: 42 }, (_, index) => addDays(gridStart, index))
  return (
    <div className="h-full min-w-[720px] grid grid-cols-7 grid-rows-[36px_repeat(6,minmax(96px,1fr))] bg-brand-line gap-px border border-brand-line rounded-xl overflow-hidden shadow-sm">
      {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
        <div key={day} className="bg-brand-surface-2 flex items-center justify-center text-[11px] font-semibold uppercase tracking-wider text-brand-muted">{day}</div>
      ))}
      {days.map((day) => {
        const iso = toIso(day)
        const dayEvents = grouped[iso] || []
        const muted = day.getMonth() !== pivotDate.getMonth()
        const today = sameDay(day, new Date())
        return (
          <div key={iso} onDragOver={(event) => event.preventDefault()} onDrop={(event) => onMoveEvent(event, day)} className={`bg-brand-surface min-h-0 p-1.5 overflow-hidden transition-colors ${today ? 'ring-2 ring-inset ring-brand-accent/50 bg-brand-accent/5' : ''} ${muted ? 'bg-brand-bg/70' : ''}`}>
            <button onClick={() => onSelectDay(day)} className={`ml-auto mb-1 flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium ${today ? 'bg-brand-accent text-white shadow-sm' : muted ? 'text-brand-muted/60 hover:bg-brand-line' : 'text-brand-ink hover:bg-brand-line'}`}>{day.getDate()}</button>
            <div className="space-y-1">
              {dayEvents.slice(0, 3).map((event) => {
                const styles = TYPE_STYLES[event.event_type] || TYPE_STYLES.task_due
                const movable = event.event_type === 'scheduled_event'
                return <button key={event.id} draggable={movable} onDragStart={movable ? (drag) => drag.dataTransfer.setData('text/calendar-event-id', event.id) : undefined} onClick={() => onEventClick(event)} className={`w-full truncate rounded-md border-l-[3px] px-1.5 py-1 text-left text-[11px] font-medium text-brand-ink shadow-sm ${styles.bg} ${movable ? 'cursor-grab active:cursor-grabbing' : ''}`}>{formatEventTime(event.start) && <span className="mr-1 font-normal text-brand-muted">{formatEventTime(event.start)}</span>}{event.title}</button>
              })}
              {dayEvents.length > 3 && <button onClick={() => onSelectDay(day)} className="px-1 text-[10px] font-semibold text-brand-accent hover:underline">+{dayEvents.length - 3} more</button>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function CurrentTimeLine() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60 * 1000)
    return () => window.clearInterval(timer)
  }, [])
  const top = (now.getHours() + now.getMinutes() / 60) * 64
  return <div className="pointer-events-none absolute inset-x-0 z-20 flex items-center" style={{ top }}><span className="-ml-1 h-2.5 w-2.5 rounded-full bg-red-500 shadow-sm" /><span className="h-0.5 flex-1 bg-red-500" /></div>
}

function TimeGridView({ view, pivotDate, grouped, onEventClick, onMoveEvent }) {
  const first = view === 'day' ? pivotDate : startOfWeek(pivotDate)
  const days = Array.from({ length: view === 'day' ? 1 : 7 }, (_, index) => addDays(first, index))
  const hours = Array.from({ length: 24 }, (_, index) => index)
  return (
    <div className="h-full overflow-auto rounded-xl border border-brand-line bg-brand-surface shadow-sm">
      <div className="sticky top-0 z-20 grid bg-brand-surface-2 border-b border-brand-line" style={{ gridTemplateColumns: `64px repeat(${days.length}, minmax(${view === 'day' ? 520 : 120}px, 1fr))` }}>
        <div className="border-r border-brand-line" />
        {days.map((day) => {
          const today = sameDay(day, new Date())
          return <div key={toIso(day)} className="h-16 border-r border-brand-line flex flex-col items-center justify-center"><span className="text-[10px] uppercase tracking-wider text-brand-muted">{day.toLocaleDateString('en-US', { weekday: 'short' })}</span><span className={`mt-1 flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold ${today ? 'bg-brand-accent text-white' : 'text-brand-ink'}`}>{day.getDate()}</span></div>
        })}
      </div>
      <div className="grid relative" style={{ gridTemplateColumns: `64px repeat(${days.length}, minmax(${view === 'day' ? 520 : 120}px, 1fr))` }}>
        <div>{hours.map((hour) => <div key={hour} className="h-16 border-r border-b border-brand-line pr-2 pt-1 text-right text-[10px] text-brand-muted">{hour === 0 ? '' : new Date(2000, 0, 1, hour).toLocaleTimeString('en-US', { hour: 'numeric' })}</div>)}</div>
        {days.map((day) => {
          const dayEvents = grouped[toIso(day)] || []
          const allDay = dayEvents.filter((event) => eventHour(event) == null)
          return <div key={toIso(day)} className={`relative border-r border-brand-line ${sameDay(day, new Date()) ? 'bg-brand-accent/[0.035]' : ''}`}>
            {sameDay(day, new Date()) && <CurrentTimeLine />}
            {hours.map((hour) => <div key={hour} onDragOver={(event) => event.preventDefault()} onDrop={(event) => onMoveEvent(event, day, hour)} className="h-16 border-b border-brand-line/80 transition-colors hover:bg-brand-accent/5" />)}
            {allDay.length > 0 && <div className="absolute top-1 inset-x-1 z-10 space-y-1">{allDay.slice(0, 2).map((event) => <EventChip key={event.id} event={event} onClick={() => onEventClick(event)} />)}</div>}
            {dayEvents.filter((event) => eventHour(event) != null).map((event) => {
              const styles = TYPE_STYLES[event.event_type] || TYPE_STYLES.task_due
              const movable = event.event_type === 'scheduled_event'
              return <button key={event.id} draggable={movable} onDragStart={movable ? (drag) => drag.dataTransfer.setData('text/calendar-event-id', event.id) : undefined} onClick={() => onEventClick(event)} className={`absolute z-10 left-1 right-1 min-h-12 rounded-lg border-l-4 px-2 py-1.5 text-left shadow-sm overflow-hidden ${styles.bg} ${movable ? 'cursor-grab active:cursor-grabbing' : ''}`} style={{ top: `${eventHour(event) * 64 + 2}px` }}><span className="block truncate text-xs font-semibold text-brand-ink">{event.title}</span><span className="text-[10px] text-brand-muted">{formatEventTime(event.start)}</span></button>
            })}
          </div>
        })}
      </div>
    </div>
  )
}

function MobileAgenda({ events, onEventClick }) {
  const sorted = [...events].sort((a, b) => `${a.date}${a.start || ''}`.localeCompare(`${b.date}${b.start || ''}`))
  return <div className="md:hidden space-y-3">
    {sorted.map((event) => <div key={event.id} className={`rounded-xl ${event.date === toIso(new Date()) ? 'ring-2 ring-brand-accent/40' : ''}`}><div className="mb-1 px-1 text-[10px] font-bold uppercase tracking-wider text-brand-muted">{event.date === toIso(new Date()) ? 'Today' : formatDisplayDate(event.date)}</div><EventChip event={event} onClick={() => onEventClick(event)} /></div>)}
    {sorted.length === 0 && <div className="py-16 text-center text-sm text-brand-muted">No events in this period.</div>}
  </div>
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function CalendarPage() {
  const navigate = useNavigate()
  const [pivotDate, setPivotDate] = useState(new Date())
  const [view, setView] = useState(() => window.localStorage.getItem('calendar-view') || 'month')
  const [events, setEvents] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState(null) // { type: 'success'|'error', text: string }
  const [calendarProvider, setCalendarProvider] = useState(null)
  const [connectProvider, setConnectProvider] = useState(null)
  const [providerEvents, setProviderEvents] = useState([])
  const [providerStatus, setProviderStatus] = useState({})
  const [zoomStatus, setZoomStatus] = useState(null)
  const [matters, setMatters] = useState([])
  const [showEventModal, setShowEventModal] = useState(false)
  const [eventSaving, setEventSaving] = useState(false)

  useEffect(() => {
    Promise.all([
      getCalendarProviders(),
      getZoomStatus().catch(() => ({ connected: false, configured: false })),
      getMattersV2({ page_size: 200 }).catch(() => ({ items: [] })),
    ])
      .then(([data, zoom, mattersData]) => {
        const connectedProvider = data.providers?.[0] || null
        const status = data.provider_status || {}
        setProviderStatus(status)
        setZoomStatus(zoom)
        setMatters(mattersData.items || mattersData || [])
        const reconnectProvider = ['microsoft', 'google'].find((provider) => status[provider]?.needs_reconnect)
        if (connectedProvider) {
          setCalendarProvider(connectedProvider)
        } else if (reconnectProvider) {
          setCalendarProvider(null)
          setSyncMessage({
            type: 'error',
            text: `${providerLabel(reconnectProvider)} needs to be reconnected before sync can run.`,
          })
        }
        setConnectProvider(data.connect_provider || reconnectProvider || data.login_provider || data.tenant_providers?.[0] || null)
      })
      .catch(() => {})
  }, [])

  const fetchEvents = useCallback(async (pivot, activeView = view) => {
    setLoading(true)
    setError(null)
    try {
      const [som, eom] = viewRange(activeView, pivot)
      const data = await getCalendarEvents(toIso(som), toIso(eom))
      const visibleProviderEvents = providerEvents.filter((event) => event.date >= toIso(som) && event.date <= toIso(eom))
      const mergedEvents = mergeCalendarEvents(data.events || [], visibleProviderEvents)
      setEvents(mergedEvents)
      setTotal(mergedEvents.length)
    } catch (err) {
      setError('Failed to load calendar events.')
      reportError(err)
    } finally {
      setLoading(false)
    }
  }, [providerEvents, view])

  useEffect(() => {
    fetchEvents(pivotDate, view)
  }, [pivotDate, view, fetchEvents])

  useEffect(() => {
    if (!calendarProvider) return undefined
    let active = true
    const pullProviderChanges = async () => {
      try {
        const result = await syncCalendarDeadlines(calendarProvider, false)
        if (active) setProviderEvents(mapProviderEvents(calendarProvider, result.events))
      } catch {
        // Connection health messaging is handled by explicit sync and provider status.
      }
    }
    pullProviderChanges()
    const timer = window.setInterval(pullProviderChanges, 5 * 60 * 1000)
    return () => { active = false; window.clearInterval(timer) }
  }, [calendarProvider])

  const move = (direction) => setPivotDate((date) => {
    if (view === 'day') return addDays(date, direction)
    if (view === 'week') return addDays(date, direction * 7)
    return new Date(date.getFullYear(), date.getMonth() + direction, 1)
  })

  const changeView = (nextView) => {
    setView(nextView)
    window.localStorage.setItem('calendar-view', nextView)
  }

  // Group events by ISO date string
  const grouped = events.reduce((acc, ev) => {
    const key = ev.date
    if (!acc[key]) acc[key] = []
    acc[key].push(ev)
    return acc
  }, {})

  const sortedDates = Object.keys(grouped).sort()

  const handleEventClick = (event) => {
    if (event.join_url) {
      window.open(event.join_url, '_blank', 'noopener,noreferrer')
      return
    }
    if (event.url?.startsWith('http')) {
      window.open(event.url, '_blank', 'noopener,noreferrer')
      return
    }
    if (event.url) navigate(event.url)
  }

  const handleMoveEvent = async (dragEvent, day, hour = null) => {
    dragEvent.preventDefault()
    const eventId = dragEvent.dataTransfer.getData('text/calendar-event-id')
    const event = events.find((candidate) => candidate.id === eventId)
    if (!event || event.event_type !== 'scheduled_event') return
    const { start, end } = movedEventTimes(event, day, hour)
    const previousEvents = events
    setEvents((current) => current.map((candidate) => candidate.id === eventId ? { ...candidate, date: toIso(start), start: start.toISOString(), end: end.toISOString() } : candidate))
    try {
      await updateScheduledEvent(eventId.replace('scheduled-', ''), { start_at: start.toISOString(), end_at: end.toISOString() })
      setSyncMessage({ type: 'success', text: `Moved “${event.title}” and updated the connected calendar.` })
      await fetchEvents(pivotDate, view)
    } catch (err) {
      setEvents(previousEvents)
      setSyncMessage({ type: 'error', text: err?.response?.data?.detail || 'The event could not be moved.' })
    }
  }

  const handleSync = async (provider) => {
    setSyncing(true)
    setSyncMessage(null)
    try {
      const result = await syncCalendarDeadlines(provider)
      const mappedProviderEvents = mapProviderEvents(provider, result.events)
      setProviderEvents(mappedProviderEvents)
      const mergedEvents = mergeCalendarEvents(
        events.filter((event) => event.event_type !== 'external_calendar'),
        mappedProviderEvents,
      )
      setEvents(mergedEvents)
      setTotal(mergedEvents.length)
      setSyncMessage({
        type: 'success',
        text: `Synced ${result.deadlines_created ?? 0} deadline(s) and loaded ${mappedProviderEvents.length} ${provider === 'google' ? 'Google Calendar' : 'Microsoft Calendar'} event(s).`,
      })
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        'Calendar sync failed. Please try again.'
      if (err?.response?.status === 424) {
        setCalendarProvider(null)
        setConnectProvider(provider)
      }
      setSyncMessage({ type: 'error', text: `Calendar sync failed: ${detail}` })
    } finally {
      setSyncing(false)
    }
  }

  const handleConnectCalendar = () => {
    const provider = connectProvider || 'microsoft'
    connectCalendarIntegration(provider)
  }

  const handleCreateScheduledEvent = async (form) => {
    setEventSaving(true)
    setSyncMessage(null)
    try {
      await createScheduledEvent({
        title: form.title,
        description: form.description || null,
        start_at: `${form.date}T${form.start_time}:00`,
        end_at: `${form.date}T${form.end_time}:00`,
        timezone: form.timezone || 'UTC',
        attendees: form.attendees
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        matter_id: form.matter_id || null,
        calendar_provider: form.calendar_provider || null,
        meeting_provider: form.meeting_provider || 'none',
      })
      setShowEventModal(false)
      setSyncMessage({ type: 'success', text: 'Event created.' })
      await fetchEvents(pivotDate)
    } catch (err) {
      setSyncMessage({
        type: 'error',
        text: err?.response?.data?.detail || 'Failed to create event.',
      })
    } finally {
      setEventSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-brand-bg">
      {/* Header */}
      <div className="min-h-16 flex flex-wrap items-center gap-y-2 px-4 md:px-6 py-3 border-b border-brand-line bg-brand-surface-2 shrink-0">
        <CalendarDays className="w-5 h-5 mr-2 text-brand-accent" strokeWidth={1.5} />
        <h1 className="font-serif font-semibold text-lg text-brand-ink">Deadline Calendar</h1>
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden lg:inline text-xs text-brand-muted font-mono">
            {loading ? 'Loading…' : `${total} event${total !== 1 ? 's' : ''}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowEventModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-brand-line bg-brand-surface hover:bg-brand-line/40 text-brand-ink transition-colors"
              title="Create a scheduled event or online meeting"
            >
              <Plus className="w-3 h-3" />
              <span className="hidden sm:inline">New Event</span>
            </button>
            {calendarProvider ? (
              <button
                onClick={() => handleSync(calendarProvider)}
                disabled={syncing}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-brand-line bg-brand-surface hover:bg-brand-line/40 text-brand-ink disabled:opacity-50 transition-colors"
                title={`Sync matter deadlines to ${calendarProvider === 'google' ? 'Google' : 'Microsoft'} Calendar`}
              >
                <RefreshCw className={`w-3 h-3 ${syncing ? 'animate-spin' : ''}`} />
                <span className="hidden sm:inline">{syncing ? 'Syncing…' : 'Sync Calendar'}</span>
              </button>
            ) : (
              <button
                onClick={handleConnectCalendar}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-brand-line bg-brand-surface hover:bg-brand-line/40 text-brand-ink transition-colors"
                title="Connect your calendar to sync events"
              >
                <RefreshCw className="w-3 h-3" />
                <span className="hidden sm:inline">Connect Calendar</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Sync message banner */}
      {syncMessage && (
        <div
          className={`px-6 py-2 text-xs font-medium flex items-center justify-between ${
            syncMessage.type === 'error'
              ? 'bg-red-50 text-red-700 border-b border-red-200'
              : 'bg-green-50 text-green-700 border-b border-green-200'
          }`}
        >
          <span>{syncMessage.text}</span>
          <button
            onClick={() => setSyncMessage(null)}
            className="ml-4 opacity-60 hover:opacity-100 text-xs"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      {/* Calendar navigation */}
      <div className="flex flex-wrap items-center gap-2 px-3 md:px-6 py-3 border-b border-brand-line bg-brand-surface-2 shrink-0">
        <button onClick={() => setPivotDate(new Date())} className="h-8 px-3 rounded-lg border border-brand-line bg-brand-surface text-xs font-semibold text-brand-ink hover:bg-brand-line/40">Today</button>
        <button
          onClick={() => move(-1)}
          className="p-1.5 rounded hover:bg-brand-line transition-colors text-brand-muted hover:text-brand-ink"
          aria-label={`Previous ${view}`}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="order-first w-full md:order-none md:w-auto font-serif font-semibold text-base text-brand-ink md:min-w-56 text-center">
          {rangeLabel(view, pivotDate)}
        </span>
        <button
          onClick={() => move(1)}
          className="p-1.5 rounded hover:bg-brand-line transition-colors text-brand-muted hover:text-brand-ink"
          aria-label={`Next ${view}`}
        >
          <ChevronRight className="w-4 h-4" />
        </button>

        <div className="ml-auto max-w-full overflow-x-auto inline-flex rounded-lg border border-brand-line bg-brand-bg p-0.5" aria-label="Calendar view">
          {[['day', 'Day', Clock3], ['week', 'Week', CalendarDays], ['month', 'Month', CalendarDays], ['list', 'List', List]].map(([key, label, Icon]) => (
            <button key={key} onClick={() => changeView(key)} aria-pressed={view === key} className={`flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-semibold transition-colors ${view === key ? 'bg-brand-surface text-brand-ink shadow-sm' : 'text-brand-muted hover:text-brand-ink'}`}><Icon className="h-3.5 w-3.5" />{label}</button>
          ))}
        </div>
      </div>

      {/* Agenda body */}
      <div className="flex-1 overflow-y-auto px-3 py-4 md:px-6 md:py-6">
        {error && (
          <div className="text-center py-16 text-brand-rose text-sm">{error}</div>
        )}

        {!error && !loading && sortedDates.length === 0 && view === 'list' && (
          <div className="text-center py-16">
            <CalendarDays className="w-10 h-10 text-brand-muted mx-auto mb-3" strokeWidth={1} />
            <p className="text-brand-muted text-sm">No deadlines in this period.</p>
          </div>
        )}

        {!error && loading && (
          <div className="text-center py-16 text-brand-muted text-sm animate-pulse">
            Loading events…
          </div>
        )}

        {!error && !loading && view !== 'list' && <MobileAgenda events={events} onEventClick={handleEventClick} />}

        {!error && !loading && view === 'month' && (
          <div className="hidden md:block h-full"><MonthView pivotDate={pivotDate} grouped={grouped} onEventClick={handleEventClick} onMoveEvent={handleMoveEvent} onSelectDay={(day) => { setPivotDate(day); changeView('day') }} /></div>
        )}

        {!error && !loading && (view === 'day' || view === 'week') && (
          <div className="hidden md:block h-full"><TimeGridView view={view} pivotDate={pivotDate} grouped={grouped} onEventClick={handleEventClick} onMoveEvent={handleMoveEvent} /></div>
        )}

        {!error && !loading && sortedDates.length > 0 && view === 'list' && (
          <div className="max-w-2xl mx-auto space-y-6">
            {sortedDates.map((isoDate) => {
              const dayEvents = grouped[isoDate]
              const isToday = isoDate === toIso(new Date())
              return (
                <div key={isoDate}>
                  {/* Date header */}
                  <div className="flex items-center gap-3 mb-2">
                    <div
                      className={`text-xs font-semibold uppercase tracking-widest px-2 py-0.5 rounded ${
                        isToday
                          ? 'bg-brand-accent text-white'
                          : 'bg-brand-line text-brand-muted'
                      }`}
                    >
                      {isToday ? 'Today' : formatDisplayDate(isoDate)}
                    </div>
                    {isToday && (
                      <span className="text-xs text-brand-muted font-mono">
                        {formatDisplayDate(isoDate)}
                      </span>
                    )}
                    <div className="flex-1 h-px bg-brand-line" />
                    <span className="text-[10px] font-mono text-brand-muted">
                      {dayEvents.length}
                    </span>
                  </div>

                  {/* Event chips */}
                  <div className="space-y-1.5 pl-2">
                    {dayEvents.map((ev) => (
                      <EventChip
                        key={ev.id}
                        event={ev}
                        onClick={() => handleEventClick(ev)}
                      />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      {showEventModal && (
        <ScheduledEventModal
          onClose={() => setShowEventModal(false)}
          onSubmit={handleCreateScheduledEvent}
          saving={eventSaving}
          matters={matters}
          providerStatus={providerStatus}
          zoomStatus={zoomStatus}
          defaultCalendarProvider={calendarProvider}
          onConnectZoom={() => connectZoomIntegration('user')}
        />
      )}
    </div>
  )
}

function ScheduledEventModal({
  onClose,
  onSubmit,
  saving,
  matters,
  providerStatus,
  zoomStatus,
  defaultCalendarProvider,
  onConnectZoom,
}) {
  const now = new Date()
  const start = new Date(now.getTime() + 60 * 60 * 1000)
  start.setMinutes(start.getMinutes() < 30 ? 30 : 0, 0, 0)
  const end = new Date(start.getTime() + 30 * 60 * 1000)
  const microsoftConnected = Boolean(providerStatus?.microsoft?.connected)
  const googleConnected = Boolean(providerStatus?.google?.connected)
  const zoomConnected = Boolean(zoomStatus?.connected)
  const initialCalendar =
    defaultCalendarProvider ||
    (microsoftConnected ? 'microsoft' : googleConnected ? 'google' : '')
  const [form, setForm] = useState({
    title: '',
    description: '',
    date: toIso(start),
    start_time: toTimeInput(start),
    end_time: toTimeInput(end),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    attendees: '',
    matter_id: '',
    calendar_provider: initialCalendar,
    meeting_provider: 'none',
  })

  const setField = (name, value) => {
    setForm((current) => {
      const next = { ...current, [name]: value }
      if (name === 'meeting_provider' && value === 'teams') {
        next.calendar_provider = 'microsoft'
      }
      if (name === 'calendar_provider' && value !== 'microsoft' && current.meeting_provider === 'teams') {
        next.meeting_provider = 'none'
      }
      return next
    })
  }

  const handleSubmit = (event) => {
    event.preventDefault()
    onSubmit(form)
  }

  return (
    <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-xl bg-brand-surface border border-brand-line rounded-xl shadow-xl overflow-hidden"
      >
        <div className="h-14 px-5 border-b border-brand-line flex items-center gap-3">
          <Video className="w-4 h-4 text-brand-accent" />
          <h2 className="font-serif font-semibold text-base text-brand-ink">Create event</h2>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto p-1.5 rounded hover:bg-brand-line text-brand-muted hover:text-brand-ink"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <input
            value={form.title}
            onChange={(e) => setField('title', e.target.value)}
            required
            placeholder="Event title"
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          />
          <textarea
            value={form.description}
            onChange={(e) => setField('description', e.target.value)}
            placeholder="Description"
            rows={3}
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          />

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              type="date"
              value={form.date}
              onChange={(e) => setField('date', e.target.value)}
              required
              className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink"
            />
            <input
              type="time"
              value={form.start_time}
              onChange={(e) => setField('start_time', e.target.value)}
              required
              className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink"
            />
            <input
              type="time"
              value={form.end_time}
              onChange={(e) => setField('end_time', e.target.value)}
              required
              className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <select
              value={form.calendar_provider}
              onChange={(e) => setField('calendar_provider', e.target.value)}
              disabled={form.meeting_provider === 'teams'}
              className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink disabled:opacity-60"
            >
              <option value="">App calendar only</option>
              {microsoftConnected && <option value="microsoft">Microsoft Calendar</option>}
              {googleConnected && <option value="google">Google Calendar</option>}
            </select>
            <select
              value={form.meeting_provider}
              onChange={(e) => setField('meeting_provider', e.target.value)}
              className="px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink"
            >
              <option value="none">No online meeting</option>
              {microsoftConnected && <option value="teams">Microsoft Teams</option>}
              {zoomConnected && <option value="zoom">Zoom</option>}
            </select>
          </div>

          {!zoomConnected && (
            <button
              type="button"
              onClick={onConnectZoom}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-brand-ink border border-brand-line rounded-lg px-3 py-1.5 hover:bg-brand-bg"
            >
              <ExternalLink className="w-3 h-3" />
              Connect Zoom
            </button>
          )}

          <select
            value={form.matter_id}
            onChange={(e) => setField('matter_id', e.target.value)}
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink"
          >
            <option value="">No matter link</option>
            {matters.map((matter) => (
              <option key={matter.id} value={matter.id}>
                {matter.matter_name || matter.name || matter.slug || matter.id}
              </option>
            ))}
          </select>

          <input
            value={form.attendees}
            onChange={(e) => setField('attendees', e.target.value)}
            placeholder="Attendee emails, comma-separated"
            className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
          />
        </div>

        <div className="px-5 py-4 border-t border-brand-line flex justify-end gap-2 bg-brand-bg">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 border border-brand-line text-brand-ink text-xs font-medium rounded-lg hover:bg-brand-line/40"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving || !form.title}
            className="px-4 py-2 bg-brand-ink text-white text-xs font-medium rounded-lg hover:bg-brand-ink/90 disabled:opacity-50"
          >
            {saving ? 'Creating...' : 'Create event'}
          </button>
        </div>
      </form>
    </div>
  )
}
