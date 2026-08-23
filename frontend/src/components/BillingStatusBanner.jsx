import { AlertTriangle } from 'lucide-react'
import { Link } from 'react-router-dom'

// Stripe marks a tenant past_due or suspended, and before this banner existed
// nothing in the product said so — the firm's first signal was features
// quietly not working. Rendered for every user so somebody notices; the fix
// link only resolves for the finance roles that can open the billing page.
const STATES = {
  past_due: {
    tone: 'amber',
    title: 'Payment problem on this firm’s subscription',
    body: 'The most recent payment did not go through. Update the payment method to avoid losing access.',
  },
  suspended: {
    tone: 'rose',
    title: 'This firm’s subscription is suspended',
    body: 'Billing is no longer active. Some features may stop working until the subscription is restored.',
  },
  unpaid: {
    tone: 'rose',
    title: 'This firm’s subscription is unpaid',
    body: 'Stripe reports the subscription as unpaid. Update the payment method to restore full access.',
  },
}

const TONES = {
  amber: 'border-amber-300 bg-amber-50 text-amber-950',
  rose: 'border-brand-rose/40 bg-brand-rose/10 text-brand-ink',
}

export function resolveBillingState(user) {
  if (!user || user.demo) return null
  const candidates = [user.billing_status, user.subscription_status]
  for (const value of candidates) {
    const key = String(value || '').toLowerCase()
    if (STATES[key]) return { key, ...STATES[key] }
  }
  return null
}

export default function BillingStatusBanner({ user, canManageBilling = false }) {
  const state = resolveBillingState(user)
  if (!state) return null

  return (
    <div
      role="status"
      aria-label="Subscription billing status"
      className={`flex flex-wrap items-center justify-center gap-x-2 gap-y-1 border-b px-4 py-2 text-center text-xs font-semibold md:text-sm ${TONES[state.tone]}`}
    >
      <AlertTriangle size={15} aria-hidden="true" className="shrink-0" />
      <span>{state.title} — {state.body}</span>
      {canManageBilling ? (
        <Link to="/admin?tab=billing" className="underline underline-offset-2 hover:no-underline">
          Update payment method
        </Link>
      ) : (
        <span className="font-normal opacity-90">
          Ask a firm administrator to update the payment method.
        </span>
      )}
    </div>
  )
}
