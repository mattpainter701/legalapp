import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'

const ToastContext = createContext(null)

const DEFAULT_TIMEOUT_MS = 5000
const DEFAULT_TIMEOUT_WITH_ACTION_MS = 9000

function normalizeType(type) {
  if (type === 'error') return 'error'
  if (type === 'success') return 'success'
  return 'info'
}

function isValidDuration(value) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function typeClasses(type) {
  if (type === 'success') {
    return {
      text: 'text-brand-green',
      background: 'border-brand-green/30 bg-white',
      iconClass: 'text-brand-green',
      Icon: CheckCircle2,
    }
  }
  if (type === 'error') {
    return {
      text: 'text-brand-rose',
      background: 'border-brand-rose/30 bg-white',
      iconClass: 'text-brand-rose',
      Icon: AlertCircle,
    }
  }
  return {
    text: 'text-brand-ink',
    background: 'border-brand-line bg-white',
    iconClass: 'text-brand-muted',
    Icon: Info,
  }
}

function makeToastId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function ToastItem({ toast, onDismiss }) {
  const { Icon, text, background, iconClass } = typeClasses(toast.type)
  return (
    <div className={`min-w-[260px] max-w-xs rounded-xl border px-3 py-2 shadow-sm ${background}`}>
      <div className="flex items-start gap-2">
        <Icon size={15} className={iconClass} />
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-bold ${text}`}>{toast.title}</p>
          {toast.message && <p className={`mt-1 text-[12px] leading-5 ${text === 'text-brand-ink' ? 'text-brand-muted' : text}`}>{toast.message}</p>}
          <div className="mt-2 flex items-center justify-end gap-2">
            {toast.actionLabel && toast.onAction && (
              <button
                type="button"
                onClick={() => toast.onAction(toast.id)}
                className="rounded-md border border-brand-line bg-white px-2 py-1 text-[11px] font-bold"
              >
                {toast.actionLabel}
              </button>
            )}
            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              className="rounded-md border border-brand-line px-2 py-1 text-[11px] font-bold"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const timeoutsRef = useRef(new Map())

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    const timeout = timeoutsRef.current.get(id)
    if (timeout) {
      clearTimeout(timeout)
      timeoutsRef.current.delete(id)
    }
  }, [])

  const show = useCallback((raw) => {
    const type = normalizeType(raw?.type)
    const id = raw?.id || makeToastId()
    const toast = {
      id,
      type,
      title: raw?.title || 'Notice',
      message: raw?.message,
      actionLabel: raw?.actionLabel,
      onAction: raw?.onAction,
    }
    const hasAction = Boolean(toast.actionLabel && toast.onAction)
    const duration = isValidDuration(raw?.durationMs)
      ? raw.durationMs
      : hasAction ? DEFAULT_TIMEOUT_WITH_ACTION_MS : DEFAULT_TIMEOUT_MS

    setToasts((current) => [toast, ...current].slice(0, 12))

    if (!raw?.persistent && duration > 0) {
      const timeout = setTimeout(() => dismiss(id), duration)
      timeoutsRef.current.set(id, timeout)
    }
    return id
  }, [dismiss])

  const api = useMemo(() => ({
    show,
    dismiss,
    success: (message, messageOrOptions = {}) => {
      if (typeof messageOrOptions === 'string') {
        return show({ type: 'success', title: 'Success', message: messageOrOptions || message })
      }
      return show({ type: 'success', title: message, ...messageOrOptions })
    },
    info: (message, messageOrOptions = {}) => {
      if (typeof messageOrOptions === 'string') {
        return show({ type: 'info', title: 'Notice', message: messageOrOptions || message })
      }
      return show({ type: 'info', title: message, ...messageOrOptions })
    },
    error: (message, messageOrOptions = {}) => {
      if (typeof messageOrOptions === 'string') {
        return show({ type: 'error', title: 'Error', message: messageOrOptions || message })
      }
      return show({ type: 'error', title: message, ...messageOrOptions })
    },
    clear: () => {
      setToasts([])
      timeoutsRef.current.forEach((timeout) => clearTimeout(timeout))
      timeoutsRef.current.clear()
    },
    withAction: (options) => show({ ...options, actionLabel: options?.actionLabel, onAction: options?.onAction }),
  }), [dismiss, show])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="fixed right-3 top-3 z-[100] flex w-[min(95vw,380px)] flex-col gap-2 sm:right-5">
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
