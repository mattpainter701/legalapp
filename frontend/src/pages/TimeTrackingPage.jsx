import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Clock, Pencil, Play, Plus, Square, Trash2, X } from 'lucide-react'
import { useAuth } from '../App'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'
import {
  AlertBanner,
  EmptyState,
  FilterToolbar,
  MetricStrip,
  SegmentedControl,
  Spinner,
  WorkspacePage,
  WorkspacePageHeader,
} from '../components/ui'
import {
  cancelTimer,
  createTimeEntry,
  deleteTimeEntry,
  getActiveTimer,
  getMattersV2,
  getTimeEntries,
  startTimer,
  stopTimer,
  updateTimeEntry,
} from '../api'

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'draft', label: 'Unbilled' },
  { value: 'invoiced', label: 'Invoiced' },
]

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

function formatElapsed(startedAt) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
}

function EntryStatus({ status, isBillable = true }) {
  if (!isBillable) {
    return <span className="inline-flex rounded-full border border-brand-line bg-brand-bg-soft px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-brand-muted">Non-billable</span>
  }
  const invoiced = status === 'invoiced'
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${
      invoiced
        ? 'border-brand-green/20 bg-brand-green/10 text-brand-green'
        : 'border-brand-amber/20 bg-brand-amber/10 text-brand-amber'
    }`}>
      {invoiced ? 'Invoiced' : 'Unbilled'}
    </span>
  )
}

export default function TimeTrackingPage() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [searchParams] = useSearchParams()
  const preselectedMatterId = searchParams.get('matter_id') || ''
  const { user } = useAuth()
  const [entries, setEntries] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [showForm, setShowForm] = useState(Boolean(preselectedMatterId))
  const [filter, setFilter] = useState('all')
  const [matterFilter, setMatterFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [activeTimer, setActiveTimer] = useState(null)
  const [timerBusy, setTimerBusy] = useState(false)
  const [, setTick] = useState(0)
  const [form, setForm] = useState({
    matter_id: preselectedMatterId,
    description: '',
    hours: '',
    hourly_rate: user?.default_billing_rate || '',
    date: new Date().toISOString().slice(0, 10),
    is_billable: true,
  })
  const [formError, setFormError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [editingEntry, setEditingEntry] = useState(null)
  const [editForm, setEditForm] = useState(null)
  const [editError, setEditError] = useState(null)
  const [editSaving, setEditSaving] = useState(false)

  const matterNames = useMemo(
    () => Object.fromEntries(matters.map((matter) => [matter.id, matter.matter_name])),
    [matters],
  )

  const loadData = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const params = {}
      if (filter !== 'all') params.status = filter
      if (matterFilter) params.matter_id = matterFilter
      if (dateFrom) params.date_from = dateFrom
      if (dateTo) params.date_to = dateTo
      const [entryData, matterData, timerData] = await Promise.all([
        getTimeEntries(params),
        getMattersV2({ page_size: 200, sort_by: 'updated_at', sort_dir: 'desc' }),
        getActiveTimer(),
      ])
      setEntries(entryData.items || entryData || [])
      setMatters(matterData.items || matterData || [])
      setActiveTimer(timerData || null)
    } catch (error) {
      setLoadError(error?.response?.data?.detail || 'Time entries could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [filter, matterFilter, dateFrom, dateTo])

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    if (!activeTimer) return undefined
    const id = setInterval(() => setTick((tick) => tick + 1), 1000)
    return () => clearInterval(id)
  }, [activeTimer])

  const handleStartTimer = async () => {
    setFormError(null)
    if (!form.matter_id) {
      setFormError('Select a matter to start the timer.')
      setShowForm(true)
      return
    }
    setTimerBusy(true)
    try {
      const timer = await startTimer({
        matter_id: form.matter_id,
        description: form.description.trim() || 'Timer session',
        is_billable: form.is_billable,
        ...(form.is_billable && form.hourly_rate ? { hourly_rate: Number.parseFloat(form.hourly_rate) } : {}),
      })
      setActiveTimer(timer)
      setForm((current) => ({ ...current, description: '' }))
    } catch (error) {
      const detail = error?.response?.data?.detail
      setFormError(typeof detail === 'string' ? detail : 'The timer could not be started.')
      setShowForm(true)
    } finally {
      setTimerBusy(false)
    }
  }

  const handleStopTimer = async () => {
    setTimerBusy(true)
    try {
      await stopTimer({})
      setActiveTimer(null)
      await loadData()
    } catch (error) {
      toast.error('Timer was not stopped', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    } finally {
      setTimerBusy(false)
    }
  }

  const handleDiscardTimer = async () => {
    const confirmed = await confirmAction({
      title: 'Discard running timer?',
      message: 'The elapsed time will not be logged.',
      confirmLabel: 'Discard timer',
      destructive: true,
    })
    if (!confirmed) return
    setTimerBusy(true)
    try {
      await cancelTimer()
      setActiveTimer(null)
      await loadData()
    } catch (error) {
      toast.error('Timer was not discarded', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    } finally {
      setTimerBusy(false)
    }
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setFormError(null)
    if (!form.matter_id) {
      setFormError('Select a matter.')
      return
    }
    const hours = Number.parseFloat(form.hours)
    if (!form.hours || Number.isNaN(hours) || hours < 0.25) {
      setFormError('Enter a valid number of hours (minimum 0.25).')
      return
    }
    if (!form.description.trim()) {
      setFormError('Enter a description.')
      return
    }

    setSaving(true)
    try {
      const payload = {
        matter_id: form.matter_id,
        description: form.description.trim(),
        hours,
        date: form.date,
        is_billable: form.is_billable,
      }
      if (form.is_billable && form.hourly_rate) {
        const hourlyRate = Number.parseFloat(form.hourly_rate)
        if (!Number.isNaN(hourlyRate) && hourlyRate > 0) payload.hourly_rate = hourlyRate
      }
      await createTimeEntry(payload)
      setShowForm(false)
      setForm({
        matter_id: preselectedMatterId,
        description: '',
        hours: '',
        hourly_rate: user?.default_billing_rate || '',
        date: new Date().toISOString().slice(0, 10),
        is_billable: true,
      })
      await loadData()
    } catch (error) {
      const detail = error?.response?.data?.detail
      setFormError(
        typeof detail === 'string'
          ? detail
          : 'The time entry could not be saved. Check the fields and try again.',
      )
    } finally {
      setSaving(false)
    }
  }

  const handleEditSubmit = async (event) => {
    event.preventDefault()
    setEditError(null)
    const hours = Number.parseFloat(editForm.hours)
    if (!editForm.description.trim()) return setEditError('Enter a description.')
    if (Number.isNaN(hours) || hours < 0.25) return setEditError('Enter a valid number of hours (minimum 0.25).')
    const hourlyRate = Number.parseFloat(editForm.hourly_rate)
    if (editForm.is_billable && (!Number.isFinite(hourlyRate) || hourlyRate <= 0)) {
      return setEditError('Enter a billing rate greater than zero.')
    }
    setEditSaving(true)
    try {
      await updateTimeEntry(editingEntry.id, {
        description: editForm.description.trim(),
        hours,
        date: editForm.date,
        is_billable: editForm.is_billable,
        ...(editForm.is_billable ? { hourly_rate: hourlyRate } : {}),
      })
      setEditingEntry(null)
      setEditForm(null)
      await loadData()
    } catch (error) {
      setEditError(error?.response?.data?.detail || 'The time entry could not be updated.')
    } finally {
      setEditSaving(false)
    }
  }

  const handleDelete = async (id) => {
    const confirmed = await confirmAction({
      title: 'Delete time entry?',
      message: 'This time entry will be permanently removed.',
      confirmLabel: 'Delete entry',
      destructive: true,
    })
    if (!confirmed) return
    try {
      await deleteTimeEntry(id)
      await loadData()
    } catch (error) {
      toast.error('Time entry was not deleted', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    }
  }

  const visibleEntries = entries.filter((entry) => entry.status !== 'running')
  const totalHours = visibleEntries.reduce((sum, entry) => sum + Number(entry.hours || 0), 0)
  const totalAmount = visibleEntries.reduce((sum, entry) => sum + Number(entry.amount || 0), 0)
  const unbilledAmount = visibleEntries
    .filter((entry) => entry.is_billable && (entry.status === 'draft' || !entry.invoice_id))
    .reduce((sum, entry) => sum + Number(entry.amount || 0), 0)
  const fieldClass = 'min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent'

  return (
    <WorkspacePage width="wide">
      <WorkspacePageHeader
        eyebrow="Billing activity"
        icon={Clock}
        title="Time tracking"
        description="Capture work as it happens, then keep unbilled and invoiced activity easy to distinguish."
        meta={<span>{visibleEntries.length} entr{visibleEntries.length === 1 ? 'y' : 'ies'} in this view</span>}
        actions={
          <>
            {!activeTimer && (
              <button
                type="button"
                onClick={handleStartTimer}
                disabled={timerBusy}
                title={form.matter_id ? 'Start a live timer for the selected matter' : 'Select a matter in the entry panel first'}
                className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Play size={16} /> Start timer
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setShowForm((open) => !open)
                setFormError(null)
              }}
              aria-expanded={showForm}
              className="btn-primary inline-flex items-center gap-2"
            >
              <Plus size={16} /> Add entry
            </button>
          </>
        }
      />

      <MetricStrip
        className="mb-6"
        items={[
          { label: 'Hours logged', value: `${totalHours.toFixed(1)}h` },
          { label: 'Recorded value', value: money.format(totalAmount) },
          {
            label: 'Unbilled value',
            value: money.format(unbilledAmount),
            className: unbilledAmount > 0 ? 'text-brand-amber' : 'text-brand-ink',
          },
        ]}
      />

      {activeTimer && (
        <section
          aria-label="Running timer"
          className="mb-6 flex flex-col gap-4 rounded-2xl border border-brand-green/30 bg-brand-green/10 p-4 shadow-sm sm:flex-row sm:items-center"
        >
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-surface text-brand-green shadow-sm">
            <Clock size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-brand-green">Timer running</p>
            <p className="mt-1 font-mono text-2xl font-bold text-brand-ink">{formatElapsed(activeTimer.timer_started_at)}</p>
            <p className="mt-1 truncate text-sm text-brand-ink-2">
              {matterNames[activeTimer.matter_id] || 'Matter'} · {activeTimer.description}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleStopTimer}
              disabled={timerBusy}
              className="btn-primary inline-flex items-center gap-2 disabled:opacity-60"
            >
              <Square size={14} /> Stop & log
            </button>
            <button
              type="button"
              onClick={handleDiscardTimer}
              disabled={timerBusy}
              className="btn-secondary inline-flex items-center gap-2 text-brand-muted disabled:opacity-60"
            >
              <X size={14} /> Discard
            </button>
          </div>
        </section>
      )}

      {(showForm || (!activeTimer && formError)) && (
        <form
          onSubmit={handleSubmit}
          className="mb-6 rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5"
        >
          <div>
            <h2 className="text-lg font-semibold text-brand-ink">Add billable time</h2>
            <p className="mt-1 text-sm text-brand-muted">Record a completed entry or select a matter before starting a live timer.</p>
          </div>
          {formError && (
            <AlertBanner type="error" title="Time was not recorded" className="mt-4">
              {formError}
            </AlertBanner>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <div className="sm:col-span-2 lg:col-span-1">
              <label htmlFor="timetrackingpage-matter" className="mb-1.5 block text-xs font-semibold text-brand-ink">Matter</label>
              <select
                id="timetrackingpage-matter"
                value={form.matter_id}
                onChange={(event) => setForm({ ...form, matter_id: event.target.value })}
                required
                className={fieldClass}
              >
                <option value="">Select a matter</option>
                {matters.map((matter) => (
                  <option key={matter.id} value={matter.id}>{matter.matter_name}</option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="timetrackingpage-description" className="mb-1.5 block text-xs font-semibold text-brand-ink">Description</label>
              <input
                id="timetrackingpage-description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                required
                placeholder="Work performed"
                className={fieldClass}
              />
            </div>
            <div>
              <label htmlFor="timetrackingpage-hours" className="mb-1.5 block text-xs font-semibold text-brand-ink">Hours</label>
              <input
                id="timetrackingpage-hours"
                type="number"
                step="0.25"
                min="0.25"
                value={form.hours}
                onChange={(event) => setForm({ ...form, hours: event.target.value })}
                required
                className={fieldClass}
              />
            </div>
            <div>
              <label htmlFor="timetrackingpage-date" className="mb-1.5 block text-xs font-semibold text-brand-ink">Date</label>
              <input
                id="timetrackingpage-date"
                type="date"
                value={form.date}
                onChange={(event) => setForm({ ...form, date: event.target.value })}
                required
                className={fieldClass}
              />
            </div>
            <div>
              <label htmlFor="timetrackingpage-rate" className="mb-1.5 block text-xs font-semibold text-brand-ink">Rate</label>
              <input
                id="timetrackingpage-rate"
                type="number"
                step="1"
                min="0"
                value={form.hourly_rate}
                onChange={(event) => setForm({ ...form, hourly_rate: event.target.value })}
                placeholder={user?.default_billing_rate ? String(user.default_billing_rate) : '0'}
                className={fieldClass}
              />
            </div>
            <label className="flex min-h-11 items-center gap-2 text-sm text-brand-ink">
              <input type="checkbox" checked={form.is_billable} onChange={(event) => setForm({ ...form, is_billable: event.target.checked })} />
              Billable time
            </label>
            <div className="flex items-end sm:col-span-2 lg:col-span-4">
              <button
                type="submit"
                disabled={saving}
                className="btn-primary inline-flex min-h-11 items-center justify-center disabled:cursor-not-allowed disabled:opacity-60"
              >
                {saving ? 'Saving entry' : 'Save entry'}
              </button>
            </div>
          </div>
        </form>
      )}

      <FilterToolbar ariaLabel="Time entry status filters">
        <SegmentedControl
          items={FILTERS}
          value={filter}
          onChange={setFilter}
          label="Filter time entries by billing status"
        />
        <select aria-label="Filter by matter" value={matterFilter} onChange={(event) => setMatterFilter(event.target.value)} className="min-h-9 rounded-xl border border-brand-line bg-brand-surface px-3 text-xs text-brand-ink">
          <option value="">All matters</option>
          {matters.map((matter) => <option key={matter.id} value={matter.id}>{matter.matter_name}</option>)}
        </select>
        <input aria-label="Entries from" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="min-h-9 rounded-xl border border-brand-line bg-brand-surface px-3 text-xs text-brand-ink" />
        <input aria-label="Entries through" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="min-h-9 rounded-xl border border-brand-line bg-brand-surface px-3 text-xs text-brand-ink" />
      </FilterToolbar>

      {editingEntry && editForm && (
        <form onSubmit={handleEditSubmit} className="mb-6 rounded-2xl border border-brand-accent/30 bg-brand-surface p-4 shadow-sm sm:p-5">
          <div className="flex items-start justify-between gap-3">
            <div><h2 className="text-lg font-semibold text-brand-ink">Edit time entry</h2><p className="mt-1 text-sm text-brand-muted">Only unbilled entries can be changed.</p></div>
            <button type="button" aria-label="Close edit form" onClick={() => setEditingEntry(null)} className="tap-target rounded-xl text-brand-muted"><X size={16} /></button>
          </div>
          {editError && <AlertBanner type="error" title="Time was not updated" className="mt-4">{editError}</AlertBanner>}
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <div className="sm:col-span-2 lg:col-span-2"><label htmlFor="edit-time-description" className="mb-1.5 block text-xs font-semibold text-brand-ink">Description</label><input id="edit-time-description" className={fieldClass} value={editForm.description} onChange={(event) => setEditForm({ ...editForm, description: event.target.value })} /></div>
            <div><label htmlFor="edit-time-hours" className="mb-1.5 block text-xs font-semibold text-brand-ink">Hours</label><input id="edit-time-hours" className={fieldClass} type="number" min="0.25" step="0.25" value={editForm.hours} onChange={(event) => setEditForm({ ...editForm, hours: event.target.value })} /></div>
            <div><label htmlFor="edit-time-date" className="mb-1.5 block text-xs font-semibold text-brand-ink">Date</label><input id="edit-time-date" className={fieldClass} type="date" value={editForm.date} onChange={(event) => setEditForm({ ...editForm, date: event.target.value })} /></div>
            <div><label htmlFor="edit-time-rate" className="mb-1.5 block text-xs font-semibold text-brand-ink">Rate</label><input id="edit-time-rate" className={fieldClass} type="number" min="0.01" step="0.01" value={editForm.hourly_rate} onChange={(event) => setEditForm({ ...editForm, hourly_rate: event.target.value })} disabled={!editForm.is_billable} /></div>
            <label className="flex min-h-11 items-center gap-2 text-sm text-brand-ink"><input type="checkbox" checked={editForm.is_billable} onChange={(event) => setEditForm({ ...editForm, is_billable: event.target.checked })} /> Billable time</label>
            <button type="submit" disabled={editSaving} className="btn-primary min-h-11">{editSaving ? 'Saving changes' : 'Save changes'}</button>
          </div>
        </form>
      )}

      {loadError ? (
        <AlertBanner
          type="error"
          title="Time entries could not be loaded"
          actionLabel="Retry"
          onAction={loadData}
        >
          {loadError}
        </AlertBanner>
      ) : loading ? (
        <Spinner />
      ) : visibleEntries.length === 0 ? (
        <EmptyState
          icon={Clock}
          title={filter === 'all' ? 'No time entries yet' : `No ${FILTERS.find((item) => item.value === filter)?.label.toLowerCase()} entries`}
          actionLabel="Add time entry"
          onAction={() => setShowForm(true)}
          secondaryActionLabel={filter !== 'all' ? 'Show all entries' : undefined}
          onSecondaryAction={() => setFilter('all')}
        >
          {filter === 'all'
            ? 'Record completed work or start a timer after selecting a matter.'
            : 'Choose another billing status or return to all time entries.'}
        </EmptyState>
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {visibleEntries.map((entry) => (
              <article key={entry.id} className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-brand-ink">{entry.description}</p>
                    <p className="mt-1 truncate text-xs text-brand-muted">{matterNames[entry.matter_id] || 'Matter unavailable'}</p>
                  </div>
                  <EntryStatus status={entry.status} isBillable={entry.is_billable} />
                </div>
                <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-brand-line pt-3">
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Date</dt>
                    <dd className="mt-1 text-xs text-brand-ink">{entry.date}</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Hours</dt>
                    <dd className="mt-1 text-xs font-semibold text-brand-ink">{entry.hours}h</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Value</dt>
                    <dd className="mt-1 text-xs font-semibold text-brand-ink">{money.format(Number(entry.amount || 0))}</dd>
                  </div>
                </dl>
                {entry.status !== 'invoiced' && (
                  <div className="mt-3 flex gap-2"><button
                    type="button"
                    onClick={() => { setEditingEntry(entry); setEditForm({ matter_id: entry.matter_id, description: entry.description || '', hours: String(entry.hours || ''), hourly_rate: String(entry.hourly_rate || ''), date: entry.date, is_billable: entry.is_billable !== false }); setEditError(null) }}
                    className="inline-flex min-h-10 items-center gap-2 rounded-xl px-2 text-xs font-semibold text-brand-accent-2 hover:bg-brand-bg-soft"
                  ><Pencil size={14} /> Edit entry</button><button
                    type="button"
                    onClick={() => handleDelete(entry.id)}
                    className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl px-2 text-xs font-semibold text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose"
                  >
                    <Trash2 size={14} /> Delete entry
                  </button></div>
                )}
              </article>
            ))}
          </div>

          <div className="hidden overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm md:block">
            <div className="overflow-x-auto">
              <table className="min-w-[760px] w-full border-collapse text-left text-sm">
                <thead className="border-b border-brand-line bg-brand-bg-soft/60">
                  <tr>
                    {['Date', 'Matter', 'Description', 'Hours', 'Value', 'Status', ''].map((heading) => (
                      <th key={heading} scope="col" className="px-4 py-3 text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {visibleEntries.map((entry) => (
                    <tr key={entry.id} className="hover:bg-brand-bg-soft/50">
                      <td className="whitespace-nowrap px-4 py-3 text-brand-muted">{entry.date}</td>
                      <td className="max-w-56 truncate px-4 py-3 text-brand-muted" title={matterNames[entry.matter_id] || ''}>
                        {matterNames[entry.matter_id] || '—'}
                      </td>
                      <td className="max-w-80 truncate px-4 py-3 font-medium text-brand-ink" title={entry.description}>
                        {entry.description}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-brand-ink">{entry.hours}h</td>
                      <td className="whitespace-nowrap px-4 py-3 font-semibold text-brand-ink">
                        {money.format(Number(entry.amount || 0))}
                      </td>
                       <td className="px-4 py-3"><EntryStatus status={entry.status} isBillable={entry.is_billable} /></td>
                      <td className="px-4 py-3 text-right">
                        {entry.status !== 'invoiced' && (
                          <div className="flex justify-end gap-1"><button
                            type="button"
                            onClick={() => { setEditingEntry(entry); setEditForm({ matter_id: entry.matter_id, description: entry.description || '', hours: String(entry.hours || ''), hourly_rate: String(entry.hourly_rate || ''), date: entry.date, is_billable: entry.is_billable !== false }); setEditError(null) }}
                            className="tap-target rounded-xl text-brand-accent-2 hover:bg-brand-bg-soft"
                            aria-label={`Edit ${entry.description}`}
                          ><Pencil size={15} /></button><button
                            type="button"
                            onClick={() => handleDelete(entry.id)}
                            className="tap-target rounded-xl text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose"
                            aria-label={`Delete ${entry.description}`}
                          >
                            <Trash2 size={15} />
                          </button></div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </WorkspacePage>
  )
}
