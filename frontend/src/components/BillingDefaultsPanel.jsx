import { useEffect, useState } from 'react'
import { getBillingSettings, updateBillingSettings } from '../api'
import { Spinner } from './ui'

// Firms bill in tenths (6 minutes) or quarters (15). Both are offered outright
// so nobody has to know the increment is stored in minutes.
const INCREMENT_OPTIONS = [
  { value: 6, label: '6 minutes (0.1 hour)' },
  { value: 10, label: '10 minutes' },
  { value: 15, label: '15 minutes (0.25 hour)' },
  { value: 30, label: '30 minutes' },
  { value: 60, label: '60 minutes' },
]

export default function BillingDefaultsPanel() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [rate, setRate] = useState('')
  const [increment, setIncrement] = useState(6)

  useEffect(() => {
    let cancelled = false
    getBillingSettings()
      .then((data) => {
        if (cancelled) return
        setSettings(data)
        setRate(data?.default_hourly_rate != null ? String(data.default_hourly_rate) : '')
        setIncrement(Number(data?.time_rounding_minutes) || 6)
      })
      .catch((e) => {
        if (!cancelled) setError(e?.response?.data?.detail || 'Billing defaults could not be loaded.')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError(null)
    setSaved(false)

    const payload = { time_rounding_minutes: Number(increment) }
    if (rate.trim()) {
      const parsed = Number.parseFloat(rate)
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setError('Enter a firm rate greater than zero, or leave it blank.')
        return
      }
      payload.default_hourly_rate = parsed
    }

    setSaving(true)
    try {
      const updated = await updateBillingSettings(payload)
      setSettings(updated)
      setSaved(true)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Billing defaults could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spinner />

  return (
    <form onSubmit={handleSubmit} className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
      <div className="px-6 py-5 border-b border-brand-line">
        <h2 className="font-serif font-bold text-xl text-brand-ink">Time and billing defaults</h2>
        <p className="text-[13px] text-brand-muted font-sans mt-0.5">
          Applied when a matter or timekeeper has no rate of its own.
        </p>
      </div>

      <div className="p-6 grid gap-5 sm:grid-cols-2">
        <div>
          <label htmlFor="billing-default-rate" className="mb-1.5 block text-xs font-semibold text-brand-ink">
            Firm default hourly rate
          </label>
          <input
            id="billing-default-rate"
            type="number"
            min="0"
            step="0.01"
            value={rate}
            onChange={(event) => setRate(event.target.value)}
            placeholder="No firm default"
            aria-describedby="billing-default-rate-hint"
            className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
          />
          <p id="billing-default-rate-hint" className="mt-1 text-[11px] text-brand-muted">
            A matter rate, then the timekeeper&apos;s own rate, both take priority over this.
          </p>
        </div>

        <div>
          <label htmlFor="billing-increment" className="mb-1.5 block text-xs font-semibold text-brand-ink">
            Billing increment
          </label>
          <select
            id="billing-increment"
            value={increment}
            onChange={(event) => setIncrement(Number(event.target.value))}
            aria-describedby="billing-increment-hint"
            className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
          >
            {INCREMENT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <p id="billing-increment-hint" className="mt-1 text-[11px] text-brand-muted">
            Stopped timers round up to this, with one increment as the minimum.
          </p>
        </div>

        <div className="sm:col-span-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-60"
          >
            {saving ? 'Saving' : 'Save defaults'}
          </button>
          {saved && <span role="status" className="text-sm text-brand-green">Saved</span>}
          {error && <span role="alert" className="text-sm text-brand-rose">{error}</span>}
          {settings?.time_rounding_minutes && !saved && !error && (
            <span className="text-xs text-brand-muted">
              Currently billing in {settings.time_rounding_minutes}-minute units
            </span>
          )}
        </div>
      </div>
    </form>
  )
}
