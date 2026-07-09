import React, { useId } from 'react'

export default function FormField({ label, hint, error, required = false, children, className = '', labelClassName = '' }) {
  const generatedId = useId()
  const controlId = children?.props?.id || `field-${generatedId.replace(/:/g, '')}`
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined
  const describedBy = [children?.props?.['aria-describedby'], hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className={className}>
      <label htmlFor={controlId} className={labelClassName || 'block text-sm font-sans font-medium text-brand-ink mb-1'}>
        {label}{required && <span aria-hidden="true"> *</span>}
      </label>
      {React.cloneElement(children, {
        id: controlId,
        required: required || children.props.required,
        'aria-invalid': Boolean(error) || undefined,
        'aria-describedby': describedBy,
      })}
      {hint && <p id={hintId} className="mt-1 text-xs text-brand-muted">{hint}</p>}
      {error && <p id={errorId} role="alert" className="mt-1 text-sm text-brand-rose">{error}</p>}
    </div>
  )
}
