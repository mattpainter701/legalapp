import { useEffect } from 'react'

const CONTROL_SELECTOR = 'input:not([type="hidden"]), select, textarea'
let associationCounter = 0

export function associateUnboundFormLabels(root = document) {
  root.querySelectorAll('label:not([for])').forEach((label) => {
    if (label.control) return
    const sibling = label.nextElementSibling?.matches?.(CONTROL_SELECTOR) ? label.nextElementSibling : null
    const candidates = sibling ? [sibling] : [...(label.parentElement?.querySelectorAll(CONTROL_SELECTOR) || [])]
    if (candidates.length !== 1) return
    const control = candidates[0]
    if (!control.id) {
      associationCounter += 1
      control.id = `associated-field-${associationCounter}`
    }
    label.htmlFor = control.id
  })
}

export default function FormLabelAssociator() {
  useEffect(() => {
    let queued = false
    let active = true
    const associate = () => {
      queued = false
      if (!active || typeof document === 'undefined') return
      associateUnboundFormLabels(document)
    }
    associate()
    const observer = new MutationObserver(() => {
      if (queued) return
      queued = true
      queueMicrotask(associate)
    })
    observer.observe(document.body, { childList: true, subtree: true })
    return () => {
      active = false
      observer.disconnect()
    }
  }, [])
  return null
}
