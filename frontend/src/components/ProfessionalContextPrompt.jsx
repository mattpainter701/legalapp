import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { updateMe } from '../api'
import { useAuth } from '../App'

const dismissalKey = (userId) => `lawhand.professional-context.dismissed.${userId}`

function readDismissed(userId) {
  if (!userId || typeof window === 'undefined') return false
  try {
    return window.sessionStorage.getItem(dismissalKey(userId)) === 'true'
  } catch {
    return false
  }
}

export default function ProfessionalContextPrompt() {
  const { user, refreshUser } = useAuth()
  const { pathname } = useLocation()
  const [dismissed, setDismissed] = useState(() => readDismissed(user?.id))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({
    professional_role: '',
    office_location: '',
    primary_jurisdictions: '',
  })
  const jurisdictionText = Array.isArray(user?.primary_jurisdictions)
    ? user.primary_jurisdictions.join(', ')
    : ''

  useEffect(() => {
    setDismissed(readDismissed(user?.id))
    setForm({
      professional_role: user?.professional_role || '',
      office_location: user?.office_location || '',
      primary_jurisdictions: jurisdictionText,
    })
  }, [
    user?.id,
    user?.professional_role,
    user?.office_location,
    jurisdictionText,
  ])

  const complete = Boolean(
    user?.professional_role
      && user?.office_location
      && user?.primary_jurisdictions?.length,
  )

  if (!user || complete || dismissed || pathname.startsWith('/profile')) return null

  const dismiss = () => {
    try {
      window.sessionStorage.setItem(dismissalKey(user.id), 'true')
    } catch {
      // Session-only dismissal is optional.
    }
    setDismissed(true)
  }

  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      await updateMe({
        professional_role: form.professional_role.trim(),
        office_location: form.office_location.trim(),
        primary_jurisdictions: form.primary_jurisdictions
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      })
      await refreshUser?.()
      dismiss()
    } catch (err) {
      setError(
        err?.response?.data?.detail
          || 'We could not save this context. Please try again.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <aside
      role="dialog"
      aria-labelledby="professional-context-title"
      className="fixed bottom-[5.25rem] right-3 z-40 w-[calc(100%-1.5rem)] max-w-md rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-xl lg:bottom-5 lg:right-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="professional-context-title" className="font-serif text-lg font-bold text-brand-ink">
            Help AI understand your work
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-brand-muted">
            These details automatically improve general chats. Matter chats add the selected matter's context.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss professional context setup"
          className="rounded-lg px-2 py-1 text-lg text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink"
        >
          ×
        </button>
      </div>

      <form onSubmit={save} className="mt-4 grid gap-3">
        {[
          ['professional_role', 'Professional role', 'Attorney, paralegal, secretary…'],
          ['office_location', 'Office location', 'Chicago, IL'],
          ['primary_jurisdictions', 'Primary jurisdictions', 'Illinois, Wisconsin'],
        ].map(([field, label, placeholder]) => (
          <label key={field} className="grid gap-1 text-xs font-semibold text-brand-ink">
            {label}
            <input
              required
              value={form[field]}
              onChange={(event) => setForm((current) => ({
                ...current,
                [field]: event.target.value,
              }))}
              placeholder={placeholder}
              className="rounded-lg border border-brand-line bg-white px-3 py-2 text-sm font-normal text-brand-ink outline-none focus:border-brand-accent focus:ring-2 focus:ring-brand-accent/20"
            />
          </label>
        ))}
        {error && <p role="alert" className="text-xs text-brand-rose">{error}</p>}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-brand-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save context'}
          </button>
        </div>
      </form>
    </aside>
  )
}
