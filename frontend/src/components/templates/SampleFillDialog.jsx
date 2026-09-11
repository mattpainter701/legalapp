import { useMemo, useState } from 'react'
import { Download, Loader2, X } from 'lucide-react'
import { renderSampleTemplateFile } from '../../api'

// Fill a shared sample form with ad-hoc values and download the flattened PDF.
// The sample library is read-only shared content: filling never saves a copy to
// the tenant's own template library, it only produces a one-off PDF download.
const inputClass = 'w-full rounded-lg border border-brand-line bg-brand-bg px-3 py-2 text-sm text-brand-ink'

function FieldInput({ field, value, onChange }) {
  const label = field.label || String(field.name || '').replace(/_/g, ' ')
  const type = field.field_type || 'text'
  const inputId = `sample-field-${field.name}`

  if (type === 'checkbox' || type === 'radio') {
    return (
      <label htmlFor={inputId} className="flex items-center gap-2 text-sm text-brand-ink">
        <input
          id={inputId}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked ? 'Yes' : '')}
          className="h-4 w-4 rounded border-brand-line"
        />
        <span>{label}{field.required ? ' *' : ''}</span>
      </label>
    )
  }

  if (type === 'choice' && Array.isArray(field.options) && field.options.length) {
    return (
      <label htmlFor={inputId} className="block text-sm text-brand-ink">
        <span className="mb-1 block font-medium">{label}{field.required ? ' *' : ''}</span>
        <select
          id={inputId}
          value={value || ''}
          onChange={(event) => onChange(event.target.value)}
          className={inputClass}
        >
          <option value="">—</option>
          {field.options.map((option) => {
            const optionValue = typeof option === 'object' ? option.value : option
            const optionLabel = typeof option === 'object' ? (option.label || option.value) : option
            return <option key={optionValue} value={optionValue}>{optionLabel}</option>
          })}
        </select>
      </label>
    )
  }

  return (
    <label htmlFor={inputId} className="block text-sm text-brand-ink">
      <span className="mb-1 block font-medium">{label}{field.required ? ' *' : ''}</span>
      <input
        id={inputId}
        type="text"
        value={value || ''}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
    </label>
  )
}

export default function SampleFillDialog({ sample, onClose }) {
  const fields = useMemo(
    () => (sample.variable_schema?.fields || []).filter((field) => field?.name),
    [sample],
  )
  const [values, setValues] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const setValue = (name, value) => setValues((current) => ({ ...current, [name]: value }))

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { blob, filename } = await renderSampleTemplateFile(sample.id, { variables: values })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
      onClose()
    } catch (err) {
      setError(err?.message || 'The sample could not be filled. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sample-fill-title"
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-brand-line bg-brand-surface-2 p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="sample-fill-title" className="text-lg font-semibold text-brand-ink">Fill “{sample.title}”</h3>
            <p className="mt-1 text-sm text-brand-muted">
              Enter values and download a filled PDF. Only filled fields are written; leave the rest blank. Nothing is saved to your template library.
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close fill dialog" className="rounded-lg p-1 text-brand-muted hover:bg-brand-bg hover:text-brand-ink">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <form onSubmit={submit} className="mt-4 space-y-3">
          {fields.map((field) => (
            <FieldInput
              key={field.name}
              field={field}
              value={values[field.name]}
              onChange={(value) => setValue(field.name, value)}
            />
          ))}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-lg border border-brand-line px-4 py-2 text-sm font-semibold text-brand-ink hover:bg-brand-bg">
              Cancel
            </button>
            <button type="submit" disabled={busy || !fields.length} className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">
              {busy ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
              {busy ? 'Filling…' : 'Download filled PDF'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
