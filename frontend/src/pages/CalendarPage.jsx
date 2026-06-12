import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCalendarEvents, syncCalendarDeadlines, getCalendarProviders, connectCalendarIntegration } from '../api'
import { ChevronLeft, ChevronRight, CalendarDays, ClipboardList, Building2, RefreshCw } from 'lucide-react'

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
}

const TYPE_LABELS = {
  task_due: 'Task',
  matter_key_date: 'Key Date',
  renewal: 'Renewal',
  estate_deadline: 'Estate',
  external_calendar: 'Synced',
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

function EventChip({ event, onClick }) {
  const styles = TYPE_STYLES[event.event_type] || TYPE_STYLES.task_due
  const Icon = styles.icon

  return (
    <button
      onClick={onClick}
      className={`w-full text-left flex items-start gap-3 px-3 py-2.5 rounded border ${styles.bg} hover:brightness-95 transition-all group`}
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
      </div>
      <Icon className="w-3.5 h-3.5 text-brand-muted shrink-0 mt-0.5 opacity-60 group-hover:opacity-100 transition-opacity" />
    </button>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function CalendarPage() {
  const navigate = useNavigate()
  const [pivotDate, setPivotDate] = useState(new Date())
  const [events, setEvents] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState(null) // { type: 'success'|'error', text: string }
  const [calendarProvider, setCalendarProvider] = useState(null)
  const [connectProvider, setConnectProvider] = useState(null)
  const [providerEvents, setProviderEvents] = useState([])

  useEffect(() => {
    getCalendarProviders()
      .then((data) => {
        if (data.providers && data.providers.length > 0) {
          setCalendarProvider(data.providers[0])
        }
        setConnectProvider(data.connect_provider || data.login_provider || data.tenant_providers?.[0] || null)
      })
      .catch(() => {})
  }, [])

  const fetchEvents = useCallback(async (pivot) => {
    setLoading(true)
    setError(null)
    try {
      const som = startOfMonth(pivot)
      // Show current month + next month for context
      const eom = endOfMonth(new Date(pivot.getFullYear(), pivot.getMonth() + 1, 1))
      const data = await getCalendarEvents(toIso(som), toIso(eom))
      const visibleProviderEvents = providerEvents.filter((event) => event.date >= toIso(som) && event.date <= toIso(eom))
      const mergedEvents = mergeCalendarEvents(data.events || [], visibleProviderEvents)
      setEvents(mergedEvents)
      setTotal(mergedEvents.length)
    } catch (err) {
      setError('Failed to load calendar events.')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [providerEvents])

  useEffect(() => {
    fetchEvents(pivotDate)
  }, [pivotDate, fetchEvents])

  const prevMonth = () => setPivotDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1))
  const nextMonth = () => setPivotDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1))

  // Group events by ISO date string
  const grouped = events.reduce((acc, ev) => {
    const key = ev.date
    if (!acc[key]) acc[key] = []
    acc[key].push(ev)
    return acc
  }, {})

  const sortedDates = Object.keys(grouped).sort()

  const handleEventClick = (event) => {
    if (event.url) navigate(event.url)
  }

  const handleSync = async (provider) => {
    setSyncing(true)
    setSyncMessage(null)
    try {
      const result = await syncCalendarDeadlines(provider)
      const mappedProviderEvents = (result.events || [])
        .map((event) => {
          const date = providerEventDate(event)
          if (!date) return null
          return {
            id: `${provider}-${event.id}`,
            title: event.subject || '(No title)',
            date,
            event_type: 'external_calendar',
            url: null,
            provider,
            location: event.location || '',
          }
        })
        .filter(Boolean)
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
      setSyncMessage({ type: 'error', text: `Calendar sync failed: ${detail}` })
    } finally {
      setSyncing(false)
    }
  }

  const handleConnectCalendar = () => {
    const provider = connectProvider || 'microsoft'
    connectCalendarIntegration(provider)
  }

  return (
    <div className="flex flex-col h-screen bg-brand-bg">
      {/* Header */}
      <div className="h-16 flex items-center px-6 border-b border-brand-line bg-brand-surface-2 shrink-0">
        <CalendarDays className="w-5 h-5 mr-2 text-brand-accent" strokeWidth={1.5} />
        <h1 className="font-serif font-semibold text-lg text-brand-ink">Deadline Calendar</h1>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-brand-muted font-mono">
            {loading ? 'Loading…' : `${total} event${total !== 1 ? 's' : ''}`}
          </span>
          <div className="flex items-center gap-2">
            {calendarProvider ? (
              <button
                onClick={() => handleSync(calendarProvider)}
                disabled={syncing}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-brand-line bg-brand-surface hover:bg-brand-line/40 text-brand-ink disabled:opacity-50 transition-colors"
                title={`Sync matter deadlines to ${calendarProvider === 'google' ? 'Google' : 'Microsoft'} Calendar`}
              >
                <RefreshCw className={`w-3 h-3 ${syncing ? 'animate-spin' : ''}`} />
                {syncing ? 'Syncing…' : 'Sync Calendar'}
              </button>
            ) : (
              <button
                onClick={handleConnectCalendar}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-brand-line bg-brand-surface hover:bg-brand-line/40 text-brand-ink transition-colors"
                title="Connect your calendar to sync events"
              >
                <RefreshCw className="w-3 h-3" />
                Connect Calendar
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

      {/* Month navigation */}
      <div className="flex items-center gap-4 px-6 py-4 border-b border-brand-line bg-brand-surface-2 shrink-0">
        <button
          onClick={prevMonth}
          className="p-1.5 rounded hover:bg-brand-line transition-colors text-brand-muted hover:text-brand-ink"
          aria-label="Previous month"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="font-serif font-semibold text-base text-brand-ink w-48 text-center">
          {monthLabel(pivotDate)}
        </span>
        <button
          onClick={nextMonth}
          className="p-1.5 rounded hover:bg-brand-line transition-colors text-brand-muted hover:text-brand-ink"
          aria-label="Next month"
        >
          <ChevronRight className="w-4 h-4" />
        </button>

        {/* Legend */}
        <div className="ml-auto flex items-center gap-4">
          {Object.entries(TYPE_STYLES).map(([type, s]) => (
            <span key={type} className="flex items-center gap-1.5 text-xs text-brand-muted">
              <span className={`w-2 h-2 rounded-full ${s.dot}`} />
              {TYPE_LABELS[type]}
            </span>
          ))}
        </div>
      </div>

      {/* Agenda body */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {error && (
          <div className="text-center py-16 text-brand-rose text-sm">{error}</div>
        )}

        {!error && !loading && sortedDates.length === 0 && (
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

        {!error && !loading && sortedDates.length > 0 && (
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
    </div>
  )
}
