import React, { createContext, useCallback, useContext, useRef, useState } from 'react'

const ConfirmContext = createContext(null)

export function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null)
  const cancelRef = useRef(null)

  const confirm = useCallback((options) => new Promise((resolve) => {
    setRequest({
      title: options?.title || 'Confirm action',
      message: typeof options === 'string' ? options : options?.message,
      confirmLabel: options?.confirmLabel || 'Confirm',
      destructive: Boolean(options?.destructive),
      resolve,
    })
  }), [])

  const finish = (value) => {
    request?.resolve(value)
    setRequest(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-brand-ink/50 p-4" onMouseDown={(event) => event.target === event.currentTarget && finish(false)}>
          <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message" className="w-full max-w-md rounded-2xl border border-brand-line bg-white p-6 shadow-xl" ref={(node) => node && queueMicrotask(() => cancelRef.current?.focus())}>
            <h2 id="confirm-title" className="font-serif text-xl text-brand-ink">{request.title}</h2>
            <p id="confirm-message" className="mt-3 text-sm leading-6 text-brand-ink-2">{request.message}</p>
            <div className="mt-6 flex justify-end gap-2">
              <button ref={cancelRef} type="button" onClick={() => finish(false)} className="rounded-lg border border-brand-line px-4 py-2 text-sm font-semibold text-brand-ink">Cancel</button>
              <button type="button" onClick={() => finish(true)} className={`rounded-lg px-4 py-2 text-sm font-semibold text-white ${request.destructive ? 'bg-brand-rose' : 'bg-brand-ink'}`}>{request.confirmLabel}</button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const value = useContext(ConfirmContext)
  if (!value) throw new Error('useConfirm must be used within ConfirmProvider')
  return value
}
