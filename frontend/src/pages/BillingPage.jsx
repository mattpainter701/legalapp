import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getBillingStatus, createCheckoutSession, createPortalSession } from '../api'
import { useAuth } from '../App'

function TierBadge({ tier }) {
  const isFlat = tier === 'flat'
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
        isFlat ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'
      }`}
    >
      {isFlat ? 'Flat-seat subscription' : 'Pay-as-you-go'}
    </span>
  )
}

export default function BillingPage({ embedded = false }) {
  useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(null)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)

  useEffect(() => {
    if (searchParams.get('success') === '1') {
      setSuccessMsg('Subscription activated! Your billing tier will update momentarily via webhook.')
    }
    getBillingStatus()
      .then(setStatus)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load billing info'))
      .finally(() => setLoading(false))
  }, [])

  const handleUpgrade = async () => {
    setActionLoading('checkout')
    setError(null)
    try {
      const { checkout_url } = await createCheckoutSession()
      window.location.href = checkout_url
    } catch (e) {
      setError(e?.response?.data?.detail || 'Stripe checkout unavailable')
      setActionLoading(null)
    }
  }

  const handlePortal = async () => {
    setActionLoading('portal')
    setError(null)
    try {
      const { portal_url } = await createPortalSession()
      window.location.href = portal_url
    } catch (e) {
      setError(e?.response?.data?.detail || 'Stripe portal unavailable')
      setActionLoading(null)
    }
  }

  if (loading) {
    return (
      <div className={`flex items-center justify-center bg-brand-bg ${embedded ? 'py-16' : 'h-screen'}`}>
        <div className="w-6 h-6 border-2 border-brand-accent border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="">
      <div className={`${embedded ? 'max-w-3xl' : 'max-w-2xl mx-auto px-4 py-12'}`}>
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-brand-ink font-serif">Subscription Billing</h1>
            <p className="text-sm text-brand-muted mt-1 font-sans">
              Manage the firm's LawHand subscription and payment method.
            </p>
          </div>
          {!embedded && (
            <button
              onClick={() => navigate(-1)}
              className="text-sm text-brand-muted hover:text-brand-ink font-sans"
            >
              ← Back
            </button>
          )}
        </div>

        {successMsg && (
          <div className="mb-6 bg-green-50 border border-green-200 rounded-lg px-4 py-3 text-sm text-green-700 font-sans">
            {successMsg}
          </div>
        )}

        {error && (
          <div className="mb-6 bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-sm text-brand-rose font-sans">
            {error}
          </div>
        )}

        {/* Current plan card */}
        <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6 mb-6">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs text-brand-muted uppercase tracking-wider font-sans mb-1">
                Current Plan
              </p>
              <div className="flex items-center gap-3">
                <span className="text-xl font-bold text-brand-ink font-serif capitalize">
                  {status?.billing_tier ?? '—'}
                </span>
                {status?.billing_tier && <TierBadge tier={status.billing_tier} />}
              </div>
              {status?.flat_seat_count > 0 && (
                <p className="text-sm text-brand-muted mt-1 font-sans">
                  {status.flat_seat_count} seat{status.flat_seat_count !== 1 ? 's' : ''}
                </p>
              )}
            </div>
          </div>

          {status?.billing_tier === 'payg' && (
            <div className="mt-4 pt-4 border-t border-brand-line">
              <p className="text-sm text-brand-ink-2 font-sans mb-3">
                On pay-as-you-go, usage is billed at a 10× markup on model cost.
                Upgrade to a flat-seat plan for predictable monthly pricing and
                significantly lower per-query costs.
              </p>
              <button
                onClick={handleUpgrade}
                disabled={actionLoading === 'checkout'}
                className="inline-flex items-center gap-2 bg-brand-accent text-white px-4 py-2 rounded-lg text-sm font-medium font-sans hover:bg-brand-accent-2 disabled:opacity-60 transition-colors"
              >
                {actionLoading === 'checkout' ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Redirecting to Stripe…
                  </>
                ) : (
                  'Upgrade to Flat-seat Plan'
                )}
              </button>
            </div>
          )}
        </div>

        {status?.mcp_usage && (
          <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6 mb-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-brand-ink font-sans mb-1">
                  MCP usage
                </p>
                <p className="text-sm text-brand-muted font-sans">
                  Product-key calls are metered separately from subscription seats and model usage.
                </p>
              </div>
              <span className="inline-flex items-center rounded-full bg-yellow-100 px-2.5 py-0.5 text-xs font-medium text-yellow-800">
                PAYG line
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2">
                <p className="text-xs uppercase tracking-wider text-brand-muted font-sans">30-day calls</p>
                <p className="mt-1 text-xl font-bold text-brand-ink font-serif">
                  {(status.mcp_usage.calls_30d || 0).toLocaleString()}
                </p>
              </div>
              <div className="rounded-lg border border-brand-line bg-brand-bg px-3 py-2">
                <p className="text-xs uppercase tracking-wider text-brand-muted font-sans">Returned results</p>
                <p className="mt-1 text-xl font-bold text-brand-ink font-serif">
                  {(status.mcp_usage.results_30d || 0).toLocaleString()}
                </p>
              </div>
            </div>
            <p className="mt-3 text-xs text-brand-muted font-mono">
              {status.mcp_usage.line_item || 'MCP usage'} / {status.mcp_usage.meter || 'mcp_product_key_calls'}
            </p>
          </div>
        )}

        {/* Manage subscription */}
        {status?.stripe_customer_id && (
          <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6 mb-6">
            <p className="text-sm font-semibold text-brand-ink font-sans mb-1">
              Manage Subscription
            </p>
            <p className="text-sm text-brand-muted font-sans mb-4">
              Update your payment method, download invoices, or cancel via the
              Stripe customer portal.
            </p>
            <button
              onClick={handlePortal}
              disabled={actionLoading === 'portal'}
              className="inline-flex items-center gap-2 border border-brand-accent text-brand-accent px-4 py-2 rounded-lg text-sm font-medium font-sans hover:bg-brand-accent hover:text-white disabled:opacity-60 transition-colors"
            >
              {actionLoading === 'portal' ? (
                <>
                  <span className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  Opening portal…
                </>
              ) : (
                'Open Billing Portal →'
              )}
            </button>
          </div>
        )}

        {/* Pricing reference */}
        <div className="bg-brand-surface rounded-xl border border-brand-line shadow-sm p-6">
          <p className="text-sm font-semibold text-brand-ink font-sans mb-3">Pricing reference</p>
          <div className="overflow-x-auto">
          <table className="w-full min-w-[400px] text-sm font-sans">
            <thead>
              <tr className="text-xs text-brand-muted uppercase">
                <th className="text-left pb-2">Model</th>
                <th className="text-right pb-2">PAYG (10×)</th>
                <th className="text-right pb-2">Flat-seat</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-brand-line">
              <tr className="py-2">
                <td className="py-2 text-brand-ink-2">Standard</td>
                <td className="py-2 text-right text-brand-muted">$2.70 / 1M input</td>
                <td className="py-2 text-right text-brand-muted">$0.27 / 1M input</td>
              </tr>
              <tr>
                <td className="py-2 text-brand-ink-2">Premium</td>
                <td className="py-2 text-right text-brand-muted">$30 / 1M input</td>
                <td className="py-2 text-right text-brand-muted">$3 / 1M input</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
      </div>
    </div>
  )
}
