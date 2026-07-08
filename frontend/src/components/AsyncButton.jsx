import React, { useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'

export default function AsyncButton({
  children,
  onClick,
  disabled = false,
  type = 'button',
  className = '',
  loadingLabel = 'Working...',
  successLabel = 'Done',
  onSuccessDelayMs = 800,
  ...props
}) {
  const [state, setState] = useState('idle')

  const handleClick = async (event) => {
    if (disabled || state !== 'idle') return
    event?.preventDefault()
    setState('loading')
    try {
      await onClick?.(event)
      setState('success')
      setTimeout(() => {
        setState('idle')
      }, onSuccessDelayMs)
    } catch (err) {
      setState('idle')
      throw err
    }
  }

  const isLoading = state === 'loading'
  const isSuccess = state === 'success'

  return (
    <button
      type={type}
      className={className}
      onClick={handleClick}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? <Loader2 size={14} className="animate-spin" /> : isSuccess ? <CheckCircle2 size={14} /> : null}
      {isLoading ? `${loadingLabel}` : isSuccess ? `${successLabel}` : children}
    </button>
  )
}
