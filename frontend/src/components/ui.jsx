import { AlertTriangle, CheckCircle2, Info, Inbox, X } from 'lucide-react'

/** Shared UI primitives used across admin and other pages. */

const ALERT_VARIANTS = {
  error: {
    icon: AlertTriangle,
    wrapper: 'bg-red-50 border-red-200 text-red-800',
    iconClass: 'text-red-600',
    action: 'text-red-800 border-red-300 hover:bg-red-100',
  },
  warning: {
    icon: AlertTriangle,
    wrapper: 'bg-amber-50 border-amber-200 text-amber-800',
    iconClass: 'text-amber-600',
    action: 'text-amber-800 border-amber-300 hover:bg-amber-100',
  },
  success: {
    icon: CheckCircle2,
    wrapper: 'bg-green-50 border-green-200 text-green-800',
    iconClass: 'text-green-600',
    action: 'text-green-800 border-green-300 hover:bg-green-100',
  },
  info: {
    icon: Info,
    wrapper: 'bg-blue-50 border-blue-200 text-blue-800',
    iconClass: 'text-blue-600',
    action: 'text-blue-800 border-blue-300 hover:bg-blue-100',
  },
}

export function Spinner() {
  return (
    <div className="flex justify-center py-16">
      <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

export function AlertBanner({
  type = 'error',
  title,
  children,
  actionLabel,
  onAction,
  onDismiss,
  className = '',
}) {
  const variant = ALERT_VARIANTS[type] || ALERT_VARIANTS.error
  const Icon = variant.icon

  return (
    <div
      role={type === 'error' ? 'alert' : 'status'}
      className={`flex items-start gap-3 rounded-lg border px-4 py-3 text-sm font-sans shadow-sm ${variant.wrapper} ${className}`}
    >
      <Icon size={18} className={`mt-0.5 shrink-0 ${variant.iconClass}`} />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold leading-5">{title}</div>}
        {children && (
          <div className={`${title ? 'mt-0.5' : ''} leading-5 opacity-90`}>
            {children}
          </div>
        )}
      </div>
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className={`shrink-0 rounded-md border px-2.5 py-1 text-xs font-semibold transition-colors ${variant.action}`}
        >
          {actionLabel}
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 rounded-md p-1 opacity-70 transition-opacity hover:opacity-100"
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

export function EmptyState({
  icon: Icon = Inbox,
  visual,
  title,
  children,
  actionLabel,
  onAction,
  secondaryActionLabel,
  onSecondaryAction,
  className = '',
  compact = false,
}) {
  return (
    <div className={`bg-brand-surface border border-brand-line rounded-xl text-center shadow-sm ${compact ? 'p-6' : 'p-10'} ${className}`}>
      <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-lg bg-brand-bg-soft text-brand-muted">
        {visual || (Icon && <Icon size={22} />)}
      </div>
      {title && <h3 className="text-base font-serif font-bold text-brand-ink">{title}</h3>}
      {children && (
        <div className={`mx-auto max-w-md text-sm font-sans text-brand-muted ${title ? 'mt-1' : ''}`}>
          {children}
        </div>
      )}
      {(actionLabel || secondaryActionLabel) && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {actionLabel && onAction && (
            <button
              type="button"
              onClick={onAction}
              className="inline-flex items-center justify-center rounded-lg bg-brand-ink px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-ink-2"
            >
              {actionLabel}
            </button>
          )}
          {secondaryActionLabel && onSecondaryAction && (
            <button
              type="button"
              onClick={onSecondaryAction}
              className="inline-flex items-center justify-center rounded-lg border border-brand-line bg-brand-surface px-4 py-2 text-sm font-semibold text-brand-ink transition-colors hover:border-brand-line-2"
            >
              {secondaryActionLabel}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${
        checked ? 'bg-brand-green' : 'bg-brand-line-2'
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-[18px]' : 'translate-x-1'
        }`}
      />
    </button>
  )
}
